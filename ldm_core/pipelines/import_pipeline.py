"""
Orchestrates the main 'ldm import' pipeline.
"""

import contextlib
import os
import shutil
import tarfile
import time
import typing
import zipfile
from datetime import datetime
from pathlib import Path

from ldm_core.pipelines.base import Pipeline, PipelineContext, PipelineStage
from ldm_core.pipelines.validation import ValidationStage as SharedValidationStage
from ldm_core.ui import UI
from ldm_core.utils import calculate_sha256


class ImportPipelineContext(PipelineContext):
    """Strongly typed context for the Import pipeline."""

    def __init__(self, manager, **kwargs):
        super().__init__(**kwargs)
        self.manager = manager
        self.set("total_start", time.time())
        self.set("is_brand_new", False)
        self.set("project_name", kwargs.get("project_name"))
        self.set("source_path", kwargs.get("source_path"))
        self.set("temp_dirs", [])
        self.set("project_path", None)
        self.set("paths", {})
        self.set("backup_dir", None)
        self.set("is_init_from", kwargs.get("is_init_from", False))
        self.set("no_run", kwargs.get("no_run"))


class ImportValidationStage(PipelineStage):
    """Verifies target paths, existing states, and CLI flags."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager
        source_path = context.get("source_path")

        # We assume source_path is a local file or directory after download/clone logic
        source = Path(source_path).resolve()

        if not source.exists():
            UI.die(f"Source path not found: {source}")
        if not manager._check_java_version("21"):
            UI.die("Incorrect system Java version. LDM import requires JDK 21.")

        if source.is_file():
            if source.suffix.lower() not in [".zip", ".tgz", ".gz", ".tar", ".ldmp"]:
                UI.die(f"Unsupported source format: {source.suffix}")

            verify_enabled = getattr(manager.args, "verify", True)
            sha_file = source.with_name(f"{source.name}.sha256")

            if verify_enabled:
                if sha_file.exists():
                    UI.detail(f"Verifying integrity of {source.name}...")
                    actual_sha = calculate_sha256(source)
                    expected_sha = sha_file.read_text().strip()
                    if actual_sha != expected_sha:
                        UI.die(
                            f"Integrity check failed for archive: {source.name}\n"
                            f"Expected: {expected_sha}\n"
                            f"Actual:   {actual_sha}\n"
                            "The archive file may be corrupted or tampered with."
                        )
                    UI.success("Archive integrity verified.")
                else:
                    UI.warning(
                        "Archive does not have an integrity checksum. Proceeding without verification."
                    )
            else:
                UI.warning("Integrity verification disabled via --no-verify.")

        context.set("source_resolved", source)


class ExtractionStage(PipelineStage):
    """Handles zip/tar expansion and prepares the payload."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        source = context.get("source_resolved")

        if source.is_file():
            temp_extract_dir = (
                Path.cwd()
                / ".ldm_temp"
                / f"import_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            temp_extract_dir.mkdir(parents=True, exist_ok=True)
            temp_dirs = context.get("temp_dirs", [])
            temp_dirs.append(temp_extract_dir)
            context.set("temp_dirs", temp_dirs)

            UI.detail("Extracting source archive...")
            from ldm_core.utils import safe_extract

            if source.suffix.lower() == ".zip":
                with zipfile.ZipFile(source, "r") as z:
                    safe_extract(z, temp_extract_dir)
            else:
                mode: typing.Literal["r:", "r:gz"] = (
                    "r:gz"
                    if source.suffix.lower() in [".tgz", ".gz", ".ldmp"]
                    else "r:"
                )
                with tarfile.open(source, mode) as t:
                    safe_extract(t, temp_extract_dir)

            extracted_source = temp_extract_dir
            for r, _d, f in os.walk(temp_extract_dir):
                if (
                    Path(r) / "liferay" / "LCP.json"
                ).exists() or "gradle.properties" in f:
                    extracted_source = Path(r)
                    break

            context.set("extracted_source", extracted_source)
            context.set("backup_dir", temp_extract_dir)
        else:
            context.set("extracted_source", source)
            extracted_source = source

        self._resolve_layout(context, extracted_source)

    @staticmethod
    def _resolve_layout(context: PipelineContext, extracted_source: Path) -> None:
        """Record where the Liferay Workspace sits inside the source tree.

        LDM-#1681. An LCP (Liferay Cloud) repository nests the Gradle
        workspace under `liferay/`; its standalone service directories are
        siblings of that folder, children of the repository root. A plain
        Liferay Workspace is its own root and has neither.

        Before PR #497 this was one line -- `source / "liferay" if
        (source / "liferay").exists() else source` -- and every consumer read
        it. The refactor dropped it and passed the source root around as
        `extracted_source` instead, so for a cloud workspace the import has
        been reading `configs/`, `client-extensions/`, `modules/` and
        `gradlew` from the repository root, where none of them exist.

        Three keys, deliberately separate, because two of them differ only
        for cloud sources and conflating them is what broke:

        * `workspace_root` -- the Gradle workspace. Everything that syncs
          code reads from here.
        * `cloud_root` -- the LCP repository root, or `None`. The only thing
          that reads it is the standalone-service scan, which must look one
          level above the workspace.
        * `is_cloud` -- detection, via the real `is_lcp_workspace`. The
          `hasattr`-guarded `manager.workspace._is_lcp_workspace` it replaces
          named a method that has never existed, so this was permanently
          `False`.
        """
        from ldm_core.utils import is_lcp_workspace

        nested = extracted_source / "liferay"
        if nested.is_dir() and is_lcp_workspace(extracted_source):
            # `ldm import <lcp-repo>` -- the usual shape.
            context.set("workspace_root", nested)
            context.set("cloud_root", extracted_source)
            context.set("is_cloud", True)
        elif is_lcp_workspace(extracted_source):
            # `ldm import <lcp-repo>/liferay` -- the workspace named directly.
            context.set("workspace_root", extracted_source)
            context.set("cloud_root", extracted_source.parent)
            context.set("is_cloud", True)
        else:
            context.set("workspace_root", extracted_source)
            context.set("cloud_root", None)
            context.set("is_cloud", False)

    def rollback(self, context: PipelineContext) -> None:
        """Discard the extraction directory this stage created.

        LDM-#1630: the temp-directory cleanup used to live only on the former
        BackupStateStage (removed in LDM-#1635), which ran *after*
        ProjectSetupStage -- so the
        refusal that actually leaks (`Unsupported database type ...`, raised in
        ProjectSetupStage) happened before the stage that owns the cleanup had
        ever executed, and rollback therefore had nothing registered to undo.
        The stage that creates the scratch directory is the one that must be
        able to remove it. `parse_package_manifest` still discards it by hand
        before its own `UI.die` calls, which is now belt-and-braces rather than
        the only cleanup there is.

        Only directories this stage created are touched: a directory *source*
        registers no temp_dirs at all, so the user's own workspace is never a
        candidate here.
        """
        for d in context.get("temp_dirs", []):
            if isinstance(d, Path) and d.exists():
                UI.detail(f"Cleaning up temporary directory: {d}")
                shutil.rmtree(d, ignore_errors=True)


def _note_unverifiable_origin(manifest) -> None:
    """Report, and never refuse on, a package origin nothing can confirm.

    LDM-#1621: `_verify_ldm_package_manifest` treats a missing
    `github_repository`, or one naming a repo other than the release it was
    fetched from, as a Security Violation. Neither refusal transfers here. A
    file the user chose themselves has no fetch origin for the declaration to
    contradict, so there is nothing to compare against -- the check is not
    relaxed so much as inapplicable.

    Both shapes import successfully today (measured: a manifest with no
    `github_repository`, and one naming an unrelated repo, each produced a
    working project), and hand-built packages and packages moved between
    machines are the normal case for this input. So the declared-origin case is
    a detail line rather than a warning, to avoid teaching users to ignore a
    warning that fires on every legitimate local import; the absent-origin case
    is a warning, because that is the one the release path calls a violation.
    """
    declared = manifest.get("github_repository")
    if declared:
        UI.detail(
            f"Package declares origin '{declared}'. It was not fetched from "
            "that repository's releases, so the origin is unverified."
        )
    else:
        UI.warning(
            "Package manifest declares no 'github_repository', so its origin "
            "cannot be established."
        )


class PackageVerificationStage(PipelineStage):
    """Manifest controls for a local or downloaded .ldmp (LDM-#1621).

    Until this stage existed, `_verify_ldm_package_manifest` was reachable from
    exactly one input -- a GitHub repo URL whose latest release carries a
    `.ldmp` -- so `ldm import ./thing.ldmp`, and the `.ldmp` URL that downloads
    and re-enters cmd_import with a local path, were verified by nothing.

    Not every control transfers, and the ones that do not are listed here
    rather than silently dropped:

    * The parse is applied, and refuses. Measured before this stage existed: a
      package whose manifest had trailing lines after the closing brace
      imported and reported success, with `tag` and `db_type` silently absent
      from the project it produced -- so the database dump was restored into
      whichever engine the defaults picked. That import was already broken; it
      just did not say so.
    * The listing reconciliation is applied, and warns rather than refuses.
      See reconcile_package_listings.
    * `db_type` is left to ProjectSetupStage, which already enforces it on this
      path (measured: exit 1 on an unknown engine, before this change) and also
      covers the `.ldmrc` manifest of a directory source, which this stage does
      not see. It was only ever vacuous because the manifest could arrive as
      {}, and the strict parse above is what fixes that.
    * The origin checks cannot be applied at all. See
      _note_unverifiable_origin.

    Creates no filesystem state, so it owns no rollback (LDM-#1643). Everything
    here is read-only: `parse_package_manifest` parses, `reconcile_package_listings`
    mutates the manifest dict *in memory*, and `_note_unverifiable_origin` warns.
    The scratch directories it is handed belong to ExtractionStage, which
    created them and cleans them up. LDM-#1643 listed this stage as a gap; that
    was wrong, and it was removed from the ratchet's allowlist rather than
    given a rollback that would have had nothing to undo.
    """

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        backup_dir = context.get("backup_dir")

        # No manifest means this is not a package. A .zip/.tgz/.tar workspace
        # archive legitimately has none, and its absence is exactly what
        # ProjectSetupStage uses to tell the two apart, so refusing here would
        # reject every workspace archive rather than every broken package.
        if not backup_dir or not (Path(backup_dir) / "meta").exists():
            return

        from ldm_core.workspace.importer import (
            parse_package_manifest,
            reconcile_package_listings,
        )

        scratch = [d for d in context.get("temp_dirs", []) if isinstance(d, Path)]
        manifest = parse_package_manifest(context.manager, Path(backup_dir), *scratch)
        reconcile_package_listings(Path(backup_dir), manifest, *scratch, refuse=False)
        _note_unverifiable_origin(manifest)


class ProjectSetupStage(PipelineStage):
    """Sets up the project directory and meta configuration."""

    def execute(self, context: PipelineContext) -> None:  # noqa: C901, PLR0912, PLR0915
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager
        backup_dir = context.get("backup_dir")

        is_ldmp = backup_dir and (backup_dir / "meta").exists()
        manifest = manager.read_meta(backup_dir) or {} if is_ldmp else {}
        context.set("is_ldmp", is_ldmp)

        if not is_ldmp:
            source_dir = context.get("source_resolved")
            if source_dir and source_dir.is_dir():
                ldmrc_path = source_dir / ".ldmrc"
                if ldmrc_path.exists():
                    from ldm_core.utils import load_global_config_safe

                    config_data = load_global_config_safe(ldmrc_path)
                    manifest = (
                        config_data.get("defaults", {})
                        if "defaults" in config_data
                        else config_data
                    )

        from ldm_core.utils import DB_ENGINES

        db_type = manifest.get("db_type")
        if db_type and db_type not in DB_ENGINES:
            UI.die(f"Unsupported database type '{db_type}' in LDM package manifest.")

        project_name = getattr(manager.args, "project", None) or getattr(
            manager.args, "project_flag", None
        )
        if not project_name:
            project_name = (
                context.get("source_resolved").stem
                if context.get("source_resolved").is_file()
                else context.get("source_resolved").name
            )
            if manager.non_interactive:
                UI.detail(f"Using default project name: {project_name}")
            else:
                project_name = UI.ask("Project Name", project_name)

        context.set("project_name", project_name)
        project_path = manager.detect_project_path(project_name, for_init=True)
        context.set("project_path", project_path)

        manager.check_uncommitted_changes(project_path)

        is_brand_new = not project_path.exists()
        context.set("is_brand_new", is_brand_new)

        if not is_brand_new:
            if manager.non_interactive:
                UI.detail(
                    f"Project '{project_name}' exists. Overwriting in non-interactive mode."
                )
            else:
                ans = UI.ask(
                    f"Project '{project_name}' exists. Overwrite? [y]es, [n]o (skip existing), [c]lean, [q]uit",
                    "Y",
                ).upper()
                if ans == "C":
                    UI.detail(f"Cleaning existing project directory: {project_path}")
                    manager.safe_rmtree(project_path)
                    context.set("is_brand_new", True)
                elif ans == "N":
                    context.set("overwrite", False)
                    UI.detail("Proceeding in 'skip existing' mode.")
                elif ans == "Y":
                    context.set("overwrite", True)
                else:
                    UI.die("Initialization aborted.")

        paths = manager.setup_paths(project_path)
        context.set("paths", paths)
        for p in [v for v in paths.values() if isinstance(v, Path) and not v.suffix]:
            p.mkdir(parents=True, exist_ok=True)

        manager.verify_runtime_environment(paths)

        project_meta = manager.read_meta(project_path) or {}
        if manifest.get("tag"):
            project_meta["tag"] = manifest["tag"]
        if manifest.get("db_type"):
            project_meta["db_type"] = manifest["db_type"]

        self._carry_compatibility_claim(manifest, project_meta)

        if not is_ldmp:
            self._apply_workspace_product(context, project_meta)

        from ldm_core.utils import sanitize_id

        safe_container_name = sanitize_id(project_name)
        if safe_container_name != project_name:
            UI.detail(
                f"Project name '{project_name}' contains invalid characters for Docker. "
                f"Using '{safe_container_name}' for container names."
            )

        final_host_name = (
            getattr(manager.args, "host_name", None)
            or manifest.get("host_name")
            or project_meta.get("host_name")
            or "localhost"
        )
        ssl_arg = getattr(manager.args, "ssl", None)
        if ssl_arg is not None:
            final_ssl = str(ssl_arg).lower()
        elif getattr(manager.args, "host_name", None) is not None:
            final_ssl = str(final_host_name != "localhost").lower()
        else:
            manifest_ssl = manifest.get("ssl")
            if manifest_ssl is not None:
                final_ssl = str(manifest_ssl).lower()
            else:
                final_ssl = str(project_meta.get("ssl") or "false").lower()

        project_meta.update(
            {
                "project_name": project_name,
                "container_name": safe_container_name,
                "port": str(
                    getattr(manager.args, "port", None)
                    or project_meta.get("port")
                    or 8080
                ),
                "ssl": final_ssl,
                "host_name": final_host_name,
                "last_run": datetime.now().isoformat(),
            }
        )

        # LDM-#1695: dropped with the rest of the literal in PR #497. Only
        # `_handle_dry_run` has written it since, so a real imported project
        # records nothing about which LDM created it.
        from ldm_core.constants import VERSION

        project_meta["ldm_version"] = VERSION

        self._record_linked_workspace(context, project_meta)
        self._resolve_cloud_project_id(context, project_meta)

        manager.write_meta(project_path, project_meta)

    @staticmethod
    def _carry_compatibility_claim(manifest: dict, project_meta: dict) -> None:
        """Carry the package's compatibility claim into the project (LDM-#1791).

        `tag` says how the package was **built**; this says what it has been
        **tested** on. `ldm run` answers the question at the moment it resolves
        a tag, and it can only do that if the claim survives the import.

        A copy, deliberately, and never a fill-in. Every package published
        before LDM-#1791 has no such key, and that absence *is* the default --
        "no claim". Manufacturing one here would turn every existing package
        into a claimant overnight, which is LDM-#1782's mistake (silence read
        as success) rebuilt with more ceremony.
        """
        from ldm_core.compatibility import CLAIM_KEY

        claim = (manifest or {}).get(CLAIM_KEY)
        if claim:
            project_meta[CLAIM_KEY] = claim

    @staticmethod
    def _apply_workspace_product(context: PipelineContext, project_meta: dict) -> None:
        """Take the project's tag from the workspace's own pin (LDM-#1693).

        PR #497 rebuilt the `project_meta` literal without the
        `liferay.workspace.product` read, so an imported or linked workspace
        has recorded no `tag` at all since 2026-07-10. `_resolve_tag` then
        falls through to discovery, and the project boots a line the workspace
        was not built for.

        That also made LDM-#1658 noisy: with nothing recorded, discovery and
        the pin disagree on *every* run, so the mismatch warning fired
        constantly instead of only when the user had chosen a different tag.
        Measured before this change -- a workspace pinned to `dxp-2026.q3.0`
        warned on every `ldm run`, resolving `2026.q1.12-lts`.

        Two deliberate differences from the line being restored:

        * **an explicit `-t` now wins.** The original comment read "Workspace
          product always wins" and it overrode the flag. LDM-#1658 settled the
          opposite principle for the same collision -- an explicit tag is a
          decision, not an accident -- and the two must agree or the warning
          contradicts the behaviour.
        * **`manager.args` is not mutated.** The original assigned
          `self.manager.args.tag`; `cmd_import` has since moved deliberately
          away from that ("Resolve parameters to avoid mutating manager.args
          directly"). Writing `project_meta` is sufficient: the post-import
          `cmd_run` re-reads the meta this stage writes.

        The seeded start the original also drove from here needs nothing: the
        run pipeline calls `_ensure_seeded` itself (`pipelines/run.py:1264`)
        with whatever tag it resolves, so recording the right tag restores the
        right seed as a consequence.
        """
        import re

        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        if getattr(manager.args, "tag", None):
            return

        workspace_root = context.get("workspace_root") or context.get(
            "extracted_source"
        )
        if not isinstance(workspace_root, Path):
            return

        gradle_props = workspace_root / "gradle.properties"
        try:
            if not gradle_props.is_file():
                return
            content = gradle_props.read_text(encoding="utf-8")
        except OSError:
            return

        # Anchored to the line start so a commented-out pin is not read as one.
        match = re.search(
            r"^[ \t]*liferay\.workspace\.product[ \t]*=[ \t]*(\S+)[ \t]*$",
            content,
            re.MULTILINE,
        )
        if not match:
            return

        raw_product = match.group(1)
        from ldm_core.utils import resolve_liferay_docker_tag

        resolved_tag, is_portal = resolve_liferay_docker_tag(raw_product, manager)
        if resolved_tag:
            project_meta["tag"] = resolved_tag
            project_meta["portal"] = "true" if is_portal else "false"
        else:
            # Offline, or a product key releases.json does not carry. Stripping
            # the prefix is what the original did and is better than recording
            # nothing -- `2026.q1.7` is still the right image, just unconfirmed.
            project_meta["tag"] = re.sub(r"^(dxp|portal)-", "", raw_product)

        # LDM-#1790: UI.info, not UI.detail. `detail` prints only under
        # `--info`/`--verbose` (LDM-#1036), so in a default non-interactive CI
        # run this line -- the one that says which Liferay line is about to be
        # booted, and why -- was invisible.
        #
        # That is not hypothetical. On 2026-09-17 LDM-#1693 changed the
        # accelerator's E2E from the tag discovery returned (2026.q1.12-lts) to
        # the tag its workspace pins (2026.q1.7-lts). The boot failed, and
        # reconstructing WHY took a binary-hash comparison, two local scaffolds
        # and three Liferay boots -- because the one line that explained it was
        # emitted at a verbosity nobody runs CI at (LDM-#1782).
        #
        # A pin is a statement of the version that has been TESTED, so a
        # consumer may be running an older line entirely deliberately. Anything
        # that changes which line boots is therefore a decision they need to
        # see, not a detail.
        UI.info(
            f"Using tag {project_meta['tag']}, from the workspace pin "
            f"{raw_product} -- not from tag discovery."
        )

    @staticmethod
    def _record_linked_workspace(context: PipelineContext, project_meta: dict) -> None:
        """Remember the workspace a linked project was created from (LDM-#1684).

        PR #497 rebuilt the `project_meta` literal without
        `"workspace_path": str(source) if is_init_from else None`, and nothing
        has written the key since. Two readers depend on it:

        * `workspace/monitor.py:39` -- `ldm monitor <project>` with no path
          falls back to it and otherwise refuses. `ldm link` itself still
          worked, because `cmd_link` passes the source to `cmd_monitor`
          directly; it is re-attaching the watcher later that failed, which is
          why this stayed quiet.
        * `handlers/snapshot.py:422` -- reads it to record the workspace's git
          origin in a package, and silently skips that when absent.

        Two deliberate differences from the line being restored:

        * **absent is not the same as `None`.** The original wrote `None` for a
          plain import, which would erase a previously linked project's path on
          any re-import. The key is now left alone instead.
        * **the path must be a directory that still exists.** `ldm clone`
          imports from a scratch git checkout that is deleted immediately
          afterwards, so recording it would store a path that is already gone.
          It reaches `cmd_import` with `is_init_from=False` and is excluded by
          that flag anyway; this is the belt to that pair of braces.
        """
        context = typing.cast(ImportPipelineContext, context)

        if not context.get("is_init_from"):
            return

        source = context.get("source_resolved")
        if not isinstance(source, Path) or not source.is_dir():
            return

        project_meta["workspace_path"] = str(source)
        UI.detail(f"Linked to workspace: {source}")

    @staticmethod
    def _resolve_cloud_project_id(context: PipelineContext, project_meta: dict) -> None:
        """Record the Liferay Cloud project ID for a cloud source (LDM-#1681).

        `--cloud-project` is declared on `import`, `init-from`, `link` and
        `clone`, and PR #497 left nothing reading it. Nothing wrote
        `cloud_project_id` either, so `handlers/cloud.py` fell through its
        chain to the project directory name and ran `lcp` commands against a
        guess -- silently, because that fallback has no warning.

        Resolution order, restored from the pre-refactor implementation:

        1. `--cloud-project`, an explicit decision by the user;
        2. the `id` in the repository-root `LCP.json` / `lcp.json`;
        3. interactively, a prompt defaulting to the repository directory
           name; non-interactively, a refusal.

        The refusal is exit code `2` as it was before, not `1`: an unusable
        cloud identity is the same class of problem as a missing LCP login,
        and automation already branches on it that way.
        """
        import json

        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        if not context.get("is_cloud"):
            return

        cli_cloud_id = getattr(manager.args, "cloud_project", None)
        if cli_cloud_id:
            project_meta["cloud_project_id"] = cli_cloud_id
            UI.detail(f"Using Liferay Cloud project ID: {cli_cloud_id}")
            return

        cloud_root = context.get("cloud_root")
        if cloud_root:
            for candidate in ("lcp.json", "LCP.json"):
                lcp_path = cloud_root / candidate
                if not lcp_path.exists():
                    continue
                try:
                    root_lcp = json.loads(lcp_path.read_text(encoding="utf-8"))
                except Exception as e:  # A malformed file is reported, not fatal
                    UI.warning(f"Failed to parse {lcp_path.name}: {e}")
                    break
                if isinstance(root_lcp, dict) and root_lcp.get("id"):
                    project_meta["cloud_project_id"] = root_lcp["id"]
                    UI.detail(
                        f"Detected Liferay Cloud project ID: {root_lcp['id']} "
                        f"(from {lcp_path.name})"
                    )
                    return
                break

        # Re-importing over a project that already carries one is not a
        # prompt, and must not be a refusal.
        if project_meta.get("cloud_project_id"):
            return

        if manager.non_interactive:
            UI.die(
                "Liferay Cloud project ID could not be determined. "
                "Please specify it using --cloud-project.",
                exit_code=2,
            )

        UI.detail(
            "Liferay Cloud project ID could not be determined from a root LCP.json."
        )
        default_id = (cloud_root or context.get("workspace_root")).name
        project_meta["cloud_project_id"] = UI.ask(
            "Liferay Cloud Project ID", default_id
        )

    def rollback(self, context: PipelineContext) -> None:
        """Remove the project directory this stage brought into existence.

        LDM-#1635: this lived on `BackupStateStage`, whose `execute` was an
        empty `pass` -- the stage existed only to host cleanup for state that
        other stages created. That is the arrangement LDM-#1630 identified as
        the structural cause of the scratch-directory leak, and `base.py`
        already states the rule it broke: undo what THIS stage created, on this
        stage. `project_path` and `is_brand_new` are both set here, so this is
        where the undo belongs.

        Behaviour is unchanged by the move, which is why it is safe. For a
        failure in any *later* stage both arrangements delete the project, and
        for a failure inside this stage neither does -- a raising stage is
        never appended to `executed_stages`, so its own rollback does not run
        (measured; see test_stage_owns_its_rollback.py). The `db_type` refusal
        that LDM-#1630 was reported against raises before this stage's `mkdir`,
        so there is no directory to leak at that point either way.
        """
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        # LDM-#1630: FinalizationStage runs `ldm run` against the finished
        # project, prompts and all. Now that a `UI.die` or a Ctrl-C anywhere in
        # that nested pipeline reaches this rollback, an aborted post-import
        # start-up would otherwise delete the project the import had already
        # completed -- destroying a successful import over a failure that
        # happened after it. `import_committed` is the commit point, mirroring
        # `init_success` in pipelines/run.py.
        if context.get("import_committed"):
            UI.detail(
                "The project was already imported; leaving it in place. "
                "Start it with 'ldm run'."
            )
            return

        is_brand_new = context.get("is_brand_new")
        project_path = context.get("project_path")
        if is_brand_new and project_path and project_path.exists():
            UI.detail(f"Removing newly created project directory: {project_path}")
            manager.safe_rmtree(project_path)


class DatabaseRestoreStage(PipelineStage):
    """Restores database from SQL dumps using snapshot manager."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        # If it's an LDMP package, cmd_restore handles DB and Volumes
        if context.get("is_ldmp"):
            UI.detail("Restoring database and volume assets from LDM package...")
            try:
                manager.snapshot.cmd_restore(
                    context.get("project_name"), backup_dir=context.get("backup_dir")
                )
            except Exception as e:
                UI.error(f"Failed to restore snapshot: {e}")
                context.stopped = True
                raise


class VolumeSyncStage(PipelineStage):
    """Synchronizes files and artifacts from the source workspace to the project paths.

    Rollback restores the artifact directories to the state they were found in
    (LDM-#1677).

    The leak this closes had one trigger: importing into a project directory
    that ALREADY EXISTED. Everything written here lands inside the project, and
    `ProjectSetupStage.rollback` removes that directory -- but only when
    `is_brand_new`. For an existing project it correctly declines to delete a
    directory it did not create, so a failure in a later stage used to leave
    the copied client extensions, fragments and services behind.

    **Why a snapshot rather than a journal of individual writes.** The obvious
    fix -- record each destination as it is written, undo them in reverse --
    cannot work here, because the writes are not all in this method. Much of
    the copying happens inside `workspace._hydrate_from_workspace`, which calls
    four further sync helpers in `workspace/hydration.py`. A journal threaded
    through this stage alone would cover roughly half the writes and leave the
    directory *not* as it was found, while looking like it had succeeded. That
    is the "declare a leak fixed while it still leaks" failure the rollback
    ratchet exists to prevent.

    So the whole target set is copied aside up front and restored wholesale.
    That covers writes made by code this stage never sees.

    Cost is paid only where the leak exists: `is_brand_new` projects are
    skipped entirely, because `ProjectSetupStage.rollback` already deletes the
    whole directory for them. The snapshot covers artifact directories only --
    `deploy`, `files`, `scripts`, `osgi/configs`, `osgi/modules`,
    `osgi/client-extensions`, `client-extensions` and `services` -- and never
    `root/data`, where document-library content lives.

    The saved copy is registered in `temp_dirs`, so a successful import
    discards it in `FinalizationStage` like any other scratch directory.
    """

    def execute(self, context: PipelineContext) -> None:  # noqa: C901, PLR0912, PLR0915
        import os
        import shutil
        import typing
        import zipfile
        from pathlib import Path

        from ldm_core.pipelines.import_pipeline import ImportPipelineContext
        from ldm_core.ui import UI
        from ldm_core.utils import safe_copy

        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        if context.get("is_ldmp"):
            return  # Handled by restore

        # LDM-#1681: the Gradle workspace, which is `<repo>/liferay` for a
        # cloud source and the source root otherwise. `extracted_source` is
        # the latter in both cases, and reading code out of it is why a cloud
        # import copied nothing. Resolved once in `ExtractionStage`.
        workspace_root = context.get("workspace_root") or context.get(
            "extracted_source"
        )
        paths = context.get("paths")
        overwrite = context.get("overwrite", True)

        # LDM-#1677: capture the artifact directories before anything writes to
        # them, so rollback can restore rather than merely delete. Must happen
        # before the first write below AND before _hydrate_from_workspace at the
        # end, whose writes this stage cannot otherwise see.
        self._snapshot_targets(context, paths)

        # LDM-#1681: this read `manager.workspace._is_lcp_workspace(...)` behind
        # a `hasattr` guard. That method has never been defined at any commit,
        # so `is_cloud` was permanently False and the service-copy block below
        # had not run since 2026-07-10. Detection now happens once, in
        # `ExtractionStage`, through the real `is_lcp_workspace`.
        is_cloud = context.get("is_cloud", False)

        def import_zips(search_base, label, target_dir, overwrite=False):
            count = 0
            if not search_base.exists():
                return count

            for zip_path in search_base.glob("**/*.zip"):
                if "-sources" in zip_path.name or "javadoc" in zip_path.name:
                    continue

                UI.debug(f"Found {label} ZIP: {zip_path.name}")
                with zipfile.ZipFile(zip_path, "r") as z:
                    if z.testzip() is not None:
                        UI.error(f"{zip_path.name} corrupt.")
                        continue
                target_file = target_dir / zip_path.name
                if target_file.exists() and not overwrite:
                    UI.debug(f"Skipping existing {label}: {zip_path.name}")
                    continue

                safe_copy(zip_path, target_file)
                count += 1
            return count

        # Sync code elements directly
        # Standard structural folders
        UI.detail("Syncing workspace structure and files...")
        # LDM-#1692: `configs` is deliberately NOT here. It is not a flat
        # mirror like the other three -- a workspace keeps it as
        # `configs/<environment>/...`, so copying it wholesale into
        # `osgi/configs/` produced `osgi/configs/local/osgi/configs/x.config`,
        # three directories below the `osgi/configs/*.config` that Liferay
        # scans. Workspace OSGi configuration was therefore never applied, and
        # nothing said so. Handled selectively by `_sync_environment_configs`
        # below, which also honours `--target-env`.
        structural_mappings = {
            "deploy": paths.get("deploy"),
            "files": paths.get("files"),
            "scripts": paths.get("scripts"),
        }

        for source_folder, target_dir in structural_mappings.items():
            if not target_dir:
                continue
            src = workspace_root / source_folder
            if src.exists():
                for item in src.iterdir():
                    dest = target_dir / item.name
                    if item.is_dir():
                        if dest.exists() and overwrite:
                            shutil.rmtree(dest)
                        if not dest.exists():
                            shutil.copytree(item, dest, copy_function=safe_copy)
                    elif not dest.exists() or overwrite:
                        safe_copy(item, dest)

        self._sync_environment_configs(context, workspace_root, paths)

        # Handle CEs and Fragments
        import_zips(
            workspace_root / "client-extensions",
            "Extension",
            paths.get("ce_dir"),
            overwrite,
        )
        import_zips(
            workspace_root / "fragments", "Fragment", paths.get("ce_dir"), overwrite
        )

        # Modules and Themes
        for search_folder in ["modules", "themes"]:
            base = workspace_root / search_folder
            if base.exists():
                for root, dirs, _files in os.walk(base):
                    if "build" in dirs:
                        libs = Path(root) / "build" / "libs"
                        if libs.exists():
                            for f in libs.glob("*.[jw]ar"):
                                if not any(
                                    x in f.name.lower()
                                    for x in ["-sources", "-javadoc", "-tests"]
                                ):
                                    safe_copy(f, paths.get("modules") / f.name)

        cloud_root = context.get("cloud_root")
        if is_cloud and cloud_root and cloud_root.is_dir():
            # LDM-#1681: this walked `workspace_root.parent`. Correct before
            # PR #497, when `workspace_root` was `<repo>/liferay` -- but the
            # refactor made it the repository root, so the walk moved one
            # level ABOVE the repository. Nobody noticed because `is_cloud`
            # was never True. `cloud_root` names the repository root outright
            # rather than deriving it, so the two cannot drift apart again.
            infra_dirs = [
                "liferay",
                "backup",
                "ci",
                "database",
                "search",
                "webserver",
                ".git",
            ]
            copied = 0
            for item in [
                i
                for i in cloud_root.iterdir()
                if i.is_dir()
                and i.name not in infra_dirs
                and not i.name.startswith(".")
            ]:
                if (item / "LCP.json").exists() and (item / "Dockerfile").exists():
                    dest = paths.get("root") / "services" / item.name
                    if dest.exists():
                        manager.safe_rmtree(dest)
                    shutil.copytree(item, dest, copy_function=safe_copy)
                    copied += 1
            if copied:
                UI.success(f"Imported {copied} standalone Liferay Cloud service(s).")

        # LDM-#1679: a call to `manager.snapshot._restore_from_cloud_layout`
        # stood here, guarded by `hasattr`. The method has never existed on the
        # snapshot service -- no commit in the repository's history defines it
        # there -- and every definition that ever existed anywhere was an
        # ellipsis stub on LiferayManager, returning None. So the branch could
        # not execute, and would have done nothing if it had. Removed rather
        # than repointed: there is no implementation to point it at.
        #
        # It was not harmless. LDM-#1677 and the VolumeSyncStage docstring
        # added in LDM-#1643 both cited it as an external write a rollback
        # could not reach -- one of two stated reasons for believing the
        # directory could not be restored. `hasattr` around a call makes a
        # permanently-false branch look like a legitimate conditional, which is
        # how it survived three refactors.
        if hasattr(manager.workspace, "_hydrate_from_workspace"):
            manager.workspace._hydrate_from_workspace(
                workspace_root, paths, overwrite=overwrite
            )

    @staticmethod
    def _sync_environment_configs(context, workspace_root, paths) -> None:
        """Apply `configs/<target_env>/` the way Liferay actually reads it (LDM-#1692).

        A Liferay Workspace keeps environment configuration as
        `configs/<environment>/`, holding `portal-ext.properties`, an
        `osgi/configs/` tree and optionally `deploy/`. PR #497 replaced the
        selective copy that understood that shape with a wholesale directory
        mapping, which put every file one environment-directory too deep:

            configs/local/osgi/configs/x.config
              -> osgi/configs/local/osgi/configs/x.config   (never scanned)
            configs/local/portal-ext.properties
              -> osgi/configs/local/portal-ext.properties   (never read)

        Measured before the fix: `ls <project>/osgi/configs/*.config` matched
        nothing, and a marker property placed in the workspace's
        `portal-ext.properties` appeared nowhere the portal would look.

        Three differences from the pre-refactor code it restores:

        * **properties are merged, not copied over.** The original did
          `safe_copy(pe, paths["files"] / "portal-ext.properties")`, which
          would flatten LDM's own generated file -- the JDBC URL, the host
          name, the feature flags. `update_portal_ext` merges key by key, and
          LDM's later writes still win on the keys it owns.
        * **`--target-env` is honoured again.** The wholesale copy took every
          environment, so a project built for `local` also carried `uat`.
        * **absent is not an error.** A workspace with no `configs/` at all is
          normal and must stay silent.
        """
        manager = typing.cast(ImportPipelineContext, context).manager

        target_env = getattr(manager.args, "target_env", None) or "local"
        config_src = workspace_root / "configs" / target_env
        if not config_src.is_dir():
            return

        from ldm_core.utils import safe_copy

        pe_file = config_src / "portal-ext.properties"
        if pe_file.is_file():
            try:
                props = manager.config._get_properties(
                    pe_file.read_text(encoding="utf-8")
                )
            except OSError as e:
                UI.warning(f"Could not read {pe_file}: {e}")
                props = {}
            if props:
                manager.config.update_portal_ext(paths, props)
                UI.success(
                    f"Applied {len(props)} propert"
                    f"{'y' if len(props) == 1 else 'ies'} from "
                    f"configs/{target_env}/portal-ext.properties."
                )

        osgi_src = config_src / "osgi" / "configs"
        if osgi_src.is_dir() and paths.get("configs"):
            target = paths["configs"]
            target.mkdir(parents=True, exist_ok=True)
            count = 0
            # Flattened by basename: Liferay scans `osgi/configs/*.config`, not
            # a tree beneath it.
            for entry in sorted(osgi_src.glob("*.config")) + sorted(
                osgi_src.glob("*.cfg")
            ):
                safe_copy(entry, target / entry.name)
                count += 1
            if count:
                UI.success(
                    f"Imported {count} OSGi config file(s) from configs/{target_env}."
                )

        deploy_src = config_src / "deploy"
        if deploy_src.is_dir() and paths.get("deploy"):
            paths["deploy"].mkdir(parents=True, exist_ok=True)
            for entry in deploy_src.iterdir():
                if entry.is_file():
                    safe_copy(entry, paths["deploy"] / entry.name)

    # Artifact directories this stage and its callees write into. `root/data`
    # is deliberately absent: document-library content lives there, it is not
    # written by this stage, and copying it would make every import into an
    # existing project pay for a snapshot of the whole document library.
    _SNAPSHOT_KEYS = (
        "deploy",
        "files",
        "scripts",
        "configs",
        "modules",
        "cx",
        "ce_dir",
    )

    def _snapshot_targets(self, context, paths) -> None:
        """Copy the artifact directories aside so rollback can restore them."""
        import shutil
        from datetime import datetime
        from pathlib import Path

        if not paths:
            return

        # A brand-new project needs no snapshot: ProjectSetupStage.rollback
        # deletes the entire directory it created, which subsumes anything
        # written here. Paying for a copy there would be pure cost.
        if context.get("is_brand_new"):
            return

        scratch = (
            Path.cwd()
            / ".ldm_temp"
            / f"volsync_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        )

        saved: dict = {}
        for key in self._SNAPSHOT_KEYS:
            target = paths.get(key)
            if not target:
                continue
            if target.exists():
                dest = scratch / key
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(target, dest)
                saved[key] = dest
            else:
                # Recorded as absent, so rollback removes it rather than
                # leaving a directory the import brought into existence.
                saved[key] = None

        services = paths.get("root") / "services" if paths.get("root") else None
        if services is not None:
            if services.exists():
                dest = scratch / "__services__"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(services, dest)
                saved["__services__"] = dest
            else:
                saved["__services__"] = None

        if not saved:
            return

        context.set("volume_sync_saved", saved)
        # Registered so a SUCCESSFUL import discards it in FinalizationStage,
        # like every other scratch directory.
        temp_dirs = context.get("temp_dirs", [])
        temp_dirs.append(scratch)
        context.set("temp_dirs", temp_dirs)

    def rollback(self, context: PipelineContext) -> None:
        """Restore the artifact directories to their pre-import state.

        LDM-#1677. Only reachable for a failure in a LATER stage -- a stage that
        raises is never appended to `executed_stages`, so this does not run for
        a failure part-way through this stage's own `execute` (measured in
        test_stage_owns_its_rollback.py).
        """
        import shutil
        import typing

        context = typing.cast(ImportPipelineContext, context)

        # Mirrors ProjectSetupStage: past the commit point the import has
        # happened and nothing below may undo it (LDM-#1630).
        if context.get("import_committed"):
            return

        saved = context.get("volume_sync_saved")
        if not saved:
            return

        paths = context.get("paths")
        if not paths:
            return

        manager = context.manager
        UI.detail("Restoring project artifact directories to their previous state...")

        for key, backup in saved.items():
            target = (
                paths.get("root") / "services"
                if key == "__services__"
                else paths.get(key)
            )
            if not target:
                continue
            if target.exists():
                manager.safe_rmtree(target)
            if backup is not None and backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(backup, target)


class BuildWorkspaceStage(PipelineStage):
    """Builds the Gradle workspace if requested."""

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        if not context.get("is_ldmp") and getattr(manager.args, "build", False):
            # LDM-#1681: `gradlew` lives in the Gradle workspace, which is
            # `<repo>/liferay` for a cloud source -- not the repository root.
            workspace_root = context.get("workspace_root") or context.get(
                "extracted_source"
            )
            UI.heading(f"Building Workspace: {workspace_root.name}")
            import platform

            gradlew = workspace_root / (
                "gradlew" if platform.system() != "Windows" else "gradlew.bat"
            )
            if gradlew.exists():
                # LDM-#1695: `_check_gradle_java_version` (handlers/base.py:845)
                # was called from nowhere after PR #497 -- the import checked
                # the *system* Java in `ImportValidationStage` but never the
                # JVM Gradle would actually use. This is the one place that is
                # about to run `gradlew`, so it is where the check belongs.
                # Advisory: a mismatch is reported, not fatal, because the
                # build below already handles its own failure.
                with contextlib.suppress(Exception):
                    manager._check_gradle_java_version(gradlew)
                if platform.system() != "Windows":
                    try:
                        os.chmod(gradlew, 0o755)  # nosec B103
                    except Exception:
                        pass
                try:
                    UI.detail(f"Executing clean build in {gradlew.parent}...")
                    manager.run_command(
                        [str(gradlew), "clean", "build", "-x", "test"],
                        capture_output=False,
                        cwd=str(gradlew.parent),
                    )
                except Exception as e:
                    UI.error(f"Build failed: {e}")
                    if manager.non_interactive:
                        UI.die("Build failed in non-interactive mode. Aborting.")


class FinalizationStage(PipelineStage):
    """Handles post-import cleanup and starts the stack if needed.

    **Must not define a rollback** (LDM-#1643), and this is a correctness
    constraint rather than an omission.

    `import_committed` is set here, before `cmd_run`, and it is the commit
    point: `ProjectSetupStage.rollback` checks it and declines to delete the
    project once it is set. A rollback on this stage would run *inside* that
    committed region and undo an import that has already succeeded -- exactly
    the failure LDM-#1630 introduced the commit point to prevent, where an
    aborted post-import `ldm run` destroyed the finished project.

    The state `cmd_run` creates is not this stage's to undo either. It belongs
    to the run pipeline, whose own stages own their own rollbacks.

    `test_stage_owns_its_rollback.py` asserts this stage defines no rollback,
    so a future attempt to "finish" LDM-#1643 by adding one fails loudly.
    """

    def execute(self, context: PipelineContext) -> None:
        context = typing.cast(ImportPipelineContext, context)
        manager = context.manager

        temp_dirs = context.get("temp_dirs", [])
        for d in temp_dirs:
            if isinstance(d, Path) and d.exists():
                shutil.rmtree(d, ignore_errors=True)

        project_path = context.get("project_path")
        UI.success(f"Project created/imported at: {project_path}")

        # LDM-#1630: past this line the import has happened, and nothing below
        # is allowed to undo it. `cmd_run` re-enters the whole run pipeline,
        # which prompts and refuses via `UI.die` in several places; without
        # this the SystemExit that a refusal or a Ctrl-C raises there would
        # reach ProjectSetupStage.rollback and delete the finished project.
        context.set("import_committed", True)

        no_run = context.get("no_run")
        if no_run is None:
            no_run = getattr(manager.args, "no_run", False)

        if not no_run:
            manager.runtime.cmd_run(
                project_id=context.get("project_name"), is_restart=True
            )


class ImportPipeline(Pipeline):
    """The complete pipeline for 'ldm import'."""

    def __init__(self):
        super().__init__(
            name="import",
            stages=[
                SharedValidationStage(),
                ImportValidationStage(),
                ExtractionStage(),
                PackageVerificationStage(),
                ProjectSetupStage(),
                DatabaseRestoreStage(),
                VolumeSyncStage(),
                BuildWorkspaceStage(),
                FinalizationStage(),
            ],
        )
