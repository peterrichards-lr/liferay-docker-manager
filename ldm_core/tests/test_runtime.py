import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.runtime import RuntimeHandler
from ldm_core.handlers.base import BaseHandler


class MockRuntime(RuntimeHandler, BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

    def detect_project_path(self, *args, **kwargs):
        return Path("/tmp/runtime-project")

    def read_meta(self, *args, **kwargs):
        return {"container_name": "test-runtime", "host_name": "localhost"}

    def setup_paths(self, root):
        return {
            "root": root,
            "compose": root / "docker-compose.yml",
            "logs": root / "logs",
        }


class TestRuntime(unittest.TestCase):
    def setUp(self):
        self.runtime = MockRuntime()
        self.tmp_dir = Path("/tmp/runtime-project")

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    def test_cmd_stop_basic(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(self.runtime, "run_command") as mock_run:
            self.runtime.cmd_stop("test")
            # Verify stop command was issued
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            self.assertIn("stop", call_args)

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    def test_cmd_restart_basic(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(self.runtime, "run_command") as mock_run:
            self.runtime.cmd_restart("test")
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            self.assertIn("restart", call_args)

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    @patch("ldm_core.handlers.runtime.shutil.rmtree")
    def test_cmd_down_with_delete(self, mock_rmtree, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with (
            patch.object(self.runtime, "run_command"),
            patch.object(Path, "exists", return_value=True),
        ):
            self.runtime.cmd_down("test", delete=True)
            # Verify down command AND directory deletion
            self.assertTrue(mock_rmtree.called)

    @patch("ldm_core.handlers.runtime.datetime")
    @patch("time.sleep")
    def test_wait_for_ready_timeout(self, mock_sleep, mock_datetime):
        # Mock run_command to always return "starting"
        with patch.object(self.runtime, "run_command", return_value="starting"):
            # Mock time.time to simulate timeout quickly
            with patch("time.time") as mock_time:
                mock_time.side_effect = [
                    0,
                    700,
                ]  # Start at 0, next call at 700 (> 600 timeout)
                result = self.runtime._wait_for_ready({}, "localhost")
                self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
