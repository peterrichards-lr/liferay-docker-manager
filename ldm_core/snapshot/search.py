import json
import time

from ldm_core.ui import UI


class SearchSnapshotService:
    def __init__(self, facade):
        self.facade = facade
        self.manager = facade.manager
        self.args = facade.manager.args

    def _snapshot_search(self, project_meta, root, timestamp, container_name):
        search_snapshot_name = None
        search_name = "liferay-search-global"

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
        return search_snapshot_name

    def _restore_search(self, choice_path, meta, container_name):
        search_snapshot_name = meta.get("search_snapshot")
        search_name = "liferay-search-global"

        if search_snapshot_name and search_snapshot_name != "None":
            if self.manager.run_command(
                ["docker", "ps", "-q", "-f", f"name={search_name}"]
            ):
                UI.info(
                    f"Triggering orchestrated search restore: {search_snapshot_name}..."
                )

                self._delete_project_indices(container_name)

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
