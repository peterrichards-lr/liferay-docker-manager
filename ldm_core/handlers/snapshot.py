import json
import tarfile
import time
from datetime import datetime
from pathlib import Path
from typing import cast

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import get_actual_home


class SnapshotService(BaseHandler):
    def __init__(self, manager):
        super().__init__(manager.args)
        self.manager = manager

    def _manage_snapshots(self, paths, delete_arg, keep_last, older_than):
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
            meta = self.manager.read_meta(b / "meta")
            name = meta.get("name", "Untitled")
            timestamp = b.name
            size = self._get_dir_size(b)
            print(
                f"[{i + 1}] {UI.CYAN}{timestamp}{UI.COLOR_OFF} - {UI.BOLD}{name}{UI.COLOR_OFF} ({size})"
            )

        return backups

    def cmd_snapshot(self, project_id=None):
        """Creates or manages snapshots of the project state."""
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
        container_name = (
            project_meta.get("liferay_container_name")
            or project_meta.get("container_name")
            or root.name.replace(".", "-")
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
                        "lportal",
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
                        "lportal",
                    ]

                try:
                    sql_content = self.manager.run_command(
                        dump_cmd, capture_output=True
                    )
                    if sql_content:
                        db_snapshot_file.write_text(sql_content)
                        UI.success("Database dump completed.")
                    else:
                        UI.warning("Database dump returned no content.")
                        db_snapshot_file = None
                except Exception as e:
                    UI.warning(f"Database dump failed: {e}")
                    db_snapshot_file = None

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

        # --- VOLUME DEHYDRATION (LDM-382) ---
        # If using Named Volumes (macOS), sync volume data back to host before archiving
        self._dehydrate_named_volumes(paths)

        # --- ARCHIVE ---
        # Final permission sync before archiving (Fixes late-created Docker file issues)
        # We call this again to ensure even files created by search snapshot are unlocked.
        self.manager.verify_runtime_environment(paths)

        # LDM-388: Proactively reclaim ownership of the project directories to ensure we can read all files for archiving.
        # We use 1000:1000 (Liferay standard) and 777 for bind mounts so LDM (host user) can read them.
        # CRITICAL: For named volumes (data, state) we use 755 and host UID to prevent breaking Elasticsearch which refuses 777.
        try:
            import os

            from ldm_core.utils import reclaim_volume_permissions

            UI.info("Reclaiming project permissions before snapshot...")
            for d in ["deploy", "files", "logs", "configs", "modules"]:
                if paths[d].exists():
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
            for f in ["files", "scripts", "osgi", "data", "deploy", "routes"]:
                f_path = paths["root"] / f
                if f_path.exists():
                    try:
                        # Re-verify specific path permissions before adding
                        if self.manager.verbose:
                            UI.info(f"Adding {f} to archive...")
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
                        if self.manager.verbose:
                            UI.info("Adding missing osgi/state to archive...")
                        tar.add(osgi_state, arcname="osgi/state")
                except Exception:
                    pass

            # If we have a search snapshot, bundle the global backup repo into the archive
            if search_snapshot_name:
                es_infra_backup = (
                    get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                )
                if es_infra_backup.exists():
                    from ldm_core.utils import reclaim_volume_permissions

                    reclaim_volume_permissions(es_infra_backup)
                    tar.add(es_infra_backup, arcname="search_backup")

        # Capture custom environment variables from docker-compose.yml
        custom_env_dict = {}
        compose_path = paths["root"] / "docker-compose.yml"
        if compose_path.exists():
            try:
                from ldm_core.utils import yaml_to_dict

                compose_data = yaml_to_dict(compose_path.read_text())
                liferay_service = compose_data.get("services", {}).get("liferay", {})
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

        # Save metadata
        meta = {
            "name": name,
            "timestamp": timestamp,
            "tag": project_meta.get("tag"),
            "db_type": project_meta.get("db_type"),
            "host_name": project_meta.get("host_name"),
            "search_snapshot": search_snapshot_name,
            "custom_env": json.dumps(custom_env_dict) if custom_env_dict else None,
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

    def cmd_restore(self, project_id=None, auto_index=None, backup_dir=None):
        root_path = self.manager.detect_project_path(project_id, for_init=True)
        if not root_path:
            return
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
            self.manager.runtime.sync_stack(
                paths, project_meta, no_up=True, show_summary=False
            )

        # 2. Reset the Environment (Clean Slate)
        container_name = (
            project_meta.get("liferay_container_name")
            or project_meta.get("container_name")
            or paths["root"].name.replace(".", "-")
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
        elif volume_tgz.exists() or (choice_path / "volume").is_dir():
            UI.info("  + Restoring cloud data volume...")
            target_data = paths["data"]
            from ldm_core.utils import safe_mkdir

            safe_mkdir(target_data, parents=True, exist_ok=True)

            if volume_tgz.exists():
                self.manager.run_command(
                    ["tar", "-xzf", str(volume_tgz), "-C", str(target_data)]
                )
            # LDM-408/422: Direct Volume Hydration (Performance & Reliability Win)
            # On macOS (Named Volumes), we hydrate directly from the snapshot source
            # to avoid hypervisor race conditions when copying via the host.
            elif self.manager.composer.is_using_named_volumes():
                UI.info("  + Hydrating Named Volumes directly from snapshot...")
                self._sync_volume(
                    choice_path / "volume",
                    f"{paths['root'].name}-data",
                    direction="to_volume",
                )
                # We still populate the host folder for developer visibility
                if target_data.exists():
                    from ldm_core.utils import safe_rmtree

                    safe_rmtree(target_data)
                import shutil

                shutil.copytree(str(choice_path / "volume"), str(target_data))
            else:
                # Standard host-mapped hydration
                import shutil

                from ldm_core.utils import safe_rmtree

                if target_data.exists():
                    safe_rmtree(target_data)
                shutil.copytree(str(choice_path / "volume"), str(target_data))

            UI.success("Cloud volume restoration completed.")
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

        # --- DATABASE RESTORE (Orchestrated) ---
        sql_file = choice_path / "database.sql"
        db_gz = choice_path / "database.gz"

        # If cloud database dump exists but hasn't been extracted yet
        if db_gz.exists() and not sql_file.exists():
            UI.info("  + Decompressing cloud database dump...")
            import gzip
            import shutil

            try:
                with gzip.open(str(db_gz), "rb") as f_in:
                    with open(str(sql_file), "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                UI.success("Cloud database dump decompressed.")
            except Exception as e:
                UI.warning(f"Failed to decompress {db_gz.name}: {e}")

        if sql_file.exists():
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

            db_type = project_meta.get("db_type", "hypersonic")
            UI.info(f"Triggering orchestrated database restore ({db_type})...")

            # 1. Ensure DB container is running, but Liferay is STOPPED
            # This is critical to prevent Liferay from locking the database or
            # attempting to initialize schemas during the restore process.
            self.manager.runtime.cmd_stop(project_id, service="liferay")

            db_container = project_meta.get("db_container_name")
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
                from ldm_core.utils import get_compose_cmd

                compose_base = get_compose_cmd()
                if compose_base:
                    UI.info("  + Starting database container for restore...")
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

        UI.success("Restore complete.")

        # LDM-422: Flag for one-time automatic reindex on next boot
        # Liferay won't automatically reindex imported databases. We set a metadata
        # flag that tells the composer to inject index.on.startup=true for the next run.
        try:
            p_meta = self.manager.read_meta(paths["root"])
            p_meta["reindex_required"] = "true"
            self.manager.write_meta(paths["root"], p_meta)
            UI.info("  + Scheduled automatic search reindex for next boot.")
        except Exception as e:
            UI.warning(f"Could not schedule automatic reindex: {e}")

        # --- OPTIONAL STARTUP (LDM-388) ---
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

        no_osgi = getattr(self.manager.args, "no_osgi_seed", False)

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
                es_infra_backup = (
                    get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                )
                from ldm_core.utils import safe_mkdir

                safe_mkdir(es_infra_backup, parents=True, exist_ok=True)
                es_infra_root = es_infra_backup.resolve()

                # Reclaim permissions before extracting (Fixes [Errno 13] in CI/Linux)
                from ldm_core.utils import reclaim_volume_permissions

                reclaim_volume_permissions(es_infra_backup)

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
        return True

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
            self.manager.run_command(cmd, check=False)
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

    def _execute_orchestrated_db_restore(
        self, db_container, db_type, sql_file, paths, project_meta
    ):
        """Internal helper to execute a robust SQL import into a running DB container."""
        import platform
        import subprocess

        def _wipe_db():
            # 1. Clean Slate (LDM-410)
            # Cloud dumps often lack DROP TABLE commands. We must wipe the target DB first.
            if db_type == "postgresql":
                UI.info("  - Wiping existing PostgreSQL database schema...")
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
                                "lportal",
                            ],
                            input=wipe_script.encode("utf-8"),
                            check=True,
                            capture_output=True,
                        )
                        wipe_success = True
                        break
                    except subprocess.CalledProcessError as e:
                        err_out = (
                            e.stderr.decode(errors="ignore") if e.stderr else str(e)
                        )
                        if (
                            "shutting down" in err_out.lower()
                            or "starting up" in err_out.lower()
                            or "does not exist" in err_out.lower()
                        ):
                            UI.debug(f"DB initializing, waiting... ({err_out.strip()})")
                            time.sleep(5)
                        else:
                            UI.warning(f"  ! Non-fatal wipe error: {err_out}")
                            break  # Other SQL error, stop retrying
                    except Exception as e:
                        UI.warning(f"  ! Wipe encountered an error: {e}")
                        break

                if not wipe_success:
                    UI.warning(
                        "  ! Could not confirm successful schema wipe. Restore may fail."
                    )

            elif db_type in ["mysql", "mariadb"]:
                UI.info("  - Wiping existing MySQL database...")
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
                        "DROP DATABASE IF EXISTS lportal; CREATE DATABASE lportal;",
                    ],
                    check=False,
                )

        # 2. Build Import Command
        import_cmd_str = ""
        if db_type == "postgresql":
            # LDM-410: Use standard user and enforce error stopping for reliability
            import_cmd_str = f'docker exec -i {db_container} psql -U lportal -d lportal -v ON_ERROR_STOP=1 < "{sql_file}"'
        elif db_type in ["mysql", "mariadb"]:
            import_cmd_str = f'docker exec -i {db_container} mysql -u lportal -ptest lportal < "{sql_file}"'

        # 3. Execute with Retry
        if import_cmd_str:
            success = False
            for i in range(3):  # Retry up to 3 times for flaky Docker IO
                # LDM-422: We MUST wipe the DB at the start of EVERY retry attempt.
                # If attempt 1 fails halfway through, the tables are created. Attempt 2 will instantly
                # fail with "relation already exists" unless the schema is dropped again.
                _wipe_db()
                try:
                    # LDM-419: Use shell=True and OS-level redirection (<) instead of Python's stdin buffering.
                    # Python's subprocess.run(stdin=f) truncates massive 50MB+ SQL files across the Docker boundary,
                    # causing "Broken pipe" and incomplete schema restorations.
                    subprocess.run(
                        import_cmd_str,
                        shell=True,
                        check=True,
                        capture_output=True,
                        executable="/bin/bash"
                        if platform.system() != "Windows"
                        else None,
                    )  # nosec B602 B604
                    success = True
                    break
                except subprocess.CalledProcessError as e:
                    err_out = e.stderr.decode(errors="ignore") if e.stderr else str(e)
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

            if success:
                UI.success("  + Database restored successfully.")

                # LDM-410: Auto-update virtualhost to match local hostname
                host_name = project_meta.get("host_name", "localhost")
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
                            "lportal",
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
                            f"UPDATE lportal.virtualhost SET hostname = '{host_name}';",  # nosec B608
                        ],
                        check=False,
                    )
