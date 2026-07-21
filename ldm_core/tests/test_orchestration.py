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


class TestOrchestration(unittest.TestCase):
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

    def test_resolve_container_label_discovery(self):
        """Verify that resolve_container uses Docker labels for discovery."""
        with patch.object(BaseHandler, "run_command") as mock_run:
            # Mock 'docker ps' returning a renamed container
            mock_run.return_value = "a8cf79c6a3b2_my-project-liferay-1"

            res = self.handler.resolve_container("my-project", "liferay")

            # Verify the call used labels
            mock_run.assert_called()
            args = mock_run.call_args[0][0]
            self.assertIn("label=com.liferay.ldm.project=my-project", args)
            self.assertIn("label=com.docker.compose.service=liferay", args)

            # Verify it returned the discovered name
            self.assertEqual(res, "a8cf79c6a3b2_my-project-liferay-1")

    def test_resolve_container_fallback(self):
        """Verify that resolve_container falls back to standard name if labels fail."""
        with patch.object(BaseHandler, "run_command") as mock_run:
            mock_run.return_value = ""

            res = self.handler.resolve_container("my-project", "db")

            self.assertEqual(res, "my-project-db-1")

    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
    def test_cmd_stop_basic(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(BaseHandler, "run_command") as mock_run:
            self.handler.cmd_stop("test")
            # Verify stop command was issued
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            self.assertIn("stop", call_args)

    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
    def test_cmd_restart_basic(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(BaseHandler, "run_command") as mock_run:
            self.handler.cmd_restart("test")
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            self.assertIn("restart", call_args)

    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
    @patch("ldm_core.runtime.orchestration.shutil.rmtree")
    def test_cmd_down_with_delete(self, mock_rmtree, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with (
            patch.object(BaseHandler, "run_command"),
            patch.object(Path, "exists", return_value=True),
        ):
            self.handler.cmd_down("test", delete=True)
            # Verify down command AND directory deletion
            self.assertTrue(mock_rmtree.called)

    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
    @patch("ldm_core.runtime.orchestration.shutil.rmtree")
    def test_cmd_down_dry_run(self, mock_rmtree, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        self.handler.dry_run = True
        with (
            patch.object(BaseHandler, "run_command") as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            self.handler.cmd_down("test", delete=True)
            self.assertFalse(mock_rmtree.called)
            self.assertFalse(mock_run.called)

    @patch("ldm_core.ui.UI.die")
    def test_cmd_reseed_no_tag_dies(self, mock_die):
        mock_die.side_effect = SystemExit
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(self.handler, "read_meta", return_value={}),
        ):
            with self.assertRaises(SystemExit):
                self.handler.handler.cmd_reseed("test")
            mock_die.assert_called_with("Project missing tag metadata. Cannot reseed.")

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.confirm", return_value=True)
    def test_cmd_reseed_success(self, mock_confirm, mock_success):
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "read_meta",
                return_value={"tag": "2026.q1", "db_type": "mysql"},
            ),
            patch.object(self.handler.handler.orchestration, "cmd_reset"),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={
                    "root": self.tmp_dir,
                    "data": self.tmp_dir / "data",
                    "state": self.tmp_dir / "osgi" / "state",
                },
            ),
            patch.object(self.handler.assets, "_fetch_seed", return_value=True),
            patch.object(self.handler, "verify_runtime_environment"),
            patch.object(self.handler.handler.orchestration, "cmd_run"),
        ):
            self.handler.handler.cmd_reseed("test")
            mock_success.assert_called_with("Reseed complete.")

    def test_cmd_reseed_dry_run(self):
        self.handler.dry_run = True
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "read_meta",
                return_value={"tag": "2026.q1", "db_type": "mysql"},
            ),
            patch.object(self.handler.handler.orchestration, "cmd_reset") as mock_reset,
            patch.object(self.handler.assets, "_fetch_seed") as mock_fetch,
        ):
            res = self.handler.handler.cmd_reseed("test")
            self.assertTrue(res)
            self.assertFalse(mock_reset.called)
            self.assertFalse(mock_fetch.called)

    @patch("ldm_core.runtime.orchestration.shutil.rmtree")
    def test_cmd_reset_dry_run(self, mock_rmtree):
        self.handler.dry_run = True
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={"data": self.tmp_dir / "data"},
            ),
            patch.object(self.handler.handler.orchestration, "cmd_down") as mock_down,
        ):
            # Create data folder to simulate existence
            data_dir = self.tmp_dir / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            res = self.handler.handler.orchestration.cmd_reset("test", target="data")
            self.assertTrue(res)
            self.assertFalse(mock_rmtree.called)
            self.assertFalse(mock_down.called)

    @patch("ldm_core.ui.UI.error")
    @patch("ldm_core.ui.UI.confirm", return_value=True)
    def test_cmd_reseed_fail(self, mock_confirm, mock_error):
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "read_meta",
                return_value={"tag": "2026.q1", "db_type": "mysql"},
            ),
            patch.object(self.handler.handler.orchestration, "cmd_reset"),
            patch.object(self.handler, "setup_paths", return_value={}),
            patch.object(self.handler.assets, "_fetch_seed", return_value=False),
        ):
            self.handler.handler.cmd_reseed("test")
            mock_error.assert_called_with("Reseed failed.")

    @patch("ldm_core.pipelines.run.Pipeline.run", return_value=True)
    def test_cmd_run_invokes_pipeline(self, mock_run):
        with patch.object(
            self.handler, "detect_project_path", return_value=self.tmp_dir
        ):
            result = self.handler.cmd_run("test_proj")
            self.assertTrue(result)
            mock_run.assert_called_once()

    def test_sync_stack_runs_compose(self):
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(self.handler.config, "sync_common_assets"),
            patch.object(BaseHandler, "get_container_status", return_value="running"),
            patch.object(BaseHandler, "run_command") as mock_run_cmd,
            patch.object(BaseHandler, "check_port", return_value=True),
            patch(
                "ldm_core.pipelines.run.ConfigResolutionStage._resolve_tag",
                return_value=("2024.q1.1", False),
            ),
        ):
            result = self.handler.cmd_run(
                "test",
                no_wait=True,
                paths=self.tmp_dir,
                project_meta={"container_name": "test"},
            )
            self.assertTrue(result)
            self.assertTrue(mock_run_cmd.called)
