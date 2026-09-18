import os
import tarfile
import time
from datetime import datetime
from pathlib import Path

from ldm_core.handlers.base import BaseHandler
from ldm_core.snapshot.archive import ArchiveSnapshotService
from ldm_core.snapshot.custom_containers import CustomContainersSnapshotService
from ldm_core.snapshot.database import DatabaseSnapshotService
from ldm_core.snapshot.search import SearchSnapshotService
from ldm_core.snapshot.utils import UtilsSnapshotService
from ldm_core.snapshot.volumes import VolumesSnapshotService
from ldm_core.ui import UI
from ldm_core.utils import get_actual_home

# LDM-#1773. The search topology is a property of the machine LDM is running
# on, not of the package -- the same class of thing as `jdbc.default.url` and
# `virtual.hosts.valid.hosts`, which `restore` already regenerates. These two
# prefixes cover every key LDM itself emits for search:
#
#   composer._build_liferay_service   -> LIFERAY_ELASTICSEARCH_PERIOD_*
#   composer._inject_liferay_search_env -> LIFERAY_ELASTICSEARCH{7,8}_PERIOD_*
#   composer's sidecar branch         -> module.framework.properties....
#                                        ElasticsearchConfiguration.*
LDM_SEARCH_ENV_PREFIX = "LIFERAY_ELASTICSEARCH"
LDM_SEARCH_PROPERTY_PREFIX = (
    "module.framework.properties.com.liferay.portal.search.elasticsearch"
)


def strip_ldm_search_env(custom_env):
    """Removes LDM's own search environment from a restored `custom_env`.

    Returns `(kept, removed)`, both dicts.

    A snapshot's `custom_env` is captured from the **rendered compose file** of
    the publisher's machine (`snapshot/archive.py`), so these entries are not a
    publisher's declaration at all -- they are LDM's own output from a
    different machine, travelling back in. `composer._build_liferay_service`
    appends `custom_env` *after* the shared-search block, and Compose resolves
    a duplicated key to the later entry, so the restored copy silently wins:

        LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=false   # shared mode
        ...
        LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=true    # from custom_env
        LIFERAY_ELASTICSEARCH_PERIOD_OPERATION_PERIOD_MODE=EMBEDDED

    A project that resolved to shared search then starts an embedded
    Elasticsearch **inside** the Liferay container as well, whose sidecar JVM
    defaults to `-Xmx2g` -- enough to OOM an 8 GB machine sized for a 3 GB
    ceiling.

    Unconditional rather than "only when it contradicts the resolved mode":
    LDM re-emits the correct values for whatever mode this machine resolves, so
    an entry that agrees is redundant and one that disagrees is harmful. There
    is no third case, and comparing values would add a way to be subtly wrong
    about which is which.
    """
    if not isinstance(custom_env, dict):
        return custom_env, {}

    kept, removed = {}, {}
    for key, value in custom_env.items():
        if str(key).upper().startswith(LDM_SEARCH_ENV_PREFIX):
            removed[key] = value
        else:
            kept[key] = value
    return kept, removed


def strip_ldm_search_properties(text):
    """Removes LDM's own search properties from a restored portal-ext text.

    Returns `(text, removed)`.

    Layer 2 of `_resolve_properties_cascade`. A package built on a `--sidecar`
    machine carries `...ElasticsearchConfiguration.operationMode=EMBEDDED` and
    that machine's sidecar ports, and `module.framework.properties.*` is
    deliberately the highest-precedence route into that configuration --
    `composer.py`'s own comment says so. Nothing rewrote it on a shared-search
    import, because `operationMode` is written only in the sidecar branch.

    Operates on the text rather than a parsed dict so that everything else in
    the file -- comments, ordering, blank lines, the publisher's own
    properties -- survives the copy unchanged.
    """
    kept_lines, removed = [], {}
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and "=" in stripped:
            # A commented-out setting keeps its leading `#` or `!` in the key
            # read here, so it never matches the prefix and survives the copy.
            # That is deliberate -- it is inert, and deleting it would destroy
            # a note the publisher left on purpose. An explicit comment guard
            # was dropped from this loop because it could not change any
            # outcome, and a branch no input can reach is not a safeguard.
            key = stripped.split("=", 1)[0].strip()
            if key.startswith(LDM_SEARCH_PROPERTY_PREFIX):
                removed[key] = stripped.split("=", 1)[1].strip()
                continue
        kept_lines.append(line)
    return "".join(kept_lines), removed


