import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.runtime import RuntimeService


class MockRuntime(BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

        # Self-referential manager for service compatibility
        from typing import Any, cast

        self.manager = cast(Any, self)

        self.assets = MagicMock()
        self.infra = MagicMock()
        self.snapshot = MagicMock()
        from ldm_core.handlers.composer import ComposerService
        from ldm_core.handlers.config import ConfigService

        self.config = ConfigService(self)
        self.composer = ComposerService(self)
        self.handler = RuntimeService(self)

    def cmd_run(self, *args, **kwargs):
        return self.handler.cmd_run(*args, **kwargs)

    def cmd_stop(self, *args, **kwargs):
        return self.handler.cmd_stop(*args, **kwargs)

    def cmd_restart(self, *args, **kwargs):
        return self.handler.cmd_restart(*args, **kwargs)

    def cmd_down(self, *args, **kwargs):
        return self.handler.cmd_down(*args, **kwargs)

    def _wait_for_ready(self, *args, **kwargs):
        return self.handler._wait_for_ready(*args, **kwargs)

    def sync_stack(self, *args, **kwargs):
        return self.handler.sync_stack(*args, **kwargs)

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
        self.handler = MockRuntime()
        self.tmp_dir = Path("/tmp/runtime-project")

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    def test_cmd_stop_basic(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(self.handler, "run_command") as mock_run:
            self.handler.cmd_stop("test")
            # Verify stop command was issued
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            self.assertIn("stop", call_args)

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    def test_cmd_restart_basic(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(self.handler, "run_command") as mock_run:
            self.handler.cmd_restart("test")
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            self.assertIn("restart", call_args)

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    @patch("ldm_core.handlers.runtime.shutil.rmtree")
    def test_cmd_down_with_delete(self, mock_rmtree, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with (
            patch.object(self.handler, "run_command"),
            patch.object(Path, "exists", return_value=True),
        ):
            self.handler.cmd_down("test", delete=True)
            # Verify down command AND directory deletion
            self.assertTrue(mock_rmtree.called)

    @patch("ldm_core.handlers.runtime.datetime")
    @patch("time.sleep")
    def test_wait_for_ready_timeout(self, mock_sleep, mock_datetime):
        # Mock run_command to always return "starting"
        with patch.object(self.handler, "run_command", return_value="starting"):
            # Mock time.time to simulate timeout quickly
            with patch("time.time") as mock_time:
                mock_time.side_effect = [
                    0,
                    700,
                ]  # Start at 0, next call at 700 (> 600 timeout)
                result = self.handler._wait_for_ready({}, "localhost")
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
            "ce_dir": root / "osgi" / "client-extensions",
            "configs": root / "osgi" / "configs",
            "modules": root / "osgi" / "modules",
            "backups": root / "snapshots",
            "portal_log4j": root / "osgi" / "log4j",
            "logs": root / "logs",
            "compose": root / "docker-compose.yml",
            "common": Path("/tmp/common"),
        }

        with (
            patch.object(self.handler, "detect_project_path") as mock_detect,
            patch.object(self.handler, "setup_paths") as mock_setup,
            patch.object(self.handler, "read_meta") as mock_read,
            patch.object(self.handler, "_ensure_seeded") as mock_seed,
            patch.object(self.handler, "write_meta") as mock_write,
            patch.object(self.handler, "verify_runtime_environment"),
            patch.object(self.handler, "run_command"),
        ):
            mock_detect.return_value = root
            mock_setup.return_value = all_paths
            mock_read.return_value = {
                "host_name": "localhost",
                "container_name": "test-project",
            }
            mock_seed.return_value = True  # Seed successfully downloaded

            # Force no_up to avoid full stack sync
            self.handler.args.no_up = True
            self.handler.args.host_name = None
            self.handler.args.tag = "2026.q1.4-lts"
            self.handler.args.samples = False

            self.handler.cmd_run("test-project")

            # Verify that write_meta was called with the seeded status
            self.assertTrue(mock_write.called)
            written_meta = mock_write.call_args[0][1]
            self.assertEqual(str(written_meta.get("seeded")).lower(), "true")
            self.assertIn("seed_version", written_meta)

    @patch("socket.gethostbyname")
    def test_cmd_run_duplicate_orchestration_suppressed(self, mock_gethost):
        mock_gethost.return_value = "127.0.0.1"
        with tempfile.TemporaryDirectory() as tmp_root:
            root = Path(tmp_root)
            (root / "files").mkdir(parents=True, exist_ok=True)
            all_paths = {
                "root": root,
                "data": root / "data",
                "deploy": root / "deploy",
                "files": root / "files",
                "state": root / "osgi" / "state",
                "cx": root / "osgi" / "client-extensions",
                "ce_dir": root / "osgi" / "client-extensions",
                "configs": root / "osgi" / "configs",
                "modules": root / "osgi" / "modules",
                "backups": root / "snapshots",
                "portal_log4j": root / "osgi" / "log4j",
                "logs": root / "logs",
                "compose": root / "docker-compose.yml",
                "common": Path("/tmp/common"),
            }

            with (
                patch.object(self.handler, "detect_project_path", return_value=root),
                patch.object(self.handler, "setup_paths", return_value=all_paths),
                patch.object(
                    self.handler,
                    "read_meta",
                    return_value={
                        "host_name": "samples.local",
                        "container_name": "test-samples",
                        "ssl": "true",
                    },
                ),
                patch.object(self.handler, "write_meta"),
                patch.object(self.handler, "verify_runtime_environment"),
                patch.object(self.handler, "run_command"),
                patch.object(self.handler.handler, "sync_stack") as mock_sync,
                patch("ldm_core.handlers.config.ConfigService.sync_samples"),
                patch(
                    "ldm_core.handlers.config.ConfigService.get_samples_tag",
                    return_value="2025.q3.10",
                ),
                patch(
                    "ldm_core.handlers.config.ConfigService.get_samples_db_type",
                    return_value="postgresql",
                ),
                patch("ldm_core.handlers.snapshot.SnapshotService.cmd_restore"),
                patch("time.sleep"),
                patch(
                    "ldm_core.handlers.runtime.get_compose_cmd",
                    return_value=["docker", "compose"],
                ),
                patch("ldm_core.ui.UI.ask", return_value="samples.local"),
                patch.object(self.handler, "check_port", return_value=True),
                patch.object(self.handler, "check_registry_collisions"),
            ):
                # Set arguments for samples bootstrap
                self.handler.args.samples = True
                self.handler.args.tag = None
                self.handler.args.db = None
                self.handler.args.host_name = None
                self.handler.args.no_up = False
                self.handler.args.sidecar = False
                self.handler.cmd_run("test-samples")

                # Verify sync_stack was called twice
                self.assertEqual(mock_sync.call_count, 2)
            # The first call should have show_summary=False (to suppress duplicates)
            first_call_kwargs = mock_sync.call_args_list[0][1]
            self.assertFalse(first_call_kwargs.get("show_summary", True))

            # The second call shouldn't have show_summary=False
            second_call_kwargs = mock_sync.call_args_list[1][1]
            self.assertNotIn(
                "show_summary", second_call_kwargs
            )  # Or it's True by default

    @patch("ldm_core.utils.discover_latest_tag")
    def test_cmd_run_non_interactive_tag_prefix(self, mock_discover):
        mock_discover.return_value = "2026.q1.10"
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler, "setup_paths", return_value={"root": self.tmp_dir}
            ),
            patch.object(self.handler, "read_meta", return_value={}),
            patch.object(self.handler, "_pre_flight_checks", return_value=8080),
            patch.object(self.handler, "verify_runtime_environment"),
            patch.object(self.handler.handler, "sync_stack"),
        ):
            self.handler.args.project = "test"
            self.handler.args.tag = None
            self.handler.args.tag_latest = False
            self.handler.args.tag_prefix = "2026.q1"
            self.handler.args.release_type = None
            self.handler.args.no_up = True
            self.handler.args.samples = False
            self.handler.args.db = None
            self.handler.args.host_name = None
            self.handler.args.jvm_args = None
            self.handler.args.port = None
            self.handler.args.snapshot = None

            # The test checks that we don't die and discover_latest_tag is called
            self.handler.cmd_run("test")

            mock_discover.assert_called_once()
            # verify prefix_filter was passed
            call_kwargs = mock_discover.call_args[1]
            self.assertEqual(call_kwargs.get("prefix_filter"), "2026.q1")
            self.assertEqual(call_kwargs.get("release_type"), "any")


if __name__ == "__main__":
    unittest.main()
