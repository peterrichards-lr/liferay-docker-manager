"""A restore must not report success over an index that cannot search (LDM-#2316).

Twice on 2026-11-02 a restored company index came back STARTED, `_recovery`
said DONE, and every `_count` answered 503 (`AlreadyClosedException`). LDM said
"Search restore completed.", and Liferay's reindex then wrote nothing.

The fake below is Elasticsearch AFTER the restore: the project's indices exist
again, and each answers `_count` with the status the test gives it.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.tests.test_restore_clears_stale_search import LIVE_INDICES, PROJECT
from ldm_core.tests.test_snapshot import MockSnapshotManager

COMPANY_INDEX, OTHER_INDEX = LIVE_INDICES


class _RestoredElasticsearch:
    def __init__(self, count_status):
        # index name -> the HTTP status its `_count` answers with; "" means the
        # probe itself produced nothing (docker exec failed, say).
        self.count_status = count_status
        self.counted = []

    def __call__(self, argv, *_args, **_kwargs):
        if not isinstance(argv, list):
            return ""
        if "ps" in argv:
            return "container-id"
        url = next(
            (t for t in argv if isinstance(t, str) and t.startswith("localhost:9200/")),
            "",
        )
        if "_cat/indices" in url:
            return "\n".join(LIVE_INDICES)
        if url.endswith("/_count"):
            name = url[len("localhost:9200/") : -len("/_count")]
            self.counted.append(name)
            return self.count_status.get(name, "200")
        if "_recovery" in url:
            return '{"x":{"shards":[{"stage":"DONE"}]}}'
        return '{"acknowledged":true}'


class ARestoredIndexMustAnswerASearch(unittest.TestCase):
    def setUp(self):
        self.manager = MockSnapshotManager()
        self.test_dir = Path(tempfile.mkdtemp())
        from ldm_core.config import TargetNode

        patcher = patch("ldm_core.docker_service.get_active_target")
        patcher.start().return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _restore(self, count_status):
        es = _RestoredElasticsearch(count_status)
        with (
            patch.object(self.manager, "run_command", side_effect=es),
            patch("time.sleep"),
            patch("ldm_core.snapshot.search.UI.success") as success,
            patch("ldm_core.snapshot.search.UI.warning") as warning,
        ):
            self.manager.snapshot.search._restore_search(
                self.test_dir, {"search_snapshot": "snap1"}, PROJECT
            )
        return es, success, warning

    def test_a_dead_shard_is_reported_not_called_complete(self):
        _, success, warning = self._restore({COMPANY_INDEX: "503"})

        success.assert_not_called()
        message = warning.call_args[0][0]
        self.assertIn(COMPANY_INDEX, message)
        self.assertNotIn(OTHER_INDEX, message)
        self.assertIn("docker restart liferay-search-global", message)

    def test_every_restored_index_is_probed(self):
        es, _, _ = self._restore({})
        self.assertEqual(sorted(es.counted), sorted(LIVE_INDICES))

    def test_healthy_indices_are_reported_complete(self):
        _, success, warning = self._restore({})

        success.assert_called_once_with("Search restore completed.")
        warning.assert_not_called()

    def test_a_probe_that_could_not_run_is_not_a_dead_shard(self):
        # No answer says nothing about the index. Calling it dead would send
        # the user to restart shared search for every project, for nothing.
        _, success, warning = self._restore({COMPANY_INDEX: ""})

        success.assert_called_once_with("Search restore completed.")
        warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
