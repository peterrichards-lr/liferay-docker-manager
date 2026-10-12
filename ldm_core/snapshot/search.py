import json
import time

from ldm_core.ui import UI
from ldm_core.utils import search_index_prefix


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
                        json.dumps(
                            {"indices": f"{search_index_prefix(container_name)}*"}
                        ),
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

        search_running = bool(
            self.manager.run_command(
                [*docker_prefix, "ps", "-q", "-f", f"name={search_name}"]
            )
        )

        # LDM-#2119: clearing the project's indices is a property of RESTORING,
        # not of having a search snapshot to put back.
        #
        # This used to sit inside the branch below, so a snapshot taken while
        # search was a sidecar, taken before search existed, or whose search
        # snapshot failed, skipped it entirely. The database was replaced and
        # the previous build's documents survived beside it -- 189 orphaned
        # object-entry documents on the reported run.
        #
        # The reindex scheduled by `handlers/snapshot.py` is what made this
        # look handled. It is not the same thing: a reindex re-adds documents
        # for classes that still have indexers, and an orphaned document is
        # precisely one whose class no longer has one.
        if search_running:
            self._delete_project_indices(container_name, docker_prefix)
        elif search_snapshot_name and search_snapshot_name != "None":
            UI.error(
                "Global search service not running. Could not restore search indices."
            )
            return
        else:
            # Nothing to restore and nothing reachable to clear. Say so rather
            # than returning silently: the indices, wherever they are, still
            # hold the previous build's documents.
            UI.warning(
                "Global search service not running; this project's existing "
                "indices were not cleared. Documents from the previous build "
                "may survive this restore."
            )
            return

        if not (search_snapshot_name and search_snapshot_name != "None"):
            # Indices cleared, nothing to put back. The reindex scheduled by
            # `handlers/snapshot.py` rebuilds what Liferay can index, which is
            # now the whole of it rather than a layer over stale documents.
            return

        UI.detail(f"Triggering orchestrated search restore: {search_snapshot_name}...")

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
                        "indices": f"{search_index_prefix(container_name)}*",
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
                    # LDM-#2119: the same wrong pattern `_delete_project_indices`
                    # carried. Liferay's indices are `ldm-<name>-*`, so
                    # `<container_name>-*` matched nothing and this loop could
                    # only ever time out into "Search restore timed out".
                    f"localhost:9200/{search_index_prefix(container_name)}*/_recovery",
                ],
                check=False,
            )
            # If no indices are currently recovering, we assume they are all restored or failed
            if res and '"stage":"DONE"' in res and '"stage":"INDEX"' not in res:
                return True
            time.sleep(5)
        return False

    def _project_index_names(self, container_name, docker_prefix=None):
        """The concrete indices this project owns on the shared search node.

        LDM-#2119. Resolved by name, deliberately, because a wildcard cannot be
        deleted: LDM runs `elasticsearch:8.x`, which defaults
        `action.destructive_requires_name` to true and answers a wildcard
        DELETE with 400. Nothing in `handlers/infra.py` sets that to false.

        The pattern comes from `search_index_prefix`, which is the single
        source for what LDM writes into `indexNamePrefix` (`composer.py`) and
        what the snapshot and restore calls in this module already match on.
        `_delete_project_indices` used to build its own -- `<container_name>-*`
        -- with no `ldm-` prefix and no lowercasing, so it named indices that
        do not exist. Both faults were independently sufficient to make the
        delete a no-op, which is why it had never removed an index on either
        branch.
        """
        docker_prefix = docker_prefix or self._resolve_docker_prefix()
        pattern = f"{search_index_prefix(container_name)}*"
        res = self.manager.run_command(
            [
                *docker_prefix,
                "exec",
                "liferay-search-global",
                "curl",
                "-s",
                f"localhost:9200/_cat/indices/{pattern}?h=index",
            ],
            check=False,
        )
        if not res:
            return []

        names = []
        for line in str(res).splitlines():
            name = line.strip()
            # `_cat` answers an unmatched pattern with a JSON error document on
            # some versions and an empty body on others. An index name contains
            # neither whitespace nor a brace, so this rejects the error shape
            # without having to parse it.
            if not name or " " in name or name.startswith(("{", "[")):
                continue
            names.append(name)
        return names

    def _delete_project_indices(self, container_name, docker_prefix=None):
        """Removes this project's search indices. Returns what it removed.

        LDM-#2119. Previously issued a wildcard DELETE with `check=False`, so
        Elasticsearch's 400 was discarded and the caller could not tell a
        successful purge from a rejected one. A restore then proceeded onto
        indices that were still there.
        """
        docker_prefix = docker_prefix or self._resolve_docker_prefix()
        names = self._project_index_names(container_name, docker_prefix)
        if not names:
            return []

        res = self.manager.run_command(
            [
                *docker_prefix,
                "exec",
                "liferay-search-global",
                "curl",
                "-s",
                "-X",
                "DELETE",
                f"localhost:9200/{','.join(names)}",
            ],
            check=False,
        )
        if not self._snapshot_accepted(res):
            UI.warning(
                f"Could not clear {len(names)} search index(es) for "
                f"{container_name}; documents from the previous build may "
                f"survive this restore. Response: {res}"
            )
            return []

        UI.detail(f"  - Cleared {len(names)} stale search index(es).")
        return names