def announce_stripped_search_keys(removed, source, search_mode):
    """Says what was discarded and where it came from. LDM-#1773.

    Silent stripping was rejected deliberately: a publisher who set
    `operationMode` on purpose would otherwise get no signal at all, and the
    announcement is what makes this recoverable rather than mysterious.
    """
    if not removed:
        return

    UI.warning(
        f"Discarded {len(removed)} search setting(s) from the package's "
        f"{source}; this machine resolved search_mode '{search_mode}'."
    )
    for key in sorted(removed):
        UI.detail(f"  - {key}={removed[key]}")
    UI.detail(
        "Search topology belongs to the machine LDM runs on, not to the "
        "package -- the same as the JDBC URL and virtual host, which are also "
        "regenerated on import. Left in place, these start an embedded "
        "Elasticsearch alongside the shared one (LDM-#1773)."
    )


class SnapshotService(BaseHandler):
    def __init__(self, manager):
        super().__init__(manager.args)
        self.manager = manager
        self.database = DatabaseSnapshotService(self)
        self.search = SearchSnapshotService(self)
        self.volumes = VolumesSnapshotService(self)
        self.custom_containers = CustomContainersSnapshotService(self)
        self.archive = ArchiveSnapshotService(self)
        self.utils = UtilsSnapshotService(self)

    def _install_restored_portal_ext(self, paths, project_meta):
        """Copies the restored portal-ext into the cascade, minus LDM's own
        search settings. LDM-#1773.

        The extracted file lands in BOTH ends of the properties cascade: it is
        layer 5 (project customisations) where it sits, and it is copied to
        `ldmp-portal-ext.properties` as layer 2. Stripping only the layer-2
        copy would achieve nothing -- the key would stop matching the baseline
        and be promoted to a layer-5 customisation, which outranks every other
        layer. So the strip happens once, on the file itself, and the copy is
        taken from the cleaned text.

        Written rather than `copy2`'d for that reason; the mtime is not worth
        shipping a second copy of the setting to preserve.
        """
        target_pe = paths["files"] / "portal-ext.properties"
        if not target_pe.exists():
            return

        ldm_dir = paths["root"] / ".liferay-docker"
        ldm_dir.mkdir(parents=True, exist_ok=True)

        text = target_pe.read_text(encoding="utf-8")
        text, removed = strip_ldm_search_properties(text)
        if removed:
            target_pe.write_text(text, encoding="utf-8")
        (ldm_dir / "ldmp-portal-ext.properties").write_text(text, encoding="utf-8")

        announce_stripped_search_keys(
            removed,
            "portal-ext.properties",
            self._resolved_search_mode(project_meta),
        )

    def _clean_restored_custom_env(self, custom_env, project_meta):
        """Removes LDM's own search environment from a restored `custom_env`
        and returns what is left. LDM-#1773.

        `composer._build_liferay_service` appends `custom_env` *after* the
        shared-search block, and Compose resolves a duplicated key to the later
        entry -- so the publisher's machine wins over the decision
        `resolve_infrastructure_mode` just made on this one.
        """
        custom_env, removed = strip_ldm_search_env(custom_env)
        announce_stripped_search_keys(
            removed,
            "captured environment",
            self._resolved_search_mode(project_meta),
        )
        return custom_env

    def _resolved_search_mode(self, project_meta):
        """The search mode THIS machine resolved, for the announcement.

        Reported rather than acted on: the strip is unconditional, because LDM
        re-emits the correct values for whichever mode this is. Naming it tells
        the operator what the discarded settings were measured against.
        """
        try:
            from ldm_core.utils import resolve_infrastructure_mode

            return resolve_infrastructure_mode(
                "search_mode", project_meta or {}, self.manager.defaults
            )
        except Exception:
            return "unknown"

    def cmd_snapshots(self, paths=None):
        """Lists snapshots for a project."""
        if not paths:
            root = self.manager.detect_project_path()
            if not root:
                return None
            paths = self.manager.setup_paths(root)

        backups_dir = paths["backups"]
        if not backups_dir.exists():
            UI.detail("No snapshots found.")
            return []

        backups = sorted(
            [d for d in backups_dir.iterdir() if d.is_dir()],
            key=lambda x: x.name,
            reverse=True,
        )

        if not backups:
            UI.detail("No snapshots found.")
            return []

        # Ensure paths is a dictionary for subscripting
        if not isinstance(paths, dict):
            paths = self.manager.setup_paths(paths)

        UI.heading(f"Snapshots for {paths['root'].name}")
        for i, b in enumerate(backups):
            meta = self.manager.read_meta(b / "meta") or {}
            name = meta.get("name", "Untitled")
            timestamp = b.name
            size = self.utils._get_dir_size(b)

            inc_db = meta.get("includes_database") in [True, "true"]
            inc_vol = meta.get("includes_volume_assets") in [True, "true"]
            inc_cx = meta.get("includes_client_extensions") in [True, "true"]
            inc_modules = meta.get("includes_osgi_modules") in [True, "true"]

            parts = []
            if inc_db:
                parts.append("DB")
            if inc_vol:
                parts.append("VOL")
            if inc_cx:
                parts.append("CX")
            if inc_modules:
                parts.append("MOD")

            inc_str = f" [{','.join(parts)}]" if parts else ""
            print(
                f"[{i + 1}] {UI.CYAN}{timestamp}{UI.COLOR_OFF} - {UI.BOLD}{name}{UI.COLOR_OFF} ({size}){UI.DIM}{inc_str}{UI.COLOR_OFF}"
            )

        return backups

    def cmd_snapshot(self, project_id=None, name=None):  # noqa: PLR0912
        """Creates or manages snapshots of the project state."""
        is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
        if is_dry_run:
            UI.detail(
                f"{UI.BYELLOW}[DRY RUN] Would create or manage snapshots for project: {project_id}{UI.COLOR_OFF}"
            )
            return
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        paths = self.manager.setup_paths(root)
        project_meta = self.manager.read_meta(root)

        self.manager.verify_runtime_environment(paths)

        delete_arg = getattr(self.manager.args, "delete", None)
        keep_last = getattr(self.manager.args, "keep_last", None)
        older_than = getattr(self.manager.args, "older_than", None)
        if name is None:
            name = getattr(self.manager.args, "name", None)

        if delete_arg or keep_last is not None or older_than is not None:
            self.utils._manage_snapshots(paths, delete_arg, keep_last, older_than)
            if not name and not self.manager.non_interactive and not delete_arg:
                return
            if delete_arg:
                return

        if not name:
            if self.manager.non_interactive:
                name = f"Auto-snapshot {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            else:
                name = UI.ask("Snapshot Name", "Manual Snapshot")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snap_dir = paths["backups"] / timestamp
        from ldm_core.utils import safe_mkdir

        safe_mkdir(snap_dir, parents=True, exist_ok=True)
        UI.detail(f"Creating snapshot: {name}...")

        from ldm_core.utils import sanitize_id

        container_name = sanitize_id(
            project_meta.get("liferay_container_name")
            or project_meta.get("container_name")
            or root.name
        )

        search_snapshot_name = self.search._snapshot_search(
            project_meta, root, timestamp, container_name
        )
        db_snapshot_file = self.database._snapshot_database(
            project_meta, container_name, snap_dir, paths
        )

        if search_snapshot_name:
            if self.search._wait_for_search_snapshot(search_snapshot_name):
                UI.success("Search snapshot completed.")
                try:
                    es_backup_source = (
                        get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                    )
                    if es_backup_source.exists():
                        snap_es_dir = paths["backups"] / timestamp / "search"
                        from ldm_core.utils import safe_mkdir

                        safe_mkdir(snap_es_dir, parents=True, exist_ok=True)
                except Exception as e:
                    UI.warning(f"Could not copy search snapshots: {e}")
            else:
                UI.warning(
                    "Search snapshot failed or timed out. Project snapshot will proceed without it."
                )
                search_snapshot_name = None

        self.custom_containers._snapshot_custom_containers(project_meta, snap_dir)

        self.volumes._dehydrate_named_volumes(paths)

        files_tar = self.archive._create_archive(paths, snap_dir, search_snapshot_name)

        self.archive._generate_snapshot_metadata(
            name,
            timestamp,
            project_meta,
            root,
            paths,
            snap_dir,
            db_snapshot_file,
            search_snapshot_name,
        )

        from ldm_core.utils import calculate_sha256

        if files_tar.exists() and getattr(self.manager.args, "verify", True):
            sha = calculate_sha256(files_tar)
            (snap_dir / "files.tar.gz.sha256").write_text(sha)
        elif files_tar.exists():
            UI.warning("Integrity checksum generation skipped via --no-verify.")

        UI.success(f"Snapshot saved: {snap_dir}")

    def cmd_restore(  # noqa: C901, PLR0912, PLR0915
        self, project_id=None, auto_index=None, backup_dir=None, no_run=None
    ):
        is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
        if is_dry_run:
            UI.detail(
                f"{UI.BYELLOW}[DRY RUN] Would restore snapshot for project: {project_id}{UI.COLOR_OFF}"
            )
            return
        root_path = self.manager.detect_project_path(project_id, for_init=True)
        if not root_path:
            return
        self.manager.check_uncommitted_changes(root_path)
        project_id = root_path.name
        paths = self.manager.setup_paths(root_path)
        project_meta = self.manager.read_meta(paths["root"]) or {}

        if getattr(self.manager.args, "list", False):
            self.cmd_snapshots(paths)
            return

        choice_path = self.utils._resolve_snapshot_choice(paths, auto_index, backup_dir)
        if not choice_path:
            return

        if not (paths["root"] / "docker-compose.yml").exists():
            UI.detail("Scaffolding Docker environment for restore...")
            self.manager.runtime.cmd_run(
                project_id=project_meta.get("container_name") or paths["root"].name,
                no_up=True,
                show_summary=False,
                paths=paths,
                project_meta=project_meta,
            )

        from ldm_core.utils import sanitize_id

        container_name = sanitize_id(
            project_meta.get("liferay_container_name")
            or project_meta.get("container_name")
            or paths["root"].name
        )
        target_name = getattr(self.manager, "target", None) or project_meta.get(
            "target"
        )
        from ldm_core.docker_service import DockerService

        docker_prefix = DockerService.get_docker_cmd_prefix(target_name)

        if (
            self.manager.run_command(
                [*docker_prefix, "ps", "-q", "-f", f"name=^{container_name}$"],
                check=False,
            )
            or (paths["root"] / "docker-compose.yml").exists()
        ):
            self.manager.runtime.cmd_reset(project_id=paths["root"].name, target="all")
            time.sleep(2)

        files_tar = choice_path / "files.tar.gz"
        volume_tgz = choice_path / "volume.tgz"

        if files_tar.exists():
            sha_file = choice_path / "files.tar.gz.sha256"
            verify_enabled = getattr(self.manager.args, "verify", True)

            if verify_enabled:
                if sha_file.exists():
                    UI.phase(1, 4, "Analyzing Snapshot Integrity")
                    UI.detail("Verifying snapshot integrity...")
                    from ldm_core.utils import calculate_sha256

                    actual_sha = calculate_sha256(files_tar)
                    expected_sha = sha_file.read_text().strip()
                    if actual_sha != expected_sha:
                        UI.die(
                            f"Integrity check failed for snapshot: {choice_path.name}\n"
                            f"Expected: {expected_sha}\n"
                            f"Actual:   {actual_sha}\n"
                            f"The snapshot file may be corrupted or tampered with."
                        )
                    UI.success("Snapshot integrity verified.")
                else:
                    UI.warning(
                        "Snapshot does not have an integrity checksum. Verification skipped."
                    )
                    UI.interruptible_pause(3, "Press CTRL+C to cancel ")
            else:
                UI.warning("Integrity verification disabled via --no-verify.")
                UI.interruptible_pause(3, "Press CTRL+C to cancel ")

            UI.phase(2, 4, "Restoring Workspace Data")
            self.archive._extract_snapshot_archive(files_tar, paths)

            if "files" in paths:
                self._install_restored_portal_ext(paths, project_meta)

            if (choice_path / ".ldm").exists():
                import shutil

                shutil.copytree(
                    choice_path / ".ldm", paths["root"] / ".ldm", dirs_exist_ok=True
                )
        elif volume_tgz.exists() or (choice_path / "volume").is_dir():
            self.volumes._restore_cloud_volume(paths, choice_path, project_meta)
        else:
            UI.die(f"Snapshot files not found in {choice_path}")

        UI.phase(3, 4, "Finalizing Metadata")
        snap_meta = self.manager.read_meta(choice_path / "meta")

        custom_env = snap_meta.get("custom_env")
        if custom_env:
            project_meta["custom_env"] = self._clean_restored_custom_env(
                custom_env, project_meta
            )
            self.manager.write_meta(paths["root"], project_meta)

        snap_tag = snap_meta.get("tag")
        if snap_tag:
            project_meta["tag"] = snap_tag
            project_meta["last_run_liferay_version"] = snap_tag
            self.manager.write_meta(paths["root"], project_meta)
            self.manager.runtime.cmd_run(
                project_id=project_meta.get("container_name") or paths["root"].name,
                no_up=True,
                show_summary=False,
                is_restore=True,
                paths=paths,
                project_meta=project_meta,
            )

        UI.phase(4, 4, "Restoring Database Components")
        self.database._restore_database(
            paths, choice_path, project_meta, container_name
        )

        self.search._restore_search(choice_path, snap_meta, container_name)

        self.custom_containers._restore_custom_images(choice_path, project_meta)

        UI.success("Restore complete.")

        if self.flag_reindex(paths["root"]):
            UI.detail("  + Scheduled automatic search reindex for next boot.")
        else:
            UI.warning("  ! Could not schedule automatic reindex (metadata missing).")

        if no_run is None:
            no_run = getattr(self.manager.args, "no_run", False)
        up_flag = getattr(self.manager.args, "up", False)

        if not no_run:
            if up_flag or (
                not self.manager.non_interactive
                and UI.confirm("Do you want to start the project now?", "Y")
            ):
                self.manager.runtime.cmd_run(project_id)
            else:
                UI.detail(
                    f"Run {UI.CYAN}ldm run {paths['root'].name}{UI.COLOR_OFF} to start the project."
                )

    def _compatibility_claim_args(self, announce=True):
        """Validate `--verified` / `--refuted` / `--evidence`; return the trio.

        LDM-#1791. The producer half of the compatibility claim: what this
        package has actually been TESTED on, as distinct from the `tag` it
        happens to have been built with.

        **Evidence is mandatory.** LDM-#1791 is explicit that if `verified` can
        be produced by something that did not actually run, it becomes noise
        within a month -- so there is no way to declare a claim here without
        pointing at what produced it, and LDM never manufactures one of its
        own. A package that declares nothing keeps the default, which is "no
        claim", which is what every package published so far already carries.

        Refusal is exit code `1`: a missing required argument is a user-input
        validation error, the same class as any other bad flag combination.

        Called from the top of `cmd_package`, deliberately. Stamping happens
        much later, after a snapshot has been taken -- refusing there would
        mean the user pays for a full snapshot before being told a flag is
        missing, which is the wrong order for an argument check.
        """
        args = self.manager.args
        verified = getattr(args, "verified", None)
        refuted = getattr(args, "refuted", None)
        evidence = getattr(args, "evidence", None)

        if not verified and not refuted:
            if evidence and announce:
                UI.warning(
                    "--evidence was given with neither --verified nor "
                    "--refuted, so there is no claim for it to support. "
                    "Ignoring it."
                )
            return None, None, None

        if not evidence:
            UI.die(
                "A compatibility claim must point at its evidence: pass "
                "--evidence <url or reference> alongside --verified/--refuted.\n"
                "  A claim LDM could mint without anything having run would be "
                "worth nothing within a month (LDM-#1791).",
                exit_code=1,
            )

        return verified, refuted, evidence

    def _record_compatibility_claim(self, meta) -> None:
        """Stamp the validated claim into the manifest about to be packaged."""
        from ldm_core import compatibility
        from ldm_core.constants import VERSION

        verified, refuted, evidence = self._compatibility_claim_args(announce=False)

        claim = compatibility.build_claim(
            verified=verified,
            refuted=refuted,
            evidence=evidence,
            ldm_version=VERSION,
        )
        if claim:
            meta[compatibility.CLAIM_KEY] = claim
            if verified:
                UI.detail(f"Package declares {verified} verified ({evidence}).")
            if refuted:
                UI.detail(
                    f"Package declares {refuted} refuted -- tried and failed "
                    f"({evidence})."
                )

    def cmd_package(  # noqa: C901, PLR0912, PLR0915
        self,
        project_id=None,
        output_dir=None,
        repo=None,
        use_latest=False,
        snapshot=None,
    ):
        """Bundles a project snapshot into a .ldmp package for GitHub release."""
        # LDM-#1791: before anything is created. A claim with no evidence is
        # refused, and paying for a full snapshot first would be the wrong
        # order for an argument check.
        self._compatibility_claim_args()

        is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
        if is_dry_run:
            UI.detail(f"[DRY RUN] Would package project: {project_id}")
            return

        root = self.manager.detect_project_path(project_id)
        if not root:
            UI.die("Failed to locate project directory.")
            return

        project_name = root.name
        paths = self.manager.setup_paths(root)

        # 1. Obtain or create the snapshot
        if snapshot:
            # Resolve specific snapshot path
            latest_snap_dir = paths["backups"] / snapshot
            if not latest_snap_dir.exists():
                # Try locating it in backups list (e.g. by name match or partial name)
                backups = self.utils._list_backups(paths)
                found = None
                for b in backups:
                    if b["name"] == snapshot or b["path"].name == snapshot:
                        found = b["path"]
                        break
                if found:
                    latest_snap_dir = found
                else:
                    UI.die(
                        f"Snapshot '{snapshot}' not found for project '{project_name}'."
                    )
                    return
        elif use_latest:
            # Locate latest snapshot
            backups = self.utils._list_backups(paths)
            if not backups:
                UI.detail("No existing snapshots found. Creating a new one...")
                self.cmd_snapshot(project_id)
                backups = self.utils._list_backups(paths)
            if not backups:
                UI.die("Failed to locate or create a project snapshot.")
                return
            latest_snap_dir = backups[-1]["path"]
        else:
            # Create a fresh snapshot
            UI.detail("Creating a fresh snapshot for the package...")
            self.cmd_snapshot(project_id)
            backups = self.utils._list_backups(paths)
            if not backups:
                UI.die("Failed to create project snapshot.")
                return
            latest_snap_dir = backups[-1]["path"]

        # 2. Resolve repository identifier
        if not repo:
            # Try to read git remote from project's linked workspace
            project_meta = self.manager.read_meta(root)
            workspace_path = project_meta.get("workspace_path")
            if workspace_path and Path(workspace_path).exists():
                try:
                    origin_url = self.manager.run_command(
                        ["git", "remote", "get-url", "origin"], cwd=workspace_path
                    ).strip()

                    parsed = self.manager.workspace._parse_github_repo(origin_url)
                    if parsed:
                        repo = f"{parsed[0]}/{parsed[1]}"
                except Exception:
                    pass

        if not repo:
            if self.manager.non_interactive:
                UI.die(
                    "GitHub repository identifier required. Use --repo <owner/repo>."
                )
            else:
                repo = UI.ask(
                    "GitHub Repository (owner/repo)",
                    "peterrichards-lr/liferay-ai-commerce-accelerator",
                )

        # 3. Write repo manifest to meta file in snapshot directory
        meta = self.manager.read_meta(latest_snap_dir)
        meta["github_repository"] = repo
        self._record_compatibility_claim(meta)
        self.manager.write_meta(latest_snap_dir, meta)

        # 4. Generate package tarball (.ldmp)
        output_path = Path(output_dir or Path.cwd()).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        package_file = output_path / f"{project_name}.ldmp"
        sha_file = output_path / f"{project_name}.ldmp.sha256"

        UI.detail(f"Generating LDM package at: {package_file}...")
        try:
            with tarfile.open(package_file, "w:gz") as tar:
                for item in latest_snap_dir.iterdir():
                    tar.add(item, arcname=item.name)
        except Exception as e:
            UI.die(f"Failed to generate package archive: {e}")

        # 5. Generate checksum
        from ldm_core.utils import calculate_sha256

        sha = calculate_sha256(package_file)
        sha_file.write_text(f"{sha}  {package_file.name}\n")

        UI.success(f"Successfully created LDM package: {package_file}")
