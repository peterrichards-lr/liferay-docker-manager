import json
import tarfile
import time
from pathlib import Path
from datetime import datetime
from ldm_core.ui import UI
from ldm_core.handlers.base import BaseHandler
from ldm_core.constants import PROJECT_META_FILE
from ldm_core.utils import run_command, get_compose_cmd


class SnapshotHandler(BaseHandler):
    def cmd_snapshots(self, paths=None):
        """Lists snapshots for a project."""
        if not paths:
            root = self.detect_project_path()
            if not root:
                return
            paths = self.setup_paths(root)

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

        UI.heading(f"Snapshots for {paths['root'].name}")
        for i, b in enumerate(backups):
            meta = self.read_meta(b / "meta")
            name = meta.get("name", "Untitled")
            timestamp = b.name
            size = self._get_dir_size(b)
            print(
                f"[{i + 1}] {UI.CYAN}{timestamp}{UI.COLOR_OFF} - {UI.BOLD}{name}{UI.COLOR_OFF} ({size})"
            )

        return backups

    def cmd_snapshot(self, project_id=None):
        """Creates a snapshot of the project state."""
        root = self.detect_project_path(project_id)
        if not root:
            return
        paths = self.setup_paths(root)
        project_meta = self.read_meta(root / PROJECT_META_FILE)

        # Reclaim permissions on potential root-owned files before starting
        self.verify_runtime_environment(paths)

        name = getattr(self.args, "name", None)
        if not name:
            if self.non_interactive:
                name = f"Auto-snapshot {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            else:
                name = UI.ask("Snapshot Name", "Manual Snapshot")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        UI.info(f"Creating snapshot: {name}...")

        # --- SEARCH SNAPSHOT (Orchestrated) ---
        search_snapshot_name = None
        search_name = "liferay-search-global"
        container_name = project_meta.get("container_name") or root.name.replace(
            ".", "-"
        )

        # Check if project uses shared search and service is running
        if str(project_meta.get("use_shared_search", "false")).lower() == "true":
            if run_command(["docker", "ps", "-q", "-f", f"name={search_name}"]):
                search_snapshot_name = f"{container_name}_{timestamp}"
                UI.info(
                    f"Triggering orchestrated search snapshot: {search_snapshot_name}..."
                )
                run_command(
                    [
                        "docker",
                        "exec",
                        search_name,
                        "curl",
                        "-s",
                        "-X",
                        "PUT",
                        f"localhost:9200/_snapshot/liferay_backup/{search_snapshot_name}?wait_for_completion=false",
                        "-H",
                        "Content-Type: application/json",
                        "-d",
                        json.dumps({"indices": f"{container_name}-*"}),
                    ]
                )

        # --- DATABASE SNAPSHOT ---
        # We handle DB snapshots by triggering a dump inside the container if it's running
        # OR by capturing the data directory if it's stopped.
        # For now, we assume the user might have stopped it, but we'll try to find a DB container.

        # Wait for search snapshot if it was triggered
        if search_snapshot_name:
            if self._wait_for_search_snapshot(search_snapshot_name):
                UI.success("Search snapshot completed.")
                # Copy ES snapshot files to the backup dir so they are portable
                try:
                    from ldm_core.utils import get_actual_home

                    es_backup_source = (
                        get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                    )
                    if es_backup_source.exists():
                        snap_es_dir = paths["backups"] / timestamp / "search"
                        snap_es_dir.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    UI.warning(f"Could not copy search snapshots: {e}")
            else:
                UI.warning(
                    "Search snapshot failed or timed out. Project snapshot will proceed without it."
                )
                search_snapshot_name = None

        # --- ARCHIVE ---
        # Final permission sync before archiving (Fixes late-created Docker file issues)
        # We call this again to ensure even files created by search snapshot are unlocked.
        self.verify_runtime_environment(paths)

        snap_dir = paths["backups"] / timestamp
        snap_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(snap_dir / "files.tar.gz", "w:gz") as tar:
            for f in ["files", "scripts", "osgi", "data", "deploy", "routes"]:
                f_path = paths["root"] / f
                if f_path.exists():
                    try:
                        # Re-verify specific path permissions before adding
                        if self.verbose:
                            UI.info(f"Adding {f} to archive...")
                        tar.add(f_path, arcname=f)
                    except (PermissionError, OSError) as e:
                        UI.warning(f"Skipping {f} due to permission error: {e}")

            # If we have a search snapshot, bundle the global backup repo into the archive
            if search_snapshot_name:
                from ldm_core.utils import get_actual_home

                es_infra_backup = (
                    get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                )
                if es_infra_backup.exists():
                    tar.add(es_infra_backup, arcname="search_backup")

        # Capture custom environment variables from docker-compose.yml
        custom_env = []
        compose_path = paths["root"] / "docker-compose.yml"
        if compose_path.exists():
            try:
                from ldm_core.utils import yaml_to_dict

                compose_data = yaml_to_dict(compose_path.read_text())
                liferay_service = compose_data.get("services", {}).get("liferay", {})
                env_vars = liferay_service.get("environment", [])
                if isinstance(env_vars, list):
                    # Filter for LIFERAY_ variables that aren't the standard ones managed by LDM
                    # Standard ones: LIFERAY_JVM_OPTS, LIFERAY_HOME, LIFERAY_HSQL_PERIOD_ENABLED
                    standard_vars = [
                        "LIFERAY_JVM_OPTS",
                        "LIFERAY_HOME",
                        "LIFERAY_HSQL_PERIOD_ENABLED",
                    ]
                    for var in env_vars:
                        if "=" in var:
                            key = var.split("=", 1)[0]
                            if key.startswith("LIFERAY_") and key not in standard_vars:
                                custom_env.append(var)
            except Exception as e:
                UI.warning(
                    f"Could not parse docker-compose.yml for environment variables: {e}"
                )

        # Save metadata
        meta = {
            "name": name,
            "timestamp": timestamp,
            "tag": project_meta.get("tag"),
            "db_type": project_meta.get("db_type"),
            "host_name": project_meta.get("host_name"),
            "search_snapshot": search_snapshot_name,
            "custom_env": ",".join(custom_env) if custom_env else None,
        }
        self.write_meta(snap_dir / "meta", meta)

        UI.success(f"Snapshot saved: {snap_dir}")

    def cmd_restore(self, project_id=None, auto_index=None, backup_dir=None):
        root_path = self.detect_project_path(project_id, for_init=True)
        if not root_path:
            return
        paths = self.setup_paths(root_path)
        # For new projects (seeding), meta might not exist yet
        project_meta = self.read_meta(paths["root"] / PROJECT_META_FILE) or {}

        # 0. Support for --list (Non-interactive overview)
        if getattr(self.args, "list", False):
            self.cmd_snapshots(paths)
            return

        # 1. Resolve choice (direct dir, index, or interactive)
        choice = None
        if backup_dir:
            choice = Path(backup_dir)
        elif getattr(self.args, "backup_dir", None):
            choice = Path(self.args.backup_dir)

        if not choice:
            backups = self.cmd_snapshots(paths)
            if not backups:
                if auto_index is not None or backup_dir is not None:
                    # Internal call path (run --samples or --snapshot)
                    UI.warning(
                        "No snapshots available. Proceeding with vanilla startup."
                    )
                    return
                UI.die("No snapshots available.")

            if auto_index is not None:
                choice = backups[auto_index - 1]
            elif getattr(self.args, "index", None):
                choice = backups[self.args.index - 1]
            elif self.non_interactive:
                UI.die(
                    "No snapshot index specified. In non-interactive mode, use: ldm restore <pid> --index <num>"
                )
            else:
                choice = backups[int(UI.ask("Select snapshot index", "1")) - 1]

        if not choice or not choice.exists():
            UI.die(f"Snapshot directory not found: {choice}")

        # 2. Handle Cloud Layout vs Standard Layout
        if (choice / "database.gz").exists() or (choice / "volume.tgz").exists():
            if self._restore_from_cloud_layout(choice, paths, project_meta):
                UI.success("Cloud restoration successful.")
            return

        # Standard LDM Layout
        container_name = project_meta.get("container_name") or paths[
            "root"
        ].name.replace(".", "-")
        if run_command(["docker", "ps", "-q", "-f", f"name=^{container_name}$"]):
            compose_base = get_compose_cmd()
            if not compose_base:
                UI.die(
                    "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
                )
            run_command(compose_base + ["stop"], check=True, cwd=str(paths["root"]))
            time.sleep(2)

        files_tar = choice / "files.tar.gz"
        if files_tar.exists():
            self._extract_snapshot_archive(files_tar, paths)
        else:
            UI.die(f"Standard snapshot files not found in {choice}")

        # --- SEARCH RESTORE (Orchestrated) ---
        snap_meta = self.read_meta(choice / "meta")
        search_snapshot_name = snap_meta.get("search_snapshot")
        search_name = "liferay-search-global"

        # Restore custom environment variables to project metadata
        custom_env = snap_meta.get("custom_env")
        if custom_env:
            project_meta["custom_env"] = custom_env
            self.write_meta(paths["root"] / PROJECT_META_FILE, project_meta)

        if search_snapshot_name and search_snapshot_name != "None":
            if run_command(["docker", "ps", "-q", "-f", f"name={search_name}"]):
                UI.info(
                    f"Triggering orchestrated search restore: {search_snapshot_name}..."
                )

                # 1. Clear existing indices for this project
                self._delete_project_indices(container_name)

                # 2. Trigger restore
                run_command(
                    [
                        "docker",
                        "exec",
                        search_name,
                        "curl",
                        "-s",
                        "-X",
                        "POST",
                        f"localhost:9200/_snapshot/liferay_backup/{search_snapshot_name}/_restore",
                        "-H",
                        "Content-Type: application/json",
                        "-d",
                        json.dumps(
                            {
                                "indices": f"{container_name}-*",
                                "include_global_state": False,
                            }
                        ),
                    ]
                )

                if self._wait_for_search_restore(search_snapshot_name, container_name):
                    UI.success("Search restore completed.")
                else:
                    UI.warning(
                        "Search restore timed out or might be still in progress. Verify index status later."
                    )
            else:
                UI.error(
                    "Global search service not running. Could not restore search indices."
                )

        UI.success("Restore complete.")

    def _extract_snapshot_archive(self, files_tar, paths):
        """Extracts a snapshot tarball into the project root with security checks."""
        no_osgi = getattr(self.args, "no_osgi_seed", False)

        with tarfile.open(files_tar, "r:gz") as tar:
            from ldm_core.utils import is_within_root

            # 1. Extract standard project files
            target_root = paths["root"].resolve()
            members = []
            for m in tar.getmembers():
                if m.name.startswith("search_backup"):
                    continue

                # OSGi State Handling: Only extract if not opted-out
                if m.name.startswith("osgi/state") and no_osgi:
                    continue

                # Security: Validate path to prevent Zip Slip / Path Traversal
                member_path = (target_root / m.name).resolve()
                if not is_within_root(member_path, target_root):
                    UI.error(f"Security: Skipping unsafe member: {m.name}")
                    continue
                members.append(m)

            tar.extractall(path=target_root, members=members)  # nosec B202

            # 2. Extract search_backup if present
            has_search = any(
                m.name.startswith("search_backup") for m in tar.getmembers()
            )
            if has_search:
                from ldm_core.utils import get_actual_home

                es_infra_backup = (
                    get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                )
                es_infra_backup.mkdir(parents=True, exist_ok=True)
                es_infra_root = es_infra_backup.resolve()

                for m in tar.getmembers():
                    if m.name.startswith("search_backup/"):
                        # Security: Validate path
                        rel_name = m.name.replace("search_backup/", "", 1)
                        if not rel_name:
                            continue
                        member_path = (es_infra_root / rel_name).resolve()

                        if not is_within_root(member_path, es_infra_root):
                            UI.error(f"Security: Skipping unsafe ES member: {m.name}")
                            continue

                        # Temporarily adjust member name for extraction into the target dir
                        m.name = rel_name
                        tar.extract(m, path=es_infra_root)  # nosec B202

    def _wait_for_search_snapshot(self, snapshot_name, timeout=120):
        search_name = "liferay-search-global"
        start_time = time.time()
        while time.time() - start_time < timeout:
            res = run_command(
                [
                    "docker",
                    "exec",
                    search_name,
                    "curl",
                    "-s",
                    "localhost:9200/_snapshot/liferay_backup/" + snapshot_name,
                ],
                check=False,
            )
            if res and '"state":"SUCCESS"' in res:
                return True
            if res and '"state":"FAILED"' in res:
                return False
            time.sleep(5)
        return False

    def _wait_for_search_restore(self, snapshot_name, container_name, timeout=60):
        search_name = "liferay-search-global"
        start_time = time.time()
        while time.time() - start_time < timeout:
            res = run_command(
                [
                    "docker",
                    "exec",
                    search_name,
                    "curl",
                    "-s",
                    f"localhost:9200/{container_name}-*/_recovery",
                ],
                check=False,
            )
            # If no indices are currently recovering, we assume they are all restored or failed
            if res and '"stage":"DONE"' in res and '"stage":"INDEX"' not in res:
                return True
            time.sleep(5)
        return True

    def _delete_project_indices(self, container_name):
        search_name = "liferay-search-global"
        run_command(
            [
                "docker",
                "exec",
                search_name,
                "curl",
                "-s",
                "-X",
                "DELETE",
                f"localhost:9200/{container_name}-*",
            ],
            check=False,
        )

    def _get_dir_size(self, path):
        total = 0
        try:
            for f in path.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
        except Exception:
            return "unknown"

        for unit in ["B", "KB", "MB", "GB"]:
            if total < 1024:
                return f"{total:.1f} {unit}"
            total /= 1024
        return f"{total:.1f} TB"

    def _restore_from_cloud_layout(self, choice, paths, project_meta):
        """Restores a project from a Liferay Cloud backup layout."""
        UI.info("Detected Liferay Cloud backup layout. Restoring...")

        # 1. Database
        db_gz = choice / "database.gz"
        if db_gz.exists():
            UI.info("  + Extracting database dump...")
            # We'll place it in the project root for now, or deploy/ if it's an SQL
            # Better: if it's a seed, we might need a specific DB handler.
            # For now, just ensure the dir exists.
            paths["data"].mkdir(parents=True, exist_ok=True)
            # Cloud backups usually need manual intervention for DB import depending on DB type
            UI.warning("  ! Cloud DB dump detected. Manual import may be required.")

        # 2. Volume (Document Library / etc)
        volume_tgz = choice / "volume.tgz"
        if volume_tgz.exists():
            UI.info("  + Extracting data volume...")
            target_data = paths["data"]
            target_data.mkdir(parents=True, exist_ok=True)
            run_command(["tar", "-xzf", str(volume_tgz), "-C", str(target_data)])

        return True
