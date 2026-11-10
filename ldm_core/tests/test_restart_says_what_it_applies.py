"""`ldm run` must not send users to `ldm restart` to apply changes (LDM-#2317).

`ldm run` on a running project refused with "Use 'ldm restart' to apply
updates". Restart reuses the existing compose file and never re-renders it from
meta, so a removed `custom_env` key survived it -- while restart printed nothing
and exited 0, so nothing showed the change had not been applied.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.pipelines.run import RuntimeValidationStage
from ldm_core.tests.test_orchestration import MockRuntime
from ldm_core.ui import UI


class TheAlreadyRunningRefusalNamesStopThenRun(unittest.TestCase):
    @patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit(5))
    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    def test_it_does_not_recommend_restart(self, _running, die):
        context = MagicMock()
        context.manager.non_interactive = True
        context.manager.args.no_up = False
        values = {
            "project_meta": {"container_name": "shop"},
            "project_id": "shop",
            "is_restart": False,
            "no_up": False,
        }
        context.get.side_effect = values.get

        with self.assertRaises(SystemExit):
            RuntimeValidationStage().execute(context)

        message = die.call_args[0][0]
        self.assertIn("'ldm stop' and then 'ldm run'", message)
        self.assertNotIn("Use 'ldm restart'", message)
        self.assertEqual(die.call_args.kwargs["exit_code"], 5)


class RestartSaysWhatItDid(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "shop"
        self.root.mkdir()
        self.manager = MockRuntime()
        self.manager.detect_project_path = MagicMock(return_value=self.root)  # type: ignore[method-assign]

    def tearDown(self):
        self.tmp.cleanup()

    def _restart_stdout(self):
        # Real stdout, not a patched UI.success: the defect was a message that
        # existed in the source and never reached the terminal (UI.detail is
        # gated behind --info/--verbose).
        self.assertFalse(UI.INFO_MODE or UI.VERBOSE)
        buffer = io.StringIO()
        with (
            patch.object(self.manager, "read_meta", return_value={}),
            patch.object(BaseHandler, "run_command", return_value="ok"),
            patch("ldm_core.runtime.mac_pin.configured_mac", return_value=None),
            contextlib.redirect_stdout(buffer),
        ):
            self.manager.handler.orchestration.cmd_restart("shop")
        return buffer.getvalue()

    def test_a_successful_restart_says_so(self):
        self.assertIn("Project 'shop' restarted.", self._restart_stdout())

    def test_it_says_meta_changes_need_stop_then_run(self):
        output = self._restart_stdout()
        self.assertIn("reuses the existing docker-compose.yml", output)
        self.assertIn("'ldm stop' and then 'ldm run'", output)


if __name__ == "__main__":
    unittest.main()
