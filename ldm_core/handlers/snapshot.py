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

    def cmd_snapshots(self, paths=None):
        """Lists snapshots for a project."""
        if not paths:
            root = self.manager.detect_project_path()
            if not root:
                return None
            paths = self.manager.setup_paths(root)

        backups_dir = paths["backups"]
        if not backups_dir.exists():
            UI.info("No snapshots found.")
            return []

        backups = sorted(
            [d for d in backups_dir.iterdir() if d.is_dir()],
            key=lambda x: x.name,
            reverse=True,
        )

        if not backups:
            UI.info("No snapshots found.")
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
            UI.info(
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
        UI.info(f"Creating snapshot: {name}...")

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
            UI.info(
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
            UI.info("Scaffolding Docker environment for restore...")
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
        if (
            self.manager.run_command(
                ["docker", "ps", "-q", "-f", f"name=^{container_name}$"], check=False
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
                    UI.info("Verifying snapshot integrity...")
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

            self.archive._extract_snapshot_archive(files_tar, paths)

            if "files" in paths:
                target_pe = paths["files"] / "portal-ext.properties"
                if target_pe.exists():
                    ldm_dir = paths["root"] / ".liferay-docker"
                    ldm_dir.mkdir(parents=True, exist_ok=True)
                    import shutil

                    shutil.copy2(target_pe, ldm_dir / "ldmp-portal-ext.properties")

            if (choice_path / ".ldm").exists():
                import shutil

                shutil.copytree(
                    choice_path / ".ldm", paths["root"] / ".ldm", dirs_exist_ok=True
                )
        elif volume_tgz.exists() or (choice_path / "volume").is_dir():
            self.volumes._restore_cloud_volume(paths, choice_path, project_meta)
        else:
            UI.die(f"Snapshot files not found in {choice_path}")

        snap_meta = self.manager.read_meta(choice_path / "meta")

        custom_env = snap_meta.get("custom_env")
        if custom_env:
            project_meta["custom_env"] = custom_env
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

        self.database._restore_database(
            paths, choice_path, project_meta, container_name
        )

        self.search._restore_search(choice_path, snap_meta, container_name)

        self.custom_containers._restore_custom_images(choice_path)

        UI.success("Restore complete.")

        if self.flag_reindex(paths["root"]):
            UI.info("  + Scheduled automatic search reindex for next boot.")
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
                UI.info(
                    f"Run {UI.CYAN}ldm run {paths['root'].name}{UI.COLOR_OFF} to start the project."
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
        is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
        if is_dry_run:
            UI.info(f"[DRY RUN] Would package project: {project_id}")
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
                UI.info("No existing snapshots found. Creating a new one...")
                self.cmd_snapshot(project_id)
                backups = self.utils._list_backups(paths)
            if not backups:
                UI.die("Failed to locate or create a project snapshot.")
                return
            latest_snap_dir = backups[-1]["path"]
        else:
            # Create a fresh snapshot
            UI.info("Creating a fresh snapshot for the package...")
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
        self.manager.write_meta(latest_snap_dir, meta)

        # 4. Generate package tarball (.ldmp)
        output_path = Path(output_dir or Path.cwd()).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        package_file = output_path / f"{project_name}.ldmp"
        sha_file = output_path / f"{project_name}.ldmp.sha256"

        UI.info(f"Generating LDM package at: {package_file}...")
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
