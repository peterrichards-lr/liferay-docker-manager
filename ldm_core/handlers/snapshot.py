import os
import json
import time
import tarfile
import gzip
import lzma
from pathlib import Path
from datetime import datetime
from ldm_core.ui import UI
from ldm_core.constants import PROJECT_META_FILE, META_VERSION
from ldm_core.utils import run_command


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
                meta = self.read_meta(b / "meta")
                size_bytes = sum(f.stat().st_size for f in b.glob("*") if f.is_file())
                size = UI.format_size(size_bytes)
                print(f"[{i + 1}] {meta.get('name', '(unnamed)')[:18]} - {size}")
        return backups

    def cmd_snapshot(self, project_id=None):
        root_path = self.detect_project_path(project_id)
        if not root_path:
            return
        paths = self.setup_paths(root_path)
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
                if "postgresql" in url.lower():
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
                    host = self.args.my_host or "localhost"
                    port = self.args.my_port or "3306"
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
                                f"-p{pw}",
                                "-e",
                                "SELECT 1",
                            ],
                            check=False,
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
                run_command(
                    ["docker", "compose", "stop"], check=True, cwd=str(paths["root"])
                )
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
            else:
                UI.warning(
                    "Search snapshot failed or timed out. Project snapshot will proceed without it."
                )
                search_snapshot_name = None

        # --- ARCHIVE ---
        snap_dir = paths["backups"] / timestamp
        snap_dir.mkdir(parents=True)

        with tarfile.open(snap_dir / "files.tar.gz", "w:gz") as tar:
            for f in ["files", "scripts", "osgi", "data", "deploy", "routes"]:
                if (paths["root"] / f).exists():
                    tar.add(paths["root"] / f, arcname=f)

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

    def cmd_restore(self, project_id=None):
        root_path = self.detect_project_path(project_id)
        if not root_path:
            return
        paths = self.setup_paths(root_path)
        backups = self.cmd_snapshots(paths)
        if not backups:
            UI.die("No snapshots available.")

        if getattr(self.args, "index", None):
            choice = backups[self.args.index - 1]
        else:
            choice = backups[int(UI.ask("Select snapshot index", "1")) - 1]

        project_meta = self.read_meta(paths["root"] / PROJECT_META_FILE)
        container_name = project_meta.get("container_name") or paths[
            "root"
        ].name.replace(".", "-")
        if run_command(["docker", "ps", "-q", "-f", f"name=^{container_name}$"]):
            run_command(
                ["docker", "compose", "stop"], check=True, cwd=str(paths["root"])
            )
            time.sleep(2)

        with tarfile.open(choice / "files.tar.gz", "r:gz") as tar:
            self.safe_extract(tar, paths["root"])

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
            UI.error("Global search service is not running.")
            return
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
