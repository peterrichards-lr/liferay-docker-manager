import json
import time

from ldm_core.ui import UI


class SearchSnapshotService:
    def __init__(self, facade):
        self.facade = facade
        self.manager = facade.manager
        self.args = facade.manager.args

    def _resolve_docker_prefix(self, project_meta=None):
        """Resolves the docker CLI prefix for the project's active target.

        Shared infra containers (e.g. `liferay-search-global`) live on
        whichever node the project resolves to -- see
        docs/explanation/remote-node-architecture.md.
        """
        target_name = getattr(self.manager, "target", None) or (
            project_meta.get("target") if isinstance(project_meta, dict) else None
        )
        from ldm_core.docker_service import DockerService

        return DockerService.get_docker_cmd_prefix(target_name)

    def _snapshot_search(self, project_meta, root, timestamp, container_name):
        search_snapshot_name = None
        search_name = "liferay-search-global"
        docker_prefix = self._resolve_docker_prefix(project_meta)

        if str(project_meta.get("use_shared_search", "false")).lower() == "true":
            if self.manager.run_command(
                [*docker_prefix, "ps", "-q", "-f", f"name={search_name}"]
            ):
                # LDM-#1355: Elasticsearch requires snapshot names to be
                # lowercase and rejects anything else outright:
                #
                #   invalid_snapshot_name_exception: Invalid snapshot name
                #   [Saarbruecken_20260826_120000], must be lowercase
                #
                # Unlike the index prefix -- which Liferay lowercases for us in
                # CompanyIdIndexNameBuilder.setIndexNamePrefix -- nothing
                # normalises this, so every capitalised project silently failed
                # to snapshot its search state.
                search_snapshot_name = f"{container_name}_{timestamp}".lower()
                UI.detail(
                    f"Triggering orchestrated search snapshot: {search_snapshot_name}..."
                )
                # LDM-#1355: the response is now inspected. `curl -s` with no
                # --fail and an unchecked return meant Elasticsearch's rejection
                # was swallowed and the name was still handed back to the caller,
                # which records it in meta as `search_snapshot`. A restore then
                # looked for a snapshot that had never been created. A snapshot
                # that was not taken must not be reported as taken.
                response = self.manager.run_command(
                    [
                        *docker_prefix,
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
                    ],
                    check=False,
                )
                if not self._snapshot_accepted(response):
                    UI.warning(
                        "Elasticsearch refused the search snapshot "
                        f"'{search_snapshot_name}'; search state will NOT be "
                        "included in this snapshot."
                    )
                    if response:
                        UI.detail(f"Elasticsearch said: {str(response).strip()}")
                    return None
        return search_snapshot_name

    @staticmethod
    def _snapshot_accepted(response):
        """True when Elasticsearch acknowledged the snapshot request (LDM-#1355).

        A missing response is treated as accepted: `docker exec ... curl` can
        legitimately return nothing, and this must not turn a working snapshot
        into a reported failure. Only an explicit error payload counts as a
        refusal.
        """
        if not response:
            return True

        text = str(response)
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            # Not JSON -- fall back to looking for the error shape in the text.
            lowered = text.lower()
            return not ('"error"' in lowered or "exception" in lowered)

        return not (isinstance(payload, dict) and payload.get("error"))

    def _restore_search(self, choice_path, meta, container_name):
        search_snapshot_name = meta.get("search_snapshot")
        search_name = "liferay-search-global"
        docker_prefix = self._resolve_docker_prefix(meta)

        if search_snapshot_name and search_snapshot_name != "None":
            if self.manager.run_command(
                [*docker_prefix, "ps", "-q", "-f", f"name={search_name}"]
            ):
                UI.detail(
                    f"Triggering orchestrated search restore: {search_snapshot_name}..."
                )

                self._delete_project_indices(container_name, docker_prefix)

                self.manager.run_command(
                    [
                        *docker_prefix,
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

                if self._wait_for_search_restore(
                    search_snapshot_name, container_name, docker_prefix=docker_prefix
                ):
                    UI.success("Search restore completed.")
                else:
                    UI.warning(
                        "Search restore timed out or might be still in progress. Verify index status later."
                    )
            else:
                UI.error(
                    "Global search service not running. Could not restore search indices."
                )

    def _wait_for_search_snapshot(self, snapshot_name, timeout=120, docker_prefix=None):
        docker_prefix = docker_prefix or self._resolve_docker_prefix()
        search_name = "liferay-search-global"
        start_time = time.time()
        while time.time() - start_time < timeout:
            res = self.manager.run_command(
                [
                    *docker_prefix,
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

    def _wait_for_search_restore(
        self, snapshot_name, container_name, timeout=60, docker_prefix=None
    ):
        docker_prefix = docker_prefix or self._resolve_docker_prefix()
        search_name = "liferay-search-global"
        start_time = time.time()
        while time.time() - start_time < timeout:
            res = self.manager.run_command(
                [
                    *docker_prefix,
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

    def _delete_project_indices(self, container_name, docker_prefix=None):
        docker_prefix = docker_prefix or self._resolve_docker_prefix()
        search_name = "liferay-search-global"
        self.manager.run_command(
            [
                *docker_prefix,
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
