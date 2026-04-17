import os
import json
import time
import tarfile
import gzip
import lzma
import subprocess
from pathlib import Path
from datetime import datetime
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE, META_VERSION
from ldm_core.utils import run_command, get_compose_cmd


class SnapshotHandler:
    """Mixin for snapshot and restore commands."""

    def get_jdbc_params(self, files_dir):
        portal_ext = Path(files_dir) / "portal-ext.properties"
        params = {}
        if portal_ext.exists():
            with open(portal_ext, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        params[k.strip()] = v.strip()
        return params

    def verify_archive(self, file_path):
        try:
            if file_path.suffix == ".gz":
                with gzip.open(file_path, "rb") as f:
                    while f.read(1024 * 1024):
                        pass
            elif file_path.suffix == ".xz":
                with lzma.open(file_path, "rb") as f:
                    while f.read(1024 * 1024):
                        pass
            if ".tar" in file_path.name or file_path.suffix in [".tgz", ".tar"]:
                with tarfile.open(file_path, "r:*") as tar:
                    tar.getmembers()
            return True
        except Exception as e:
            UI.error(f"Integrity check failed: {e}")
            return False

    def cmd_snapshots(self, paths=None, project_id=None):
        if not paths:
            root_path = self.detect_project_path(project_id)
            if not root_path:
                return []
            paths = self.setup_paths(root_path)
        if not paths["backups"].exists():
            return []
        backups = sorted(
            [d for d in paths["backups"].iterdir() if d.is_dir()], reverse=True
        )
        if backups:
            UI.heading(f"Snapshots in {paths['backups']}")
            for i, b in enumerate(backups):
                meta_file = b / "meta"
                if meta_file.exists():
                    meta = self.read_meta(meta_file)
                    size_bytes = sum(
                        f.stat().st_size for f in b.glob("*") if f.is_file()
                    )
                    size = UI.format_size(size_bytes)
                    print(f"[{i + 1}] {meta.get('name', '(unnamed)')[:18]} - {size}")
                else:
                    # Possibly a cloud backup directory
                    print(f"[{i + 1}] {b.name} (Cloud Backup)")
        return backups

    def _restore_from_cloud_layout(self, backup_dir, paths, project_meta):
        """Restores a project from a Liferay Cloud-style backup (database.gz + volume.tgz)."""
        compose_base = get_compose_cmd()
        if not compose_base:
            UI.die(
                "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
            )

        backup_dir = Path(backup_dir).resolve()
        if not backup_dir.exists():
            UI.die(f"Backup directory not found: {backup_dir}")

        restored_anything = False
        vol = backup_dir / "volume.tgz"
        if vol.exists():
            UI.info("Restoring volume...")
            from ldm_core.utils import safe_extract

            with tarfile.open(vol, "r:gz") as tar:
                safe_extract(tar, paths["data"])
            restored_anything = True

        db_dump = backup_dir / "database.gz"
        if db_dump.exists():
            UI.info("Detecting database dialect from dump...")
            db_type = None
            try:
                with gzip.open(db_dump, "rt", encoding="utf-8", errors="ignore") as f:
                    head = "".join([f.readline() for _ in range(50)])
                    if "PostgreSQL" in head:
                        db_type = "postgresql"
                    elif "MySQL" in head or "MariaDB" in head:
                        db_type = "mysql"
            except Exception:
                pass

            if db_type:
                UI.info(f"Detected {db_type} dump. Preparing orchestrated restore...")
                project_meta["db_type"] = db_type
                project_meta["db_name"] = "lportal"
                project_meta["db_user"] = "liferay"
                # Bandit: B105 (password string) is safe here as 'liferay' is the
                # standard default for local developer instances.
                project_meta["db_pass"] = "liferay"  # nosec B105
                self.write_meta(paths["root"] / PROJECT_META_FILE, project_meta)

                # Ensure stack is synced for the correct DB type
                if hasattr(self, "sync_stack"):
                    self.sync_stack(paths, project_meta, show_summary=False, no_up=True)

                db_container = f"{project_meta['container_name']}-db"

                UI.info(f"Starting database container: {db_container}...")
                run_command(
                    compose_base + ["up", "-d", "db"],
                    cwd=str(paths["root"]),
                )

                UI.info("Waiting for database service to become ready...")
                start_time, db_ready = time.time(), False
                while time.time() - start_time < 60:
                    status = run_command(
                        [
                            "docker",
                            "inspect",
                            "-f",
                            "{{.State.Health.Status}}",
                            db_container,
                        ],
                        check=False,
                    )
                    if status == "healthy":
                        db_ready = True
                        break

                    if db_type == "postgresql":
                        res = run_command(
                            [
                                "docker",
                                "exec",
                                db_container,
                                "pg_isready",
                                "-U",
                                "liferay",
                                "-d",
                                "lportal",
                            ],
                            check=False,
                        )
                        if res and "accepting connections" in res:
                            db_ready = True
                            break
                    else:
                        res = run_command(
                            [
                                "docker",
                                "exec",
                                db_container,
                                "mysqladmin",
                                "ping",
                                "-h",
                                "localhost",
                                "-u",
                                "liferay",
                                "-pliferay",
                            ],
                            check=False,
                        )
                        if res and "mysqld is alive" in res:
                            db_ready = True
                            break
                    time.sleep(2)

                if db_ready:
                    if db_type == "postgresql":
                        UI.info("Creating compatibility roles for Cloud restoration...")
                        run_command(
                            [
                                "docker",
                                "exec",
                                db_container,
                                "psql",
                                "-U",
                                "liferay",
                                "-d",
                                "lportal",
                                "-c",
                                "CREATE ROLE cloudsqlsuperuser;",
                            ],
                            check=False,
                        )

                    UI.info("Streaming database dump into container...")
                    try:
                        # Stream the decompressed database dump directly into the container without shell=True
                        # This resolves potential security alerts for command injection.

                        # 1. Start decompression process
                        gunzip_proc = subprocess.Popen(
                            ["gunzip", "-c", str(db_dump)],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )

                        # 2. Start database import process
                        db_cmd = (
                            [
                                "docker",
                                "exec",
                                "-i",
                                db_container,
                                "psql",
                                "-U",
                                "liferay",
                                "-d",
                                "lportal",
                            ]
                            if db_type == "postgresql"
                            else [
                                "docker",
                                "exec",
                                "-i",
                                "-e",
                                "MYSQL_PWD=liferay",
                                db_container,
                                "mysql",
                                "-u",
                                "liferay",
                                "lportal",
                            ]
                        )

                        import_env = os.environ.copy()
                        if db_type == "postgresql":
                            import_env["PGPASSWORD"] = "liferay"

                        import_proc = subprocess.Popen(
                            db_cmd,
                            stdin=gunzip_proc.stdout,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            env=import_env,
                        )

                        # Allow gunzip_proc to receive a SIGPIPE if import_proc exits.
                        if gunzip_proc.stdout:
                            gunzip_proc.stdout.close()

                        stdout, stderr = import_proc.communicate()

                        if import_proc.returncode == 0:
                            UI.success("Database restored.")
                            restored_anything = True
                        else:
                            UI.error(
                                f"Database restore failed (Exit {import_proc.returncode})",
                                stderr,
                            )
                    except Exception as e:
                        UI.error(f"Database restore failed: {e}")
                else:
                    UI.error(
                        "Database container failed to become ready. Restore skipped."
                    )
            else:
                UI.warning("Could not determine database dialect. Restore skipped.")

        return restored_anything

    def cmd_snapshot(self, project_id=None):
        root_path = self.detect_project_path(project_id)
        if not root_path:
            return
        paths = self.setup_paths(root_path)

        # Ensure directories exist and permissions are synchronized (Fixes CI [Errno 13])
        self.verify_runtime_environment(paths)

        project_meta = self.read_meta(paths["root"] / PROJECT_META_FILE)
        container_name = project_meta.get("container_name") or paths[
            "root"
        ].name.replace(".", "-")

        if not getattr(self.args, "files_only", False):
            jdbc = self.get_jdbc_params(paths["files"])
            url = jdbc.get("jdbc.default.url")
            if url:
                user, pw = (
                    jdbc.get("jdbc.default.username", ""),
                    jdbc.get("jdbc.default.password", ""),
                )
                db_container = f"{container_name}-db"
                db_running = run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{db_container}$"], check=False
                )

                if "postgresql" in url.lower():
                    if db_running:
                        # Use docker exec for reachability check (CI friendly)
                        if (
                            run_command(
                                [
                                    "docker",
                                    "exec",
                                    db_container,
                                    "pg_isready",
                                    "-U",
                                    user,
                                    "-d",
                                    "postgres",
                                ],
                                check=False,
                            )
                            is None
                        ):
                            UI.die(
                                f"PostgreSQL container '{db_container}' is not accepting connections."
                            )
                    else:
                        # Fallback to host-side check (Local dev)
                        host = self.args.pg_host or "localhost"
                        port = self.args.pg_port or "5432"
                        env = os.environ.copy()
                        env["PGPASSWORD"] = pw
                        if (
                            run_command(
                                [
                                    "psql",
                                    "-h",
                                    host,
                                    "-p",
                                    port,
                                    "-U",
                                    user,
                                    "-d",
                                    "postgres",
                                    "-c",
                                    "SELECT 1",
                                ],
                                check=False,
                                env=env,
                            )
                            is None
                        ):
                            UI.die(f"PostgreSQL not reachable on {host}:{port}.")
                elif "mysql" in url.lower():
                    if db_running:
                        # Use docker exec for reachability check (CI friendly)
                        if (
                            run_command(
                                [
                                    "docker",
                                    "exec",
                                    "-e",
                                    f"MYSQL_PWD={pw}",
                                    db_container,
                                    "mysqladmin",
                                    "ping",
                                    "-u",
                                    user,
                                ],
                                check=False,
                            )
                            is None
                        ):
                            UI.die(
                                f"MySQL container '{db_container}' is not accepting connections."
                            )
                    else:
                        # Fallback to host-side check (Local dev)
                        host = self.args.my_host or "localhost"
                        port = self.args.my_port or "3306"
                        env = os.environ.copy()
                        env["MYSQL_PWD"] = pw
                        if (
                            run_command(
                                [
                                    "mysql",
                                    "-h",
                                    host,
                                    "-P",
                                    port,
                                    "-u",
                                    user,
                                    "-e",
                                    "SELECT 1",
                                ],
                                check=False,
                                env=env,
                            )
                            is None
                        ):
                            UI.die(f"MySQL not reachable on {host}:{port}.")

        is_running = run_command(
            ["docker", "ps", "-q", "-f", f"name=^{container_name}$"]
        )
        if is_running and not getattr(self.args, "no_stop", False):
            if (
                not self.non_interactive
                and UI.ask("Stop stack during backup?", "Y").upper() == "Y"
            ):
                compose_base = get_compose_cmd()
                if not compose_base:
                    UI.die(
                        "Docker Compose not found. Please run 'ldm doctor' for installation instructions."
                    )
                run_command(compose_base + ["stop"], check=True, cwd=str(paths["root"]))
                time.sleep(2)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        # --- SEARCH SNAPSHOT (Orchestrated) ---
        search_snapshot_name = None
        search_name = "liferay-search-global"
        use_shared_search = self.parse_version(project_meta.get("tag")) >= (2025, 1, 0)

        if use_shared_search and run_command(
            ["docker", "ps", "-q", "-f", f"name={search_name}"]
        ):
            search_snapshot_name = f"{container_name}-{timestamp}"
            UI.info(
                f"Triggering orchestrated search snapshot: {search_snapshot_name}..."
            )

            # Ensure repository is registered
            self.cmd_search_status(project_id)

            run_command(
                [
                    "docker",
                    "exec",
                    search_name,
                    "curl",
                    "-s",
                    "-X",
                    "PUT",
                    f"localhost:9200/_snapshot/liferay_backup/{search_snapshot_name}",
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

            if self._wait_for_search_snapshot(search_snapshot_name):
                UI.success("Search snapshot completed.")
                # Copy ES snapshot files to the backup dir so they are portable
                from ldm_core.utils import get_actual_home

                es_backup_source = (
                    get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                )
                if es_backup_source.exists():
                    snap_es_dir = paths["backups"] / timestamp / "search"
                    snap_es_dir.mkdir(parents=True, exist_ok=True)
                    # We only need the files related to this snapshot
                    # However, ES snapshots are incremental and shared.
                    # For a truly portable seed, we might need more, but for now
                    # we'll copy the whole backup repo if it's small, or just reference it.
                    # BETTER: For SEEDING, we want a standalone archive.
                    # Let's just include the search folder in the main tar if it's a seed.
            else:
                UI.warning(
                    "Search snapshot failed or timed out. Project snapshot will proceed without it."
                )
                search_snapshot_name = None

        # --- ARCHIVE ---
        snap_dir = paths["backups"] / timestamp

        # Final permission sync before archiving (Fixes late-created Docker file issues)
        self.verify_runtime_environment(paths)

        snap_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(snap_dir / "files.tar.gz", "w:gz") as tar:
            for f in ["files", "scripts", "osgi", "data", "deploy", "routes"]:
                if (paths["root"] / f).exists():
                    tar.add(paths["root"] / f, arcname=f)

            # If we have a search snapshot, bundle the global backup repo into the archive
            if search_snapshot_name:
                from ldm_core.utils import get_actual_home

                es_backup_source = (
                    get_actual_home() / ".ldm" / "infra" / "search" / "backup"
                )
                if es_backup_source.exists():
                    tar.add(es_backup_source, arcname="search_backup")

        self.write_meta(
            snap_dir / "meta",
            {
                "meta_version": META_VERSION,
                "name": self.args.name or "",
                "timestamp": timestamp,
                "container": container_name,
                "search_snapshot": search_snapshot_name or "None",
            },
        )
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
            with tarfile.open(files_tar, "r:gz") as tar:
                from ldm_core.utils import is_within_root

                # 1. Extract standard project files
                target_root = paths["root"].resolve()
                members = []
                for m in tar.getmembers():
                    if m.name.startswith("search_backup"):
                        continue

                    # Security: Validate path to prevent Zip Slip / Path Traversal
                    member_path = (target_root / m.name).resolve()
                    if not is_within_root(member_path, target_root):
                        UI.error(f"Security: Skipping unsafe member: {m.name}")
                        continue
                    members.append(m)

                tar.extractall(path=target_root, members=members)  # nosec B202

                # 2. Extract search_backup if present
                search_member = next(
                    (m for m in tar.getmembers() if m.name == "search_backup"), None
                )
                if search_member:
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
                            member_path = (es_infra_root / rel_name).resolve()

                            if not is_within_root(member_path, es_infra_root):
                                UI.error(
                                    f"Security: Skipping unsafe ES member: {m.name}"
                                )
                                continue

                            # Temporarily adjust member name for extraction into the target dir
                            m.name = rel_name
                            tar.extract(m, path=es_infra_root)  # nosec B202
        else:
            UI.die(f"Standard snapshot files not found in {choice}")

        # --- SEARCH RESTORE (Orchestrated) ---
        snap_meta = self.read_meta(choice / "meta")
        search_snapshot_name = snap_meta.get("search_snapshot")
        search_name = "liferay-search-global"

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

    def _wait_for_search_snapshot(self, snapshot_name, timeout=60):
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
                    f"localhost:9200/_snapshot/liferay_backup/{snapshot_name}",
                ],
                check=False,
            )
            if res:
                try:
                    data = json.loads(res)
                    snaps = data.get("snapshots", [])
                    if snaps:
                        state = snaps[0].get("state")
                        if state == "SUCCESS":
                            return True
                        if state in ["FAILED", "PARTIAL", "INCOMPATIBLE"]:
                            UI.error(
                                f"Search snapshot {snapshot_name} failed with state: {state}"
                            )
                            return False
                except Exception:
                    pass
            time.sleep(2)
        return False

    def _wait_for_search_restore(self, snapshot_name, prefix, timeout=60):
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
                    "localhost:9200/_recovery",
                ],
                check=False,
            )
            if res:
                try:
                    data = json.loads(res)
                    # Recovery is complete when no indices matching the prefix are in the recovery list
                    # or their stages are all 'DONE'
                    active_recoveries = [
                        k for k, v in data.items() if k.startswith(prefix)
                    ]
                    if not active_recoveries:
                        return True

                    all_done = True
                    for idx in active_recoveries:
                        shards = data[idx].get("shards", [])
                        if any(s.get("stage") != "DONE" for s in shards):
                            all_done = False
                            break
                    if all_done:
                        return True
                except Exception:
                    pass
            time.sleep(2)
        return False

    def _delete_project_indices(self, prefix):
        search_name = "liferay-search-global"
        UI.info(f"Clearing existing search indices for prefix '{prefix}'...")
        run_command(
            [
                "docker",
                "exec",
                search_name,
                "curl",
                "-s",
                "-X",
                "DELETE",
                f"localhost:9200/{prefix}*",
            ],
            check=False,
        )

    def cmd_search_status(self, project_id=None):
        search_name = "liferay-search-global"
        if not run_command(["docker", "ps", "-q", "-f", f"name={search_name}"]):
            UI.die("Global search service is not running.")
        UI.heading("Search Snapshot Status")
        repo_check = run_command(
            [
                "docker",
                "exec",
                search_name,
                "curl",
                "-s",
                "localhost:9200/_snapshot/liferay_backup",
            ],
            check=False,
        )
        if not repo_check or '"error"' in repo_check:
            run_command(
                [
                    "docker",
                    "exec",
                    search_name,
                    "curl",
                    "-s",
                    "-X",
                    "PUT",
                    "localhost:9200/_snapshot/liferay_backup",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    '{"type": "fs", "settings": {"location": "backup"}}',
                ]
            )

        snaps_raw = run_command(
            [
                "docker",
                "exec",
                search_name,
                "curl",
                "-s",
                "localhost:9200/_snapshot/liferay_backup/_all",
            ]
        )
        try:
            data = json.loads(snaps_raw)
            snaps = data.get("snapshots", [])
            if not snaps:
                UI.info("No snapshots found.")
                return
            print(f"{'Snapshot':<30} {'State':<12} {'End Time':<20}\n" + "-" * 65)
            for s in snaps[-10:]:
                state = s.get("state", "UNKNOWN")
                if state == "SUCCESS":
                    color = UI.GREEN
                elif state in ["IN_PROGRESS", "PARTIAL"]:
                    color = UI.YELLOW
                else:
                    color = UI.RED

                end_time_ms = s.get("end_time_in_millis", 0)
                ts = datetime.fromtimestamp(end_time_ms / 1000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                print(f"{s.get('snapshot'):<30} {color}{state:<12}{UI.COLOR_OFF} {ts}")
        except Exception as e:
            UI.error(f"Failed to parse snapshot data: {e}")
