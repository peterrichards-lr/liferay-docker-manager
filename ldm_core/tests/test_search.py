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
    @patch("ldm_core.ui.UI.detail")
    @patch("ldm_core.docker_service.DockerService.exec")
    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    def test_cmd_reindex_running_schedules_for_boot_without_gogo(
        self, mock_is_running, mock_exec, mock_detail, mock_success
    ):
        """LDM-#1242: a running container must schedule a boot reindex, not attempt Gogo.

        This test previously asserted the opposite -- that a telnet/Gogo command
        was issued and that reindex had been "triggered". It passed while the
        feature was entirely broken, because it only checked that a command was
        *issued*, never that Gogo accepted it. Gogo answered
        `PatternSyntaxException` and no reindex ever ran.
        """
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

            # No Gogo/telnet attempt at all -- there is no Gogo command capable
            # of triggering a reindex, so issuing one was pure noise.
            mock_exec.assert_not_called()

            # It must fall through to the boot-time path that actually works.
            mock_flag.assert_called_once_with(self.tmp_dir)
            mock_success.assert_called_with(
                f"Project '{self.tmp_dir.name}' scheduled for search reindex on next boot."
            )

            # And it must explain why, rather than staying silent.
            self.assertTrue(
                any(
                    "immediate" in str(c[0][0]).lower()
                    for c in mock_detail.call_args_list
                ),
                "Expected an explanation that an immediate reindex is unavailable. "
                f"Got: {[str(c[0][0]) for c in mock_detail.call_args_list]}",
            )

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.docker_service.DockerService.exec")
    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    def test_cmd_reindex_never_claims_immediate_success(
        self, mock_is_running, mock_exec, mock_success
    ):
        """LDM-#1242 regression guard: the false success message must never return.

        telnet exits 0 whenever the connection succeeds, regardless of whether
        Gogo understood the input, so the old code reported
        "Successfully triggered immediate runtime reindex" every single time
        while doing nothing at all.
        """
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
            ),
        ):
            self.handler.handler.search.cmd_reindex("test")

            claims = [
                str(c[0][0])
                for c in mock_success.call_args_list
                if "immediate" in str(c[0][0]).lower()
            ]
            self.assertEqual(
                [],
                claims,
                f"Regression (#1242): reindex claimed immediate success: {claims}",
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

    def test_cmd_migrate_search_uses_remote_target_context(self):
        """cmd_migrate_search must honor active target context for docker ps checks (#1133)."""
        from ldm_core.config import TargetNode
        from ldm_core.runtime.search import SearchService

        search_svc = SearchService(self.handler)
        self.handler.read_meta = MagicMock(return_value={"target": "aws-1"})  # type: ignore[method-assign]
        self.handler.detect_project_path = MagicMock(return_value=self.tmp_dir)  # type: ignore[method-assign]

        files_dir = self.tmp_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        configs_dir = self.tmp_dir / "osgi" / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)
        data_dir = self.tmp_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        paths = {
            "root": self.tmp_dir,
            "files": files_dir,
            "configs": configs_dir,
            "data": data_dir,
            "deploy": self.tmp_dir / "deploy",
            "common_dirs": [],
        }
        self.handler.setup_paths = MagicMock(return_value=paths)  # type: ignore[method-assign]
        self.handler.run_command = MagicMock(return_value="")  # type: ignore[method-assign]

        with patch("ldm_core.docker_service.get_active_target") as mock_target:
            mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
            with (
                patch("ldm_core.ui.UI.heading"),
                patch("ldm_core.ui.UI.ask", return_value="N"),
                patch("ldm_core.ui.UI.die"),
            ):
                try:
                    search_svc.cmd_migrate_search("test")
                except SystemExit:
                    pass

        calls = [c[0][0] for c in self.handler.run_command.call_args_list]
        for cmd in calls:
            self.assertIn("--context", cmd)
            self.assertIn("aws-1", cmd)
