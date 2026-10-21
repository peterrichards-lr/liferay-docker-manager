"""The search repair does not delete data over a missing plugin (LDM-#2284).

A recreated `liferay-search-global` without `analysis-icu` dies at boot on any
index whose mappings use `icu_collation_keyword`. The repair treated that like
corruption and deleted the shared data -- every project's index -- although a
wipe cannot fix a missing plugin. Observed on v2.26.7-pre.3 verification.

Driven through the real `setup_global_search` with a container that never
becomes ready; only the Docker calls are answered.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.tests.test_stack import MockManager

MAPPER_FAILURE = (
    '{"log.level":"ERROR", "message":"fatal exception while booting '
    'Elasticsearch", "error.message":"Failed to parse mappings for index '
    '[[ldm-devcon2026-1/x]]", "error.stack_trace":"... No mapper found for '
    'type [icu_collation_keyword] ..."}'
)
OTHER_FAILURE = '{"log.level":"ERROR", "message":"fatal exception: disk I/O"}'


class _NeverReady(unittest.TestCase):
    def _run(self, logs):
        home = Path(self._td.name)
        data = home / ".ldm" / "infra" / "search" / "data"
        data.mkdir(parents=True)
        (data / "sentinel").write_text("an index")

        def respond(cmd, *_a, **_k):
            text = " ".join(str(part) for part in cmd)
            if (
                text.endswith("logs --tail 500 liferay-search-global")
                or " logs " in f" {text} "
            ):
                return logs
            if "inspect" in text:
                return "exited"
            if "ps -a" in text or "ps -q" in text:
                return ""
            return "OK"

        manager = MockManager()
        with (
            patch("ldm_core.handlers.infra.get_actual_home", return_value=home),
            patch("ldm_core.utils.check_port", return_value=False),
            patch("time.sleep"),
            patch.object(manager, "run_command", side_effect=respond),
            patch.object(
                manager, "get_container_status", return_value="exited", create=True
            ),
            patch.object(manager.infra, "scan_running_projects", return_value=[]),
        ):
            try:
                manager.infra.setup_global_search()
            except SystemExit as exc:
                return exc.code, (data / "sentinel").exists()
        return None, (data / "sentinel").exists()

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._td.cleanup()


class AMissingPluginIsNotWiped(_NeverReady):
    def test_the_data_survives_and_the_run_stops(self):
        code, survived = self._run(MAPPER_FAILURE)
        self.assertEqual(code, 3)
        self.assertTrue(survived, "the repair deleted the data over a missing plugin")


class OtherFailuresStillRepair(_NeverReady):
    def test_control_an_unrelated_failure_is_still_repaired(self):
        """Control: the guard is narrow; the repair still runs otherwise."""
        _, survived = self._run(OTHER_FAILURE)
        self.assertFalse(survived)


if __name__ == "__main__":
    unittest.main()
