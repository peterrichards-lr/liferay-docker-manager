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
            "files": root / "files",
        }

    def _ensure_seeded(self, *args, **kwargs):
        return False

    def write_meta(self, *args, **kwargs):
        pass

    def _is_ssl_active(self, *args, **kwargs):
        return False

    def _ensure_network(self, *args, **kwargs):
        pass

    def setup_infrastructure(self, *args, **kwargs):
        pass

    def write_docker_compose(self, *args, **kwargs):
        pass


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

    @patch("socket.gethostbyname")
    def test_cmd_run_seeding_persistence(self, mock_gethost):
        mock_gethost.return_value = "127.0.0.1"
        # Case: New project initialization with seeding
        root = Path("test-project")
        all_paths = {
            "root": root,
            "data": root / "data",
            "deploy": root / "deploy",
            "files": root / "files",
            "state": root / "osgi" / "state",
            "cx": root / "osgi" / "client-extensions",
            "configs": root / "osgi" / "configs",
            "modules": root / "osgi" / "modules",
            "backups": root / "snapshots",
            "portal_log4j": root / "osgi" / "log4j",
            "common": Path("/tmp/common"),
        }

        with (
            patch.object(self.runtime, "detect_project_path") as mock_detect,
            patch.object(self.runtime, "setup_paths") as mock_setup,
            patch.object(self.runtime, "read_meta") as mock_read,
            patch.object(self.runtime, "_ensure_seeded") as mock_seed,
            patch.object(self.runtime, "write_meta") as mock_write,
            patch.object(self.runtime, "verify_runtime_environment"),
            patch.object(self.runtime, "run_command"),
        ):
            mock_detect.return_value = root
            mock_setup.return_value = all_paths
            mock_read.return_value = {
                "host_name": "localhost",
                "container_name": "test-project",
            }
            mock_seed.return_value = True  # Seed successfully downloaded

            # Force no_up to avoid full stack sync
            self.runtime.args.no_up = True
            self.runtime.args.host_name = None
            self.runtime.args.tag = "2026.q1.4-lts"
            self.runtime.args.samples = False

            self.runtime.cmd_run("test-project")

            # Verify that write_meta was called with the seeded status
            self.assertTrue(mock_write.called)
            written_meta = mock_write.call_args[0][1]
            self.assertEqual(str(written_meta.get("seeded")).lower(), "true")
            self.assertIn("seed_version", written_meta)


if __name__ == "__main__":
    unittest.main()
