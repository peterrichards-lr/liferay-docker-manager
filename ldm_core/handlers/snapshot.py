import json
import os
import tarfile
import time
from datetime import datetime
from pathlib import Path
from typing import cast

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import get_actual_home, safe_extract


class SnapshotService(BaseHandler):
    def __init__(self, manager):
        super().__init__(manager.args)
        self.manager = manager

    def _manage_snapshots(self, paths, delete_arg, keep_last, older_than):  # noqa: C901, PLR0912
        backups_dir = paths["backups"]
        if not backups_dir.exists():
            UI.warning("No snapshots found to manage.")
            return

        backups = sorted(
            [d for d in backups_dir.iterdir() if d.is_dir()],
            key=lambda x: x.name,
            reverse=True,
        )

        if not backups:
            UI.warning("No snapshots found to manage.")
            return

        to_delete = []

        if delete_arg:
            target = None
            if delete_arg.isdigit():
                idx = int(delete_arg) - 1
                if 0 <= idx < len(backups):
                    target = backups[idx]
            else:
                for b in backups:
                    meta = self.manager.read_meta(b / "meta")
                    if meta.get("name") == delete_arg:
                        target = b
                        break

            if target:
                to_delete.append(target)
            else:
                UI.die(f"Snapshot not found matching index or name: '{delete_arg}'")

        if keep_last is not None:
            if keep_last < len(backups):
                to_delete.extend(backups[keep_last:])

        if older_than is not None:
            cutoff = time.time() - (older_than * 86400)
            for b in backups:
                if b.stat().st_mtime < cutoff and b not in to_delete:
                    to_delete.append(b)

        if not to_delete:
            UI.info("No snapshots matched management criteria.")
            return

        for snap in to_delete:
            meta = self.manager.read_meta(snap / "meta")
            name = meta.get("name", "Untitled")
            search_snap = meta.get("search_snapshot")

            UI.info(f"Deleting snapshot: {name} ({snap.name})...")

            # Delete global search snapshot if it exists
            if search_snap:
                search_name = "liferay-search-global"
                if self.manager.run_command(
                    ["docker", "ps", "-q", "-f", f"name={search_name}"]
                ):
                    self.manager.run_command(
                        [
                            "docker",
                            "exec",
                            search_name,
                            "curl",
                            "-s",
                            "-X",
                            "DELETE",
                            f"localhost:9200/_snapshot/liferay_backup/{search_snap}",
                        ]
                    )

            self.manager.safe_rmtree(snap)

        UI.success(f"Successfully deleted {len(to_delete)} snapshot(s).")

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
            size = self._get_dir_size(b)

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

    def cmd_snapshot(self, project_id=None, name=None):  # noqa: C901, PLR0912, PLR0915
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

        # Reclaim permissions on potential root-owned files before starting
        self.manager.verify_runtime_environment(paths)

        delete_arg = getattr(self.manager.args, "delete", None)
        keep_last = getattr(self.manager.args, "keep_last", None)
        older_than = getattr(self.manager.args, "older_than", None)
        if name is None:
            name = getattr(self.manager.args, "name", None)

        if delete_arg or keep_last is not None or older_than is not None:
            self._manage_snapshots(paths, delete_arg, keep_last, older_than)
            # If the user only provided management flags (no --name), exit after managing
            if not name and not self.manager.non_interactive and not delete_arg:
                # If they just said --keep-last 5, we shouldn't automatically create a new snapshot named "Manual Snapshot"
                # unless they actually want to. It's safer to exit.
                return
            if delete_arg:
                return  # Explicit delete command doesn't create a new snapshot

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

        # --- SEARCH SNAPSHOT (Orchestrated) ---
        search_snapshot_name = None
        search_name = "liferay-search-global"
        from ldm_core.utils import sanitize_id

        container_name = sanitize_id(
            project_meta.get("liferay_container_name")
            or project_meta.get("container_name")
            or root.name
        )

        # Check if project uses shared search and service is running
        if str(project_meta.get("use_shared_search", "false")).lower() == "true":
            if self.manager.run_command(
                ["docker", "ps", "-q", "-f", f"name={search_name}"]
            ):
                search_snapshot_name = f"{container_name}_{timestamp}"
                UI.info(
                    f"Triggering orchestrated search snapshot: {search_snapshot_name}..."
                )
                self.manager.run_command(
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

        # --- DATABASE SNAPSHOT (Orchestrated) ---
        db_type = project_meta.get("db_type", "hypersonic")
        db_snapshot_file = None
        if db_type in ["mysql", "postgresql", "mariadb"]:
            # LDM-388: Priority: Metadata -> Explicit Heuristic
            db_container = project_meta.get("db_container_name")
            from ldm_core.utils import resolve_infrastructure_mode

            db_mode = resolve_infrastructure_mode(
                "database_mode", project_meta or {}, self.manager.defaults
            )
            if db_mode == "shared":
                db_container = "liferay-db-global"

            if not db_container:
                for suffix in ["-db", "-db-1"]:
                    candidate = f"{container_name}{suffix}"
                    if self.manager.run_command(
                        ["docker", "ps", "-q", "-f", f"name=^{candidate}$"]
                    ):
                        db_container = candidate
                        break

            if db_container and self.manager.run_command(
                ["docker", "ps", "-q", "-f", f"name=^{db_container}$"]
            ):
                db_snapshot_file = snap_dir / "database.sql"
                UI.info(f"Triggering orchestrated database snapshot ({db_type})...")

                db_name = "lportal"
                if db_mode == "shared":
                    from ldm_core.utils import sanitize_id

                    db_name = (
                        f"lportal_{sanitize_id(paths['root'].name).replace('-', '_')}"
                    )

                if db_type in ["mysql", "mariadb"]:
                    # MySQL/MariaDB Dump (Explicitly include drop tables for rollback)
                    dump_cmd = [
                        "docker",
                        "exec",
                        db_container,
                        "mysqldump",
                        "-u",
                        "lportal",
                        "-ptest",
                        "--opt",
                        "--add-drop-table",
                        db_name,
                    ]
                else:
                    # PostgreSQL Dump (Include clean/drop commands for rollback)
                    dump_cmd = [
                        "docker",
                        "exec",
                        db_container,
                        "pg_dump",
                        "-U",
                        "lportal",
                        "--clean",
                        "--if-exists",
                        db_name,
                    ]

                try:
                    with open(db_snapshot_file, "wb") as db_f:
                        self.manager.run_command(dump_cmd, stdout_file=db_f)
                    if db_snapshot_file.stat().st_size > 0:
                        UI.success("Database dump completed.")
                    else:
                        if db_snapshot_file and db_snapshot_file.exists():
                            import time

                            for _ in range(5):
                                try:
                                    db_snapshot_file.unlink()
                                    break
                                except OSError:
                                    time.sleep(0.2)
                        UI.die("Database dump returned no content.", exit_code=3)
                except Exception as e:
                    if db_snapshot_file and db_snapshot_file.exists():
                        import time

                        for _ in range(5):
                            try:
                                db_snapshot_file.unlink()
                                break
                            except OSError:
                                time.sleep(0.2)
                    UI.die(f"Database dump failed: {e}", exit_code=3)

        # Wait for search snapshot if it was triggered
        if search_snapshot_name:
            if self._wait_for_search_snapshot(search_snapshot_name):
                UI.success("Search snapshot completed.")
                # Copy ES snapshot files to the backup dir so they are portable
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

        # --- CUSTOM IMAGES PACKAGING (Issue #618) ---
        custom_containers = project_meta.get("custom_containers")
        if custom_containers and isinstance(custom_containers, list):
            custom_images_dir = snap_dir / "custom_images"
            from ldm_core.utils import safe_mkdir

            safe_mkdir(custom_images_dir, parents=True, exist_ok=True)
            for container in custom_containers:
                image = container.get("image")
                c_name = container.get("service_name")
                if image and c_name:
                    UI.info(f"Saving custom image {image} for service {c_name}...")
                    image_tar = custom_images_dir / f"{c_name}.tar"
                    try:
                        res = self.manager.run_command(
                            ["docker", "save", image, "-o", str(image_tar)], check=False
                        )
                        if res is None:
                            UI.warning(
                                f"Failed to save custom image {image}. It may not exist locally."
                            )
                    except Exception as e:
                        UI.warning(f"Failed to save custom image {image}: {e}")

        # --- VOLUME DEHYDRATION (LDM-382) ---
        # If using Named Volumes (macOS), sync volume data back to host before archiving
        self._dehydrate_named_volumes(paths)

        # --- ARCHIVE ---
        # Final permission sync before archiving (Fixes late-created Docker file issues)
        # We call this again to ensure even files created by search snapshot are unlocked.
        self.manager.verify_runtime_environment(paths)

        # LDM-388: Proactively reclaim ownership of the project directories to ensure we can read all files for archiving.
        # We use 1000:1000 (Liferay standard) and 777 for bind mounts so LDM (host user) can read them.
        try:
            from ldm_core.utils import reclaim_volume_permissions

            UI.info("Reclaiming project permissions before snapshot...")
            for d in [
                "deploy",
                "files",
                "logs",
                "configs",
                "modules",
                "client-extensions",
            ]:
                if paths.get(d) and paths[d].exists():
                    reclaim_volume_permissions(
                        paths[d], uid="1000", gid="1000", chmod_val="777"
                    )
            for d in ["data", "state"]:
                if paths.get(d) and paths[d].exists():
                    reclaim_volume_permissions(
                        paths[d],
                        uid=str(os.getuid()),
                        gid=str(os.getgid()),
                        chmod_val="755",
                    )
        except Exception as e:
            UI.debug(f"Failed to reclaim permissions: {e}")

        from ldm_core.utils import safe_mkdir

        safe_mkdir(snap_dir, parents=True, exist_ok=True)
        files_tar = snap_dir / "files.tar.gz"

        with tarfile.open(files_tar, "w:gz") as tar:
            for f in [
                "files",
                "scripts",
                "osgi",
                "data",
                "deploy",
                "routes",
                "client-extensions",
                "configs",
                ".ldm",
            ]:
                f_path = paths["root"] / f
                if f_path.exists():
                    try:
                        # Re-verify specific path permissions before adding
                        UI.detail(f"Adding {f} to archive...")
                        tar.add(f_path, arcname=f)
                    except (PermissionError, OSError) as e:
                        UI.warning(f"Skipping {f} due to permission error: {e}")

            # Explicitly ensure osgi/state is included if it was missed by the generic 'osgi' add
            # (Happens if osgi/ exists but state was empty or handled as a separate mount point)
            osgi_state = paths["state"]
            if osgi_state.exists() and osgi_state.is_dir():
                try:
                    # Check if it's already in the tar to avoid duplicates
                    tar_names = [m.name for m in tar.getmembers()]
                    if "osgi/state" not in tar_names:
                        UI.detail("Adding missing osgi/state to archive...")
                        tar.add(osgi_state, arcname="osgi/state")
                except Exception:
                    pass

            # Explicitly add .ldm/fragment-overrides.json if it exists, since the .ldm dir is not archived entirely
            fragment_overrides = paths["root"] / ".ldm" / "fragment-overrides.json"
            if fragment_overrides.exists():
                try:
                    UI.detail("Adding .ldm/fragment-overrides.json to archive...")
                    tar.add(fragment_overrides, arcname=".ldm/fragment-overrides.json")
                except Exception as e:
                    UI.warning(
                        f"Skipping .ldm/fragment-overrides.json due to error: {e}"
                    )

            # If we have a search snapshot, bundle the global backup repo into the archive
            if search_snapshot_name:
                es_infra_backup = (
                    get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                )
                if es_infra_backup.exists():
                    from ldm_core.utils import reclaim_volume_permissions

                    reclaim_volume_permissions(es_infra_backup, chmod_val="777")
                    tar.add(es_infra_backup, arcname="search_backup")

        # Capture custom environment variables from docker-compose.yml
        custom_env_dict = {}
        compose_path = paths["root"] / "docker-compose.yml"
        if compose_path.exists():
            try:
                from ldm_core.utils import yaml_to_dict

                compose_data = yaml_to_dict(compose_path.read_text())
                liferay_service = (compose_data.get("services") or {}).get(
                    "liferay", {}
                )
                env_vars = liferay_service.get("environment", [])
                if isinstance(env_vars, list):
                    # Filter for LIFERAY_ variables that aren't the standard ones managed by LDM
                    standard_vars = [
                        "LIFERAY_JVM_OPTS",
                        "LIFERAY_HOME",
                        "LIFERAY_HSQL_PERIOD_ENABLED",
                    ]
                    for var in env_vars:
                        if "=" in var:
                            key, val = var.split("=", 1)
                            if key.startswith("LIFERAY_") and key not in standard_vars:
                                custom_env_dict[key] = val
            except Exception as e:
                UI.warning(
                    f"Could not parse docker-compose.yml for environment variables: {e}"
                )

        # Determine included resources dynamically
        db_included = db_snapshot_file is not None and db_snapshot_file.exists()

        has_data = False
        data_dir = paths.get("data")
        if data_dir and data_dir.exists() and data_dir.is_dir():
            try:
                has_data = any(data_dir.iterdir())
            except Exception:
                pass

        has_cx = False
        for d in ["cx", "deploy"]:
            dir_path = paths.get(d)
            if dir_path and dir_path.exists() and dir_path.is_dir():
                try:
                    if any(dir_path.glob("*.zip")):
                        has_cx = True
                        break
                except Exception:
                    pass
        if not has_cx:
            ce_dir = paths.get("ce_dir")
            if ce_dir and ce_dir.exists() and ce_dir.is_dir():
                try:
                    if any(ce_dir.glob("*.zip")) or any(ce_dir.glob("*/dist/*.zip")):
                        has_cx = True
                except Exception:
                    pass

        has_modules = False
        for d in ["modules", "deploy"]:
            dir_path = paths.get(d)
            if dir_path and dir_path.exists() and dir_path.is_dir():
                try:
                    if any(dir_path.glob("*.jar")) or any(dir_path.glob("*.war")):
                        has_modules = True
                        break
                except Exception:
                    pass
        if not has_modules:
            for s_folder in ["modules", "themes"]:
                base = paths["root"] / s_folder
                if base.exists() and base.is_dir():
                    try:
                        if any(base.glob("**/build/libs/*.[jw]ar")):
                            has_modules = True
                            break
                    except Exception:
                        pass

        # Collect client extensions list
        cx_list = []
        ce_dir = paths.get("ce_dir")
        if ce_dir and ce_dir.exists() and ce_dir.is_dir():
            try:
                cx_list = [f.name for f in ce_dir.glob("*.zip")]
                if not cx_list:
                    cx_list = [f.name for f in ce_dir.glob("*/dist/*.zip")]
            except Exception:
                pass

        # Collect OSGi modules list
        modules_list = []
        for d in ["modules", "deploy", "themes"]:
            dir_path = paths.get(d)
            if dir_path and dir_path.exists() and dir_path.is_dir():
                try:
                    for ext in ["*.jar", "*.war"]:
                        modules_list.extend([f.name for f in dir_path.glob(ext)])
                except Exception:
                    pass
        modules_list = sorted(set(modules_list))

        # Collect active services list
        active_services = []
        try:
            from ldm_core.utils import sanitize_id

            safe_name = sanitize_id(project_meta.get("container_name") or root.name)
            cmd = [
                "docker",
                "ps",
                "--filter",
                f"label=com.liferay.ldm.project={safe_name}",
                "--filter",
                "status=running",
                "--format",
                '{{.Label "com.docker.compose.service"}}',
            ]
            res = self.manager.run_command(cmd, check=False)
            if res and res.strip():
                active_services = sorted(
                    {line.strip() for line in res.strip().splitlines() if line.strip()}
                )
        except Exception:
            pass

        # Save metadata
        meta = {
            "name": name,
            "timestamp": timestamp,
            "tag": project_meta.get("tag"),
            "db_type": project_meta.get("db_type"),
            "host_name": getattr(self.manager.args, "host_name", None)
            or project_meta.get("host_name"),
            "ssl": str(
                getattr(self.manager.args, "ssl", None)
                if getattr(self.manager.args, "ssl", None) is not None
                else (project_meta.get("ssl") or "false")
            ).lower(),
            "search_snapshot": search_snapshot_name,
            "custom_env": json.dumps(custom_env_dict) if custom_env_dict else None,
            "includes_database": str(db_included).lower(),
            "includes_volume_assets": str(has_data).lower(),
            "includes_client_extensions": str(has_cx).lower(),
            "includes_osgi_modules": str(has_modules).lower(),
            "client_extensions": ",".join(cx_list) if cx_list else "",
            "osgi_modules": ",".join(modules_list) if modules_list else "",
            "active_services": ",".join(active_services) if active_services else "",
        }
        self.manager.write_meta(snap_dir, meta)

        # 4. Generate SHA-256 Checksum
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
        # For new projects (seeding), meta might not exist yet
        project_meta = self.manager.read_meta(paths["root"]) or {}

        # 0. Support for --list (Non-interactive overview)
        if getattr(self.manager.args, "list", False):
            self.cmd_snapshots(paths)
            return

        # 1. Resolve choice (direct dir, index, or interactive)
        choice = None
        if backup_dir:
            choice = Path(backup_dir)
        elif getattr(self.manager.args, "backup_dir", None):
            choice = Path(self.manager.args.backup_dir)
        elif getattr(self.manager.args, "latest", False):
            backups = self.cmd_snapshots(paths)
            if not backups:
                UI.die("No snapshots available for --latest.")
            choice = backups[0]  # sorted reverse=True, so index 0 is latest

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
            elif getattr(self.manager.args, "name", None):
                target_name = self.manager.args.name
                for b in backups:
                    meta = self.manager.read_meta(b / "meta")
                    if meta.get("name") == target_name:
                        choice = b
                        break
                if not choice:
                    UI.die(f"No snapshot found with name: '{target_name}'")
            elif getattr(self.manager.args, "index", None):
                choice = backups[self.manager.args.index - 1]
            elif self.manager.non_interactive:
                UI.die(
                    "No snapshot index specified. In non-interactive mode, use: ldm restore <pid> --index <num>"
                )
            else:
                choice = backups[int(UI.ask("Select snapshot index", "1")) - 1]

        if not choice or not choice.exists():
            UI.die(f"Snapshot directory not found: {choice}")

        # At this point Mypy should know choice is a Path and not None
        choice_path = cast(Path, choice)

        # 1.5 Ensure Compose file exists (Mandate 2.1)
        if not (paths["root"] / "docker-compose.yml").exists():
            UI.info("Scaffolding Docker environment for restore...")
            self.manager.runtime.cmd_run(
                project_id=project_meta.get("container_name") or paths["root"].name,
                no_up=True,
                show_summary=False,
                paths=paths,
                project_meta=project_meta,
            )

        # 2. Reset the Environment (Clean Slate)
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
            # LDM-388/389: Do a FULL reset (wipe host volumes and down -v for anonymous DB volumes)
            # This ensures the restore is a 100% clean slate, preventing leftover files or DB rows.
            self.manager.runtime.cmd_reset(project_id=paths["root"].name, target="all")
            time.sleep(2)

        # 3. File System Restore (Standard or Cloud)
        files_tar = choice_path / "files.tar.gz"
        volume_tgz = choice_path / "volume.tgz"

        if files_tar.exists():
            # Verify Integrity (Mandate 6.2)
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
            else:
                UI.warning("Integrity verification disabled via --no-verify.")

            self._extract_snapshot_archive(files_tar, paths)

            # Back up the restored/imported portal-ext.properties to serve as .ldmp/snapshot baseline
            if "files" in paths:
                target_pe = paths["files"] / "portal-ext.properties"
                if target_pe.exists():
                    ldm_dir = paths["root"] / ".liferay-docker"
                    ldm_dir.mkdir(parents=True, exist_ok=True)
                    import shutil

                    shutil.copy2(target_pe, ldm_dir / "ldmp-portal-ext.properties")

            # Fallback for older or custom packaging scripts that place .ldm outside files.tar.gz
            if (choice_path / ".ldm").exists():
                import shutil

                shutil.copytree(
                    choice_path / ".ldm", paths["root"] / ".ldm", dirs_exist_ok=True
                )
        elif volume_tgz.exists() or (choice_path / "volume").is_dir():
            UI.detail("  + Restoring cloud data volume...")
            target_data = paths["data"]
            from ldm_core.utils import safe_mkdir

            safe_mkdir(target_data, parents=True, exist_ok=True)

            if volume_tgz.exists():
                hash_file = target_data / ".ldm_volume.sha256"
                skip_extraction = False
                # If target directory is empty or missing, we must extract
                has_files = False
                try:
                    if target_data.exists():
                        has_files = any(
                            item
                            for item in target_data.iterdir()
                            if item.name not in (".DS_Store", ".ldm_volume.sha256")
                        )
                except Exception:
                    pass

                if has_files and hash_file.exists():
                    try:
                        from ldm_core.utils import calculate_sha256

                        current_hash = calculate_sha256(volume_tgz)
                        cached_hash = hash_file.read_text().strip()
                        if current_hash == cached_hash:
                            skip_extraction = True
                            UI.info(
                                "  + Volume archive unchanged (hash matched). Skipping extraction."
                            )
                    except Exception:
                        pass

                if not skip_extraction:
                    self.manager.run_command(
                        ["tar", "-xzf", str(volume_tgz), "-C", str(target_data)]
                    )
                    try:
                        from ldm_core.utils import calculate_sha256

                        current_hash = calculate_sha256(volume_tgz)
                        hash_file.write_text(current_hash)
                    except Exception:
                        pass
            else:
                # LDM-408/422/423: Robust Volume Hydration (Mac Sync Resilience)
                # 1. First, synchronously copy the snapshot to the host project folder.
                # This ensures the files are physically on the disk and owned by the user.
                import shutil

                from ldm_core.utils import safe_rmtree

                if target_data.exists():
                    safe_rmtree(target_data)

                UI.detail("  + Unpacking volume to host...")
                shutil.copytree(str(choice_path / "volume"), str(target_data))

                # 2. On macOS (Named Volumes), we must push the host data into the Docker volume.
                if self.manager.composer.is_using_named_volumes():
                    # LDM-423: Critical 'Sync Wait'. On macOS, the Docker hypervisor (VirtioFS)
                    # needs a moment to 'see' the files we just wrote to the host before we
                    # can mount them into a container for the tar-sync.
                    time.sleep(2)

                    UI.detail("  + Hydrating internal Docker volumes...")

                    self._hydrate_named_volumes(paths)

            UI.success("Cloud volume restoration completed.")

            # LDM-424: Smart Store Detection (Automatic path resolution)
            # We look at the physical folders in the document library to see if they follow
            # the simplified FileSystemStore layout or the nested AdvancedFileSystemStore.
            try:
                doclib_root = target_data / "document_library"
                if doclib_root.exists():
                    is_simple = False
                    for company_dir in doclib_root.iterdir():
                        if company_dir.is_dir() and company_dir.name.isdigit():
                            # If the company dir contains any folders that are NOT numbers (like ._*)
                            # or if it contains folder IDs directly that are referenced in the DB
                            # we assume it might be simple.
                            # Better heuristic: Advanced store has repositoryId (Group ID) as 2nd level.
                            # Simple store has folderId as 2nd level.
                            # If we find a deep file at level 3 instead of level 4, it's simple.
                            subdirs = [
                                d
                                for d in company_dir.iterdir()
                                if d.is_dir() and d.name.isdigit()
                            ]
                            if subdirs:
                                # Check if any of these subdirs contain further numbered subdirs
                                for s in subdirs:
                                    grandkids = [
                                        d
                                        for d in s.iterdir()
                                        if d.is_dir() and d.name.isdigit()
                                    ]
                                    if not grandkids:
                                        is_simple = True
                                        break
                        if is_simple:
                            break

                    if is_simple:
                        UI.info("  + Detected simplified FileSystemStore layout.")
                        project_meta["dl_store_impl"] = (
                            "com.liferay.portal.store.file.system.FileSystemStore"
                        )
                    else:
                        project_meta["dl_store_impl"] = (
                            "com.liferay.portal.store.file.system.AdvancedFileSystemStore"
                        )
                    self.manager.write_meta(paths["root"], project_meta)
            except Exception as e:
                UI.debug(f"Store detection failed: {e}")
        else:
            UI.die(f"Snapshot files not found in {choice_path}")

        # --- SEARCH RESTORE (Orchestrated) ---
        snap_meta = self.manager.read_meta(choice_path / "meta")
        search_snapshot_name = snap_meta.get("search_snapshot")
        search_name = "liferay-search-global"

        # Restore custom environment variables to project metadata
        custom_env = snap_meta.get("custom_env")
        if custom_env:
            project_meta["custom_env"] = custom_env
            self.manager.write_meta(paths["root"], project_meta)

        # Restore tag/version to project metadata so we revert to the correct Liferay version (Issue #246)
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

        # --- DATABASE RESTORE (Orchestrated) ---
        sql_file = choice_path / "database.sql"
        db_gz = choice_path / "database.gz"

        # If cloud database dump exists but hasn't been extracted yet
        if db_gz.exists() and not sql_file.exists():
            UI.detail("  + Decompressing cloud database dump...")
            import gzip
            import shutil

            try:
                with gzip.open(str(db_gz), "rb") as f_in:
                    with open(str(sql_file), "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                UI.success("Cloud database dump decompressed.")
            except Exception as e:
                UI.warning(f"Failed to decompress {db_gz.name}: {e}")

        db_type = project_meta.get("db_type", "hypersonic")
        if db_type == "hypersonic":
            UI.success("  + Hypersonic database restored successfully (file-based).")
        elif sql_file.exists():
            # LDM-413: Scrub proprietary LCP \restrict meta-commands
            # These commands cause standard psql to fail when ON_ERROR_STOP=1 is active
            try:
                import tempfile

                scrubbed = False
                with tempfile.NamedTemporaryFile(
                    dir=str(sql_file.parent), delete=False, mode="w", encoding="utf-8"
                ) as temp_file:
                    temp_sql = Path(temp_file.name)

                    with open(str(sql_file), encoding="utf-8", errors="ignore") as f_in:
                        # Quick check: does it even have \restrict near the top?
                        first_chunk = f_in.read(4096)
                        if "\\restrict" in first_chunk:
                            UI.info(
                                "  + Scrubbing Cloud-specific meta-commands from SQL dump..."
                            )
                            f_in.seek(0)
                            for line in f_in:
                                if line.startswith("\\restrict") or line.startswith(
                                    "\\unrestrict"
                                ):
                                    continue
                                temp_file.write(line)
                            scrubbed = True

                # We must close the NamedTemporaryFile block before moving it to avoid file locks on some OSs
                if scrubbed:
                    from ldm_core.utils import safe_move

                    safe_move(str(temp_sql), str(sql_file))
                elif temp_sql.exists():
                    temp_sql.unlink(missing_ok=True)
            except Exception as e:
                UI.debug(f"SQL scrub failed: {e}")
                if "temp_sql" in locals() and temp_sql.exists():
                    temp_sql.unlink(missing_ok=True)

            UI.info(f"Triggering orchestrated database restore ({db_type})...")

            # 1. Ensure DB container is running, but Liferay is STOPPED
            # This is critical to prevent Liferay from locking the database or
            # attempting to initialize schemas during the restore process.
            self.manager.runtime.cmd_stop(project_id, service="liferay")

            from ldm_core.utils import resolve_infrastructure_mode

            db_mode = resolve_infrastructure_mode(
                "database_mode", project_meta, self.manager.defaults
            )

            db_container = project_meta.get("db_container_name")
            if db_mode == "shared":
                db_container = (
                    "liferay-db-mysql-global"
                    if db_type in ["mysql", "mariadb"]
                    else "liferay-db-global"
                )

            if not db_container:
                for suffix in ["-db", "-db-1"]:
                    candidate = f"{container_name}{suffix}"
                    if self.manager.run_command(
                        ["docker", "ps", "-q", "-f", f"name=^{candidate}$"]
                    ):
                        db_container = candidate
                        break

            if not db_container or not self.manager.run_command(
                ["docker", "ps", "-q", "-f", f"name=^{db_container}$"]
            ):
                UI.info("  + Starting database container for restore...")
                if db_mode == "shared":
                    self.manager.infra.setup_global_database()
                else:
                    from ldm_core.utils import get_compose_cmd

                    compose_base = get_compose_cmd()
                    if compose_base:
                        self.manager.run_command(
                            [*compose_base, "up", "-d", "db"], cwd=str(paths["root"])
                        )

                        # Wait for DB to be responsive
                        for _i in range(10):
                            time.sleep(2)
                            for suffix in ["-db", "-db-1"]:
                                candidate = f"{container_name}{suffix}"
                                if self.manager.run_command(
                                    ["docker", "ps", "-q", "-f", f"name=^{candidate}$"]
                                ):
                                    db_container = candidate
                                    break
                            if db_container:
                                break

            # Wait for shared database to be responsive before importing
            if db_mode == "shared" and db_container:
                UI.detail(
                    f"Waiting for shared database ({UI.CYAN}{db_container}{UI.COLOR_OFF}) to be ready..."
                )
                start_wait = time.time()
                while time.time() - start_wait < 60:
                    status = self.manager.get_container_status(db_container)
                    if status in {"healthy", "running"}:
                        time.sleep(2)
                        break
                    if status == "exited":
                        UI.error(
                            f"Global database container '{db_container}' exited unexpectedly."
                        )
                        return
                    time.sleep(2)

            if db_container:
                self._execute_orchestrated_db_restore(
                    db_container, db_type, sql_file, paths, project_meta
                )
            else:
                UI.error("  ! Could not find database container for restore.")

        if search_snapshot_name and search_snapshot_name != "None":
            if self.manager.run_command(
                ["docker", "ps", "-q", "-f", f"name={search_name}"]
            ):
                UI.info(
                    f"Triggering orchestrated search restore: {search_snapshot_name}..."
                )

                # 1. Clear existing indices for this project
                self._delete_project_indices(container_name)

                # 2. Trigger restore
                self.manager.run_command(
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

        # --- CUSTOM IMAGES RESTORE (Issue #618) ---
        custom_images_dir = choice_path / "custom_images"
        if custom_images_dir.exists() and custom_images_dir.is_dir():
            UI.info("Loading custom container images from snapshot...")
            for tar_file in custom_images_dir.glob("*.tar"):
                UI.detail(f"  + Loading image from {tar_file.name}...")
                try:
                    self.manager.run_command(["docker", "load", "-i", str(tar_file)])
                except Exception as e:
                    UI.warning(f"Failed to load custom image {tar_file.name}: {e}")

        UI.success("Restore complete.")

        # LDM-422: Flag for one-time automatic reindex on next boot
        # Liferay won't automatically reindex imported databases. We set a metadata
        # flag that tells the composer to inject index.on.startup=true for the next run.
        if self.flag_reindex(paths["root"]):
            UI.info("  + Scheduled automatic search reindex for next boot.")
        else:
            UI.warning("  ! Could not schedule automatic reindex (metadata missing).")

        # --- OPTIONAL STARTUP (LDM-388) ---
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

    def _extract_snapshot_archive(self, files_tar, paths):
        """Extracts a snapshot tarball into the project root with security checks."""
        # Ensure paths is a dictionary for subscripting
        if not isinstance(paths, dict):
            paths = self.manager.setup_paths(paths)

        # Guardrail: Pre-Flight System Resource & Disk Space Checks for Hydration (Issue #168)
        target_root = paths["root"].resolve()
        import shutil

        try:
            free_space = shutil.disk_usage(target_root).free
            archive_size = Path(files_tar).stat().st_size
            if free_space < archive_size * 1.5:
                free_mb = round(free_space / (1024 * 1024), 2)
                required_mb = round((archive_size * 1.5) / (1024 * 1024), 2)
                UI.die(
                    f"Insufficient disk space on target partition to safely extract backup. "
                    f"Required: {required_mb} MB (1.5x archive size), Available: {free_mb} MB."
                )
        except OSError as e:
            UI.warning(f"Could not verify available disk space: {e}")

        no_osgi = getattr(self.manager.args, "no_osgi_seed", False)

        with tarfile.open(files_tar, "r:gz") as tar:
            from ldm_core.utils import is_safe_path

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
                is_link = m.issym() or m.islnk()
                if not is_safe_path(target_root, m.name, is_link, m.linkname):
                    UI.error(f"Security: Skipping unsafe member: {m.name}")
                    continue

                member_path = (target_root / m.name).resolve()
                # Pre-emptively remove file to avoid PermissionError (Errno 13) during overwrite
                if member_path.exists() and not member_path.is_dir():
                    try:
                        member_path.unlink()
                    except Exception:
                        pass

                members.append(m)

            tar.errorlevel = (
                0  # Robustness: suppress non-fatal OSErrors (like copystat failures)
            )
            safe_extract(tar, target_root, members=members)

            # 2. Extract search_backup if present
            has_search = any(
                m.name.startswith("search_backup") for m in tar.getmembers()
            )
            if has_search:
                es_infra_backup = (
                    get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                )
                from ldm_core.utils import safe_mkdir

                safe_mkdir(es_infra_backup, parents=True, exist_ok=True)
                es_infra_root = es_infra_backup.resolve()

                # Reclaim permissions before extracting (Fixes [Errno 13] in CI/Linux)
                from ldm_core.utils import reclaim_volume_permissions

                reclaim_volume_permissions(es_infra_backup, chmod_val="777")

                for m in tar.getmembers():
                    if m.name.startswith("search_backup/"):
                        # Security: Validate path
                        rel_name = m.name.replace("search_backup/", "", 1)
                        if not rel_name:
                            continue
                        is_link = m.issym() or m.islnk()
                        if not is_safe_path(
                            es_infra_root, rel_name, is_link, m.linkname
                        ):
                            UI.error(f"Security: Skipping unsafe ES member: {m.name}")
                            continue

                        # Temporarily adjust member name for extraction into the target dir
                        m.name = rel_name
                        tar.extract(m, path=es_infra_root)  # nosec B202

            # 3. Hydrate Named Volumes (LDM-382)
            # If using Named Volumes (macOS), sync the extracted files into Docker volumes
            self._hydrate_named_volumes(paths)

    def _wait_for_search_snapshot(self, snapshot_name, timeout=120):
        search_name = "liferay-search-global"
        start_time = time.time()
        while time.time() - start_time < timeout:
            res = self.manager.run_command(
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
            res = self.manager.run_command(
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
        return False

    def _delete_project_indices(self, container_name):
        search_name = "liferay-search-global"
        self.manager.run_command(
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
        total: float = 0
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

    def _sync_volume(self, host_path, volume_name, direction="to_volume"):
        """Synchronizes data between a host directory and a Docker Named Volume."""
        host_path_abs = Path(host_path).resolve()
        if not host_path_abs.exists():
            host_path_abs.mkdir(parents=True, exist_ok=True)

        # Ensure volume exists
        self.manager.run_command(
            ["docker", "volume", "create", volume_name], check=False
        )

        src = "/host" if direction == "to_volume" else "/vol"
        dst = "/vol" if direction == "to_volume" else "/host"

        # Note: We use 'tar' piped to 'tar' to safely copy the entire directory structure
        # (including hidden files and deep nesting) without relying on shell expansion quirks in Alpine.
        # LDM-420: We MUST chown the files to 1000:1000 (liferay) when hydrating the volume,
        # otherwise the Alpine container creates them as root, causing 404 access errors.
        chown_cmd = f" && chown -R 1000:1000 {dst}" if direction == "to_volume" else ""
        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{host_path_abs.as_posix()}:/host",
            "-v",
            f"{volume_name}:/vol",
            "alpine",
            "sh",
            "-c",
            f"tar -cC {src} . | tar -xC {dst}{chown_cmd}",
        ]

        try:
            if self.manager.verbose:
                UI.info(
                    f"  + Syncing volume {UI.CYAN}{volume_name}{UI.COLOR_OFF} ({direction})..."
                )
            res = self.manager.run_command(cmd, check=False)
            if res is None:
                UI.warning(
                    f"Failed to sync volume {volume_name}: Command execution returned error status."
                )
                return False
            return True
        except Exception as e:
            UI.warning(f"Failed to sync volume {volume_name}: {e}")
            return False

    def _dehydrate_named_volumes(self, paths):
        """Copies data from Docker Named Volumes back to the host for snapshotting."""
        if not self.manager.composer.is_using_named_volumes():
            return

        meta = self.manager.read_meta(paths["root"])
        c_name = meta.get("container_name") or paths["root"].name

        for target in ["data", "state"]:
            volume_name = f"{c_name}-{target}"
            host_path = paths[target]
            UI.info(
                f"  + Dehydrating volume {UI.CYAN}{volume_name}{UI.COLOR_OFF} to host..."
            )
            self._sync_volume(host_path, volume_name, direction="from_volume")

    def _hydrate_named_volumes(self, paths):
        """Copies data from the host into Docker Named Volumes after extraction."""
        if not self.manager.composer.is_using_named_volumes():
            return

        meta = self.manager.read_meta(paths["root"])
        c_name = meta.get("container_name") or paths["root"].name

        for target in ["data", "state"]:
            volume_name = f"{c_name}-{target}"
            host_path = paths[target]
            if host_path.exists():
                UI.info(
                    f"  + Hydrating volume {UI.CYAN}{volume_name}{UI.COLOR_OFF} from host..."
                )
                self._sync_volume(host_path, volume_name, direction="to_volume")

    def _execute_orchestrated_db_restore(  # noqa: C901, PLR0912, PLR0915
        self, db_container, db_type, sql_file, paths, project_meta
    ):
        """Internal helper to execute a robust SQL import into a running DB container."""
        import subprocess

        from ldm_core.utils import resolve_infrastructure_mode

        db_mode = resolve_infrastructure_mode(
            "database_mode", project_meta or {}, self.manager.defaults
        )
        db_name = "lportal"
        if db_mode == "shared":
            from ldm_core.utils import sanitize_id

            db_name = f"lportal_{sanitize_id(paths['root'].name).replace('-', '_')}"

        def _wipe_db():
            # 1. Clean Slate (LDM-410)
            # Cloud dumps often lack DROP TABLE commands. We must wipe the target DB first.
            if db_type == "postgresql":
                UI.detail("  - Wiping existing PostgreSQL database schema...")
                # LDM-416: Liferay's official image sets POSTGRES_USER=lportal, meaning the
                # default 'postgres' user DOES NOT EXIST. We must use lportal (which is granted superuser).
                # We use a comprehensive DO block to drop all objects in the public schema
                # to guarantee a clean slate without needing to drop the database itself.
                wipe_script = """
                DO $$ DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                        EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
                    END LOOP;
                    FOR r IN (SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'S') LOOP
                        EXECUTE 'DROP SEQUENCE IF EXISTS public.' || quote_ident(r.relname) || ' CASCADE';
                    END LOOP;
                    FOR r IN (SELECT viewname FROM pg_views WHERE schemaname = 'public') LOOP
                        EXECUTE 'DROP VIEW IF EXISTS public.' || quote_ident(r.viewname) || ' CASCADE';
                    END LOOP;

                    -- LDM-416: Clear Large Objects to prevent pg_largeobject_metadata_oid_index collisions
                    -- 'lo_unlink' is insufficient for Liferay's usage pattern. We must directly delete.
                    DELETE FROM pg_largeobject_metadata;
                    DELETE FROM pg_largeobject;

                    -- Mock cloudsqlsuperuser to prevent ON_ERROR_STOP=1 from aborting LCP imports
                    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'cloudsqlsuperuser') THEN
                        CREATE ROLE cloudsqlsuperuser;
                    END IF;
                END $$;
                """
                # LDM-418: Execute via stdin instead of -c to prevent multi-line parsing failures across docker exec
                # We wrap this in a retry loop because a freshly created Postgres container
                # will initialize and then restart itself, causing temporary connection refusals.
                wipe_success = False
                for _wipe_attempt in range(6):  # Up to ~30 seconds wait
                    try:
                        subprocess.run(
                            [
                                "docker",
                                "exec",
                                "-i",
                                db_container,
                                "psql",
                                "-U",
                                "lportal",
                                "-d",
                                db_name,
                            ],
                            input=wipe_script.encode("utf-8"),
                            check=True,
                            capture_output=True,
                        )
                        wipe_success = True
                        break
                    except subprocess.CalledProcessError as e:
                        # LDM-423: Capture both stdout and stderr for robust error parsing
                        # psql sometimes sends connection errors to stdout
                        raw_err = (e.stderr or b"").decode(errors="ignore")
                        raw_out = (e.stdout or b"").decode(errors="ignore")
                        err_out = f"{raw_err} {raw_out}".lower()

                        if (
                            "shutting down" in err_out
                            or "starting up" in err_out
                            or "does not exist" in err_out
                            or "no such file or directory" in err_out
                        ):
                            UI.debug(f"DB initializing, waiting... ({err_out.strip()})")
                            time.sleep(5)
                        else:
                            UI.warning(f"  ! Non-fatal wipe error: {err_out.strip()}")
                            break  # Other SQL error, stop retrying
                    except Exception as e:
                        UI.warning(f"  ! Wipe encountered an error: {e}")
                        break

                if not wipe_success:
                    UI.warning(
                        "  ! Could not confirm successful schema wipe. Restore may fail."
                    )

            elif db_type in ["mysql", "mariadb"]:
                UI.detail("  - Wiping existing MySQL database...")
                self.manager.run_command(
                    [
                        "docker",
                        "exec",
                        db_container,
                        "mysql",
                        "-u",
                        "lportal",
                        "-ptest",
                        "-e",
                        f"DROP DATABASE IF EXISTS {db_name}; CREATE DATABASE {db_name};",
                    ],
                    check=False,
                )

        # 2. Build Import Command as a list
        import_cmd = []
        if db_type == "postgresql":
            # LDM-410: Use standard user and enforce error stopping for reliability
            import_cmd = [
                "docker",
                "exec",
                "-i",
                db_container,
                "psql",
                "-U",
                "lportal",
                "-d",
                db_name,
                "-v",
                "ON_ERROR_STOP=1",
            ]
        elif db_type in ["mysql", "mariadb"]:
            import_cmd = [
                "docker",
                "exec",
                "-i",
                db_container,
                "mysql",
                "-u",
                "lportal",
                "-ptest",
                db_name,
            ]

        # 3. Execute with Retry
        if import_cmd:
            success = False
            baseline_file = Path(sql_file).parent / ".restore_baseline.sql"
            baseline_dump_cmd = []
            if db_type in ["mysql", "mariadb"]:
                baseline_dump_cmd = [
                    "docker",
                    "exec",
                    db_container,
                    "mysqldump",
                    "-u",
                    "lportal",
                    "-ptest",
                    "--opt",
                    "--add-drop-table",
                    db_name,
                ]
            elif db_type == "postgresql":
                baseline_dump_cmd = [
                    "docker",
                    "exec",
                    db_container,
                    "pg_dump",
                    "-U",
                    "lportal",
                    "--clean",
                    "--if-exists",
                    db_name,
                ]

            has_baseline = False
            if baseline_dump_cmd:
                try:
                    with open(baseline_file, "wb") as bf:
                        subprocess.run(
                            baseline_dump_cmd,
                            stdout=bf,
                            stderr=subprocess.PIPE,
                            check=False,
                        )
                    if baseline_file.exists() and baseline_file.stat().st_size > 0:
                        has_baseline = True
                except Exception as e:
                    UI.debug(f"Could not create pre-restore baseline (non-fatal): {e}")

            for i in range(3):  # Retry up to 3 times for flaky Docker IO
                # LDM-422: We MUST wipe the DB at the start of EVERY retry attempt.
                # If attempt 1 fails halfway through, the tables are created. Attempt 2 will instantly
                # fail with "relation already exists" unless the schema is dropped again.
                _wipe_db()
                try:
                    # Pipe the file stream directly via stdin to prevent memory buffering and command injection
                    with open(sql_file, "rb") as sql_f:
                        subprocess.run(
                            import_cmd,
                            stdin=sql_f,
                            check=True,
                            capture_output=True,
                        )
                    success = True
                    break
                except subprocess.CalledProcessError as e:
                    # LDM-423: Capture both for better debugging
                    raw_err = (e.stderr or b"").decode(errors="ignore")
                    raw_out = (e.stdout or b"").decode(errors="ignore")
                    err_out = f"{raw_err} {raw_out}".strip()

                    if i < 2:
                        UI.warning(f"  ! Restore attempt {i + 1} failed, retrying...")
                        UI.debug(f"  ! Error: {err_out}")
                        time.sleep(5)
                    else:
                        UI.error(
                            f"  ! Database restore failed after 3 attempts: {err_out}"
                        )
                except Exception as e:
                    if i < 2:
                        UI.warning(f"  ! Restore attempt {i + 1} failed, retrying...")
                        UI.debug(f"  ! Error: {e}")
                        time.sleep(5)
                    else:
                        UI.error(f"  ! Database restore failed after 3 attempts: {e}")

            if not success:
                if has_baseline and baseline_file.exists():
                    UI.info("Restoring database to the pre-restore baseline...")
                    _wipe_db()
                    try:
                        with open(baseline_file, "rb") as bf_read:
                            subprocess.run(
                                import_cmd,
                                stdin=bf_read,
                                check=True,
                                capture_output=True,
                            )
                        UI.info(
                            "Original database data has been successfully restored."
                        )
                    except Exception as e:
                        UI.error(f"Failed to restore original database baseline: {e}")

                if baseline_file.exists():
                    try:
                        baseline_file.unlink()
                    except OSError:
                        pass

                UI.die(
                    "Database restore failed after all retries. Original data has been preserved.",
                    exit_code=3,
                )

            if baseline_file.exists():
                try:
                    baseline_file.unlink()
                except OSError:
                    pass

            if success:
                UI.success("  + Database restored successfully.")
                if hasattr(self.manager, "config") and hasattr(
                    self.manager.config, "track_roi"
                ):
                    self.manager.config.track_roi(300, "database restore")

                # LDM-410: Auto-update virtualhost to match local hostname

                host_name = project_meta.get("host_name", "localhost")
                from ldm_core.utils import resolve_infrastructure_mode

                db_mode = resolve_infrastructure_mode(
                    "database_mode", project_meta or {}, self.manager.defaults
                )
                db_name = "lportal"
                if db_mode == "shared":
                    from ldm_core.utils import sanitize_id

                    db_name = (
                        f"lportal_{sanitize_id(paths['root'].name).replace('-', '_')}"
                    )

                if db_type == "postgresql":
                    UI.info(f"  - Synchronizing Virtual Host entries to: {host_name}")
                    self.manager.run_command(
                        [
                            "docker",
                            "exec",
                            db_container,
                            "psql",
                            "-U",
                            "lportal",
                            "-d",
                            db_name,
                            "-c",
                            f"UPDATE virtualhost SET hostname = '{host_name}';",  # nosec B608
                        ],
                        check=False,
                    )
                elif db_type in ["mysql", "mariadb"]:
                    UI.info(f"  - Synchronizing Virtual Host entries to: {host_name}")
                    self.manager.run_command(
                        [
                            "docker",
                            "exec",
                            db_container,
                            "mysql",
                            "-u",
                            "lportal",
                            "-ptest",
                            "-e",
                            f"UPDATE {db_name}.virtualhost SET hostname = '{host_name}';",  # nosec B608
                        ],
                        check=False,
                    )

    def _list_backups(self, paths):
        """Helper to list available snapshots for a project, returning a list of dicts with paths."""
        backups_dir = paths["backups"]
        if not backups_dir.exists():
            return []

        # Find all directories in the snapshots folder, sorted by name
        subdirs = sorted(
            [d for d in backups_dir.iterdir() if d.is_dir()],
            key=lambda x: x.name,
        )

        return [{"path": d, "name": d.name} for d in subdirs]

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
                backups = self._list_backups(paths)
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
            backups = self._list_backups(paths)
            if not backups:
                UI.info("No existing snapshots found. Creating a new one...")
                self.cmd_snapshot(project_id)
                backups = self._list_backups(paths)
            if not backups:
                UI.die("Failed to locate or create a project snapshot.")
                return
            latest_snap_dir = backups[-1]["path"]
        else:
            # Create a fresh snapshot
            UI.info("Creating a fresh snapshot for the package...")
            self.cmd_snapshot(project_id)
            backups = self._list_backups(paths)
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
