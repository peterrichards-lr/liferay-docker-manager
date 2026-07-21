import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.runtime import RuntimeService


class MockRuntime(BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.args.tag_latest = False
        self.args.tag_prefix = None
        self.args.timeout = 900
        self.verbose = False
        self.non_interactive = True
        self.dry_run = False

        # Self-referential manager for service compatibility
        from typing import Any, cast

        self.manager = cast(Any, self)

        self.assets = MagicMock()
        self.infra = MagicMock()
        self.snapshot = MagicMock()
        self.share = MagicMock()
        self.license = MagicMock()
        self.diagnostics = MagicMock()
        self.share.resolve_share_config.return_value = ("lfr-tunnel", "lfr-demo.online")
        from ldm_core.defaults import DefaultsManager
        from ldm_core.handlers.composer import ComposerService
        from ldm_core.handlers.config import ConfigService

        self.defaults = DefaultsManager()
        self.config = ConfigService(self)
        self.config.update_portal_ext = MagicMock()  # type: ignore[method-assign]
        self.composer = ComposerService(self)
        self.handler = RuntimeService(self)
        self.runtime = self.handler
        self.verify_runtime_environment = MagicMock()  # type: ignore[method-assign]

    def cmd_run(self, *args, **kwargs):
        return self.handler.cmd_run(*args, **kwargs)

    def cmd_stop(self, *args, **kwargs):
        return self.handler.cmd_stop(*args, **kwargs)

    def cmd_restart(self, *args, **kwargs):
        return self.handler.cmd_restart(*args, **kwargs)

    def cmd_down(self, *args, **kwargs):
        return self.handler.cmd_down(*args, **kwargs)

    def cmd_logs(self, *args, **kwargs):
        return self.handler.cmd_logs(*args, **kwargs)

    def cmd_wait(self, *args, **kwargs):
        return self.handler.cmd_wait(*args, **kwargs)

    def _wait_for_ready(self, *args, **kwargs):
        return self.handler._wait_for_ready(*args, **kwargs)

    def get_resource_path(self, name):
        return Path("/tmp/res") / name

    def get_config(self, key, default=None):
        return default

    def read_meta(self, *args, **kwargs):
        return {"container_name": "test-runtime", "host_name": "localhost"}

    def setup_paths(self, root):
        return super().setup_paths(root)

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


class TestSearch(unittest.TestCase):
    def setUp(self):
        from unittest.mock import MagicMock, patch

        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self.tmp_dir_obj.name)
        self.handler = MockRuntime()
        self.handler.detect_project_path = MagicMock(return_value=self.tmp_dir)  # type: ignore[method-assign]

        # Globally mock requests.get for _wait_for_ready tests to prevent hanging/failing
        self.req_patcher = patch("requests.get")
        self.mock_req = self.req_patcher.start()
        self.mock_req.return_value = MagicMock(status_code=200)

        self.update_patcher = patch(
            "ldm_core.diagnostics.doctor.check_for_updates", return_value=(None, None)
        )
        self.update_patcher.start()

    def tearDown(self):
        self.req_patcher.stop()
        self.update_patcher.stop()

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.confirm", return_value=True)
    def test_cmd_reindex(self, mock_confirm, mock_success):
        """Verify that ldm reindex flags the project correctly."""
        # Enable interactive mode for this test to trigger confirm
        self.handler.non_interactive = False
        self.handler.handler.non_interactive = False
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(self.handler.handler.search, "flag_reindex") as mock_flag,
            patch.object(self.handler.handler.orchestration, "cmd_run") as mock_run,
        ):
            self.handler.handler.search.cmd_reindex("test")
            mock_flag.assert_called_once_with(self.tmp_dir)
            mock_run.assert_called_once_with(self.tmp_dir.name)
            mock_success.assert_called_with(
                f"Project '{self.tmp_dir.name}' scheduled for search reindex on next boot."
            )

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.docker_service.DockerService.exec")
    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    def test_cmd_reindex_immediate_running(
        self, mock_is_running, mock_exec, mock_info, mock_success
    ):
        """Verify that ldm reindex triggers immediate reindex when container is running."""
        self.handler.args.force_boot = False
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "read_meta",
                return_value={"liferay_container_name": "test-container"},
            ),
            patch.object(self.handler.handler.search, "flag_reindex") as mock_flag,
            patch.object(self.handler.handler.orchestration, "cmd_run") as mock_run,
        ):
            self.handler.handler.search.cmd_reindex("test")

            # Verify DockerService.exec was called to run telnet command
            mock_is_running.assert_called_once_with("test-container")
            mock_exec.assert_called_once()
            args = mock_exec.call_args[0][1]
            self.assertIn("telnet localhost 11311", args[2])

            # Verify it did NOT flag or restart
            mock_flag.assert_not_called()
            mock_run.assert_not_called()
            mock_success.assert_called_with(
                "Successfully triggered immediate runtime reindex on 'test-container'."
            )

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.warning")
    @patch(
        "ldm_core.docker_service.DockerService.exec",
        side_effect=Exception("Failed connection"),
    )
    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    def test_cmd_reindex_immediate_failure_fallback(
        self, mock_is_running, mock_exec, mock_warning, mock_success
    ):
        """Verify fallback to boot scheduling if immediate reindex command fails."""
        self.handler.args.force_boot = False
        self.handler.non_interactive = True
        self.handler.handler.non_interactive = True
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "read_meta",
                return_value={"liferay_container_name": "test-container"},
            ),
            patch.object(
                self.handler.handler.search, "flag_reindex", return_value=True
            ) as mock_flag,
        ):
            self.handler.handler.search.cmd_reindex("test")

            mock_is_running.assert_called_once_with("test-container")
            mock_exec.assert_called_once()
            mock_warning.assert_called_once()
            self.assertIn(
                "Failed to execute immediate reindex", mock_warning.call_args[0][0]
            )

            # Verify we fell back to scheduling for next boot
            mock_flag.assert_called_once_with(self.tmp_dir)
            mock_success.assert_called_with(
                f"Project '{self.tmp_dir.name}' scheduled for search reindex on next boot."
            )

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.confirm", return_value=True)
    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    def test_cmd_reindex_force_boot(self, mock_is_running, mock_confirm, mock_success):
        """Verify force-boot skips immediate reindexing and does standard scheduling."""
        self.handler.args.force_boot = True
        self.handler.non_interactive = False
        self.handler.handler.non_interactive = False
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "read_meta",
                return_value={"liferay_container_name": "test-container"},
            ),
            patch.object(
                self.handler.handler.search, "flag_reindex", return_value=True
            ) as mock_flag,
            patch.object(self.handler.handler.orchestration, "cmd_run") as mock_run,
        ):
            self.handler.handler.search.cmd_reindex("test")

            # Should check status, see it's running, but skip because force_boot is true
            mock_is_running.assert_called_once_with("test-container")

            # Should flag for reindex and restart
            mock_flag.assert_called_once_with(self.tmp_dir)
            mock_run.assert_called_once_with(self.tmp_dir.name)
            mock_success.assert_called_with(
                f"Project '{self.tmp_dir.name}' scheduled for search reindex on next boot."
            )
