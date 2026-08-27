import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.docker_service import DockerService
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
        self.workspace = MagicMock()
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


class TestReadiness(unittest.TestCase):
    def setUp(self):
        # LDM-#1409: on Linux -- so on CI, and never on a macOS dev machine --
        # pipelines/run.py takes its `elif platform.system() == "linux"` branch
        # and calls reclaim_volume_permissions, which runs
        # `docker run --rm -v <path> alpine chown -R ...` against the host.
        #
        # This is class-scope rather than per-case because more than one case
        # in test_preflight_port_collision_check reaches ComposerStage, and a
        # per-case patch silently covers only the one it is attached to. It
        # cost two rounds of CI to learn that; the platform gate means a local
        # macOS run cannot see any of it.
        from unittest.mock import patch as _patch

        reclaim_patcher = _patch("ldm_core.utils.reclaim_volume_permissions")
        self.mock_reclaim_volume_permissions = reclaim_patcher.start()
        self.addCleanup(reclaim_patcher.stop)

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

        # Isolate every test in this class from the tester's REAL ~/.ldmrc
        # persisted default target -- get_docker_cmd_prefix() always calls
        # get_active_target() even for a falsy target_name (see PR #1150).
        from ldm_core.config import TargetNode

        self.active_target_patcher = patch("ldm_core.docker_service.get_active_target")
        self.mock_active_target = self.active_target_patcher.start()
        self.mock_active_target.return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )

    def tearDown(self):
        self.req_patcher.stop()
        self.update_patcher.stop()
        self.active_target_patcher.stop()

    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.die")
    def test_cmd_wait_default_timeout(self, mock_die, mock_success, mock_info):
        """Verify cmd_wait uses the default timeout of 900 if passed None."""
        mock_die.side_effect = Exception("UI.die called")
        with patch.object(self.handler.manager, "run_command", return_value="10%"):
            # Use a time mock that jumps forward by 1000 seconds on the second call
            t = [100, 100, 1100, 1100, 1100, 1100]

            def mock_time():
                return t.pop(0)

            with patch("time.time", side_effect=mock_time), patch("time.sleep"):
                with (
                    patch("requests.get") as mock_get,
                    patch("subprocess.run"),
                    patch("subprocess.Popen"),
                ):
                    mock_get.return_value.status_code = 200

                    try:
                        self.handler.cmd_wait("test", timeout=None)
                    except Exception as e:
                        self.assertEqual(str(e), "UI.die called")

        # Verify it died due to timeout in _wait_for_ready since we advanced time by 1000 > 900
        mock_die.assert_called_with(
            "Project 'test' failed to become ready within 900s."
        )

    @patch("ldm_core.runtime.readiness.datetime")
    @patch("time.sleep")
    def test_wait_for_ready_timeout(self, mock_sleep, mock_datetime):
        # Mock run_command to always return "starting"
        with patch.object(BaseHandler, "run_command", return_value="starting"):
            # Mock time.time to simulate timeout quickly
            with patch("time.time") as mock_time:
                mock_time.side_effect = [
                    0,
                    700,
                ]  # Start at 0, next call at 700 (> 600 timeout)
                result = self.handler._wait_for_ready({}, "localhost")
                self.assertFalse(result)

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.warning")
    def test_wait_for_ready_healthy_with_error_logs(self, mock_warning, mock_success):
        # We need to simulate time passing so `elapsed >= 30` triggers
        def mock_time_side_effect():
            yield 1000  # start_time
            yield 1035  # while condition check (time.time() - start_time = 35)
            yield 1035  # elapsed calculation
            yield 1035  # duration calculation after healthy
            yield 1035  # one more just in case

        def mock_run_command_side_effect(cmd, **kwargs):
            if "logs" in cmd:
                return "INFO: starting\nERROR: ClusterBlockException disk full\n"
            if "inspect" in cmd:
                return "healthy"
            return ""

        self.handler.args.total_start = "900"
        self.handler.args.browser = False
        with (
            patch("time.time") as mock_time,
            patch.object(
                self.handler, "run_command", side_effect=mock_run_command_side_effect
            ),
            patch.object(self.handler.infra, "thaw_elasticsearch", return_value=True),
        ):
            # Create a mock generator
            mock_time.side_effect = mock_time_side_effect()

            project_meta = {"container_name": "test-container"}
            self.handler.handler.readiness._wait_for_ready(project_meta, "test.local")

            mock_warning.assert_any_call("LDM detected 1 new error(s) in the logs.")
            mock_success.assert_any_call(
                "Auto-Thaw successful. Liferay should now proceed."
            )
            # Verify it completed successfully
            mock_success.assert_any_call("Liferay is ready  (2m 15s)")

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.warning")
    def test_wait_for_ready_uses_remote_target_context(
        self, mock_warning, mock_success
    ):
        """_wait_for_ready's docker logs/inspect calls must honor the project's target (#1133)."""
        from ldm_core.config import TargetNode

        self.mock_active_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")

        def mock_time_side_effect():
            yield 1000
            yield 1035
            yield 1035
            yield 1035
            yield 1035

        called_cmds = []

        def mock_run_command_side_effect(cmd, **kwargs):
            called_cmds.append(cmd)
            if "logs" in cmd:
                return "INFO: starting\n"
            if "inspect" in cmd:
                return "healthy"
            return ""

        self.handler.args.total_start = "900"
        self.handler.args.browser = False
        with (
            patch("time.time") as mock_time,
            patch.object(
                self.handler, "run_command", side_effect=mock_run_command_side_effect
            ),
        ):
            mock_time.side_effect = mock_time_side_effect()
            project_meta = {"container_name": "test-container", "target": "aws-1"}
            self.handler.handler.readiness._wait_for_ready(project_meta, "test.local")

        has_context = any(
            "--context" in cmd and "aws-1" in cmd
            for cmd in called_cmds
            if isinstance(cmd, list)
        )
        self.assertTrue(
            has_context, f"Expected --context aws-1 in one of {called_cmds}"
        )

    def test_cmd_wait_uses_remote_target_context(self):
        """cmd_wait's docker stats CPU-idle poll must honor the project's target (#1133)."""
        from ldm_core.config import TargetContext, TargetNode

        target_node = TargetNode(name="aws-1", host="34.1.1.1")
        self.mock_active_target.return_value = target_node
        target_ctx = TargetContext(
            target=target_node,
            is_remote=True,
            docker_prefix=["docker", "--context", "aws-1"],
            compose_prefix=["docker-compose", "--context", "aws-1"],
        )

        meta_val = {
            "container_name": "test-runtime",
            "host_name": "localhost",
            "target": "aws-1",
        }
        self.handler.read_meta = MagicMock(return_value=meta_val)  # type: ignore[method-assign]
        self.handler.manager.workspace.read_meta = MagicMock(return_value=meta_val)  # type: ignore[method-assign]

        with patch(
            "ldm_core.runtime.readiness.resolve_target_context", return_value=target_ctx
        ):
            with patch.object(
                self.handler.handler.readiness, "_wait_for_ready", return_value=True
            ) as mock_wfr:
                with patch.object(
                    self.handler.manager, "run_command", return_value="10%"
                ) as mock_run:
                    with patch("time.sleep"):
                        self.handler.cmd_wait("test", timeout=5)

        self.assertTrue(mock_wfr.called)
        called_cmds = [c.args[0] for c in mock_run.call_args_list]
        has_context = any(
            "--context" in cmd and "aws-1" in cmd
            for cmd in called_cmds
            if isinstance(cmd, list)
        )
        self.assertTrue(
            has_context, f"Expected --context aws-1 in one of {called_cmds}"
        )

    def test_cmd_wait_remote_target_http_probe_uses_node_host(self):
        """Issue #1223: cmd_wait HTTP readiness probe must use remote node host IP instead of 127.0.0.1."""
        from ldm_core.config import TargetContext, TargetNode

        target_node = TargetNode(name="aws-1", host="51.20.52.201")
        target_ctx = TargetContext(
            target=target_node, is_remote=True, docker_prefix=[], compose_prefix=[]
        )

        self.handler.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={
                "container_name": "test-runtime",
                "host_name": "localhost",
                "target": "aws-1",
                "port": 8080,
            }
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch(
            "ldm_core.runtime.readiness.resolve_target_context", return_value=target_ctx
        ):
            with patch.object(
                self.handler.handler.readiness, "_wait_for_ready", return_value=True
            ):
                with patch.object(
                    self.handler.manager, "run_command", return_value="5%"
                ):
                    with patch("requests.get", return_value=mock_resp) as mock_http_get:
                        with patch("time.sleep"):
                            res = self.handler.cmd_wait("test-project", timeout=5)

        self.assertTrue(res)
        self.assertTrue(mock_http_get.called)
        called_url = mock_http_get.call_args[0][0]
        self.assertIn("51.20.52.201", called_url)
        self.assertNotIn("127.0.0.1", called_url)

    @patch("ldm_core.ui.UI.raw")
    @patch("ldm_core.ui.UI.warning")
    @patch("ldm_core.ui.UI.success")
    def test_wait_for_ready_masks_admin_password_in_completion_banner(
        self, mock_success, mock_warning, mock_raw
    ):
        """LDM-#1161: the completion banner used to interpolate the real
        password wrapped in the ANSI 'conceal' escape (UI.HIDDEN,
        \\033[8m), relying on terminal support that many common terminals
        don't have -- so a custom admin password sourced from
        portal-ext.properties (or anywhere else) could render in fully
        visible clear text. The banner must never pass the real password
        to UI.raw() at all; it should print a fixed placeholder mask
        instead, regardless of the password's origin/value."""

        def mock_time_side_effect():
            yield 1000
            yield 1035
            yield 1035
            yield 1035
            yield 1035

        def mock_run_command_side_effect(cmd, **kwargs):
            if "logs" in cmd:
                return "INFO: starting\n"
            if "inspect" in cmd:
                return "healthy"
            return ""

        secret_password = "correct-horse-battery-staple"  # pragma: allowlist secret
        self.handler.args.total_start = "900"
        self.handler.args.browser = False
        with (
            patch("time.time") as mock_time,
            patch.object(
                self.handler, "run_command", side_effect=mock_run_command_side_effect
            ),
            patch.object(self.handler.infra, "thaw_elasticsearch", return_value=True),
        ):
            mock_time.side_effect = mock_time_side_effect()

            project_meta = {
                "container_name": "test-container",
                "credentials": [
                    {
                        "type": "admin",
                        "email": "test@liferay.com",
                        "password": secret_password,  # pragma: allowlist secret
                    }
                ],
            }
            self.handler.handler.readiness._wait_for_ready(project_meta, "test.local")

        all_output = "\n".join(
            str(call.args[0]) if call.args else "" for call in mock_raw.call_args_list
        )
        self.assertNotIn(secret_password, all_output)
        self.assertIn("••••••••", all_output)

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.info")
    def test_wait_for_ready_with_reindex(self, mock_info, mock_success):
        """Verifies that LDM waits for reindex completion if flagged."""

        def mock_run_command_side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "logs" in cmd_str:
                # First check: Healthy/Startup
                if not hasattr(self, "_log_count"):
                    self._log_count = 0
                self._log_count += 1
                if self._log_count == 1:
                    return "Server startup in 123 ms"
                if self._log_count == 2:
                    return "Reindexing all search indexes starting..."
                if self._log_count >= 3:
                    return "Reindexing all search indexes completed in 5000 ms"
            if "inspect" in cmd_str:
                return "healthy"
            return ""

        self.handler.args.total_start = None
        self.handler.args.browser = False
        with (
            patch("time.sleep"),
            patch.object(
                self.handler, "run_command", side_effect=mock_run_command_side_effect
            ),
        ):
            project_meta = {
                "container_name": "test-container",
                "reindex_required": "true",
            }
            # Reset log count for fresh run
            if hasattr(self, "_log_count"):
                delattr(self, "_log_count")

            self.handler.handler.readiness._wait_for_ready(project_meta, "test.local")

            # Verify we saw the reindex message
            mock_success.assert_any_call("Liferay is ready  (0s)")
            # Metadata should have been updated to clear flag
            self.assertEqual(project_meta["reindex_required"], "false")

    @patch("ldm_core.ui.UI.success")
    def test_print_ngrok_url_success(self, mock_success):
        with patch.object(BaseHandler, "run_command") as mock_run:
            mock_run.return_value = (
                '{"tunnels": [{"public_url": "https://foo.ngrok.app"}]}'
            )
            self.handler.handler._print_ngrok_url("my-project")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("docker", args)
            self.assertIn("exec", args)
            self.assertIn("my-project-ngrok-1", args)
            mock_success.assert_called_with(
                "🌍 Public ngrok Tunnel Active: \033[0;36mhttps://foo.ngrok.app\033[0m"
            )

    @patch("ldm_core.ui.UI.warning")
    @patch("ldm_core.ui.UI.debug")
    def test_print_ngrok_url_failure(self, mock_debug, mock_warning):
        with patch.object(BaseHandler, "run_command") as mock_run:
            mock_run.side_effect = Exception("network error")
            self.handler.handler._print_ngrok_url("my-project")
            # Verify debug-level log is emitted with error detail
            mock_debug.assert_called_once()
            debug_msg = mock_debug.call_args[0][0]
            self.assertIn("Could not retrieve ngrok public URL", debug_msg)
            self.assertIn("network error", debug_msg)
            # Verify the user-visible fallback warning is still emitted
            mock_warning.assert_called_with(
                "ngrok container is running, but failed to retrieve public URL."
            )

    @patch("ldm_core.ui.UI.warning")
    def test_print_ngrok_url_none(self, mock_warning):
        with patch.object(BaseHandler, "run_command") as mock_run:
            mock_run.return_value = None
            self.handler.handler._print_ngrok_url("my-project")
            mock_warning.assert_called_with(
                "ngrok container is running, but failed to retrieve public URL."
            )

    @patch("ldm_core.ui.UI.warning")
    def test_cx_expansion_failure_emits_warning_not_raise(self, mock_warning):
        """docker inspect failure during CX env-var expansion should emit UI.warning, not silently pass."""
        with patch.object(BaseHandler, "run_command") as mock_run:
            # port inspect succeeds; CX docker inspect fails
            mock_run.side_effect = [
                "0.0.0.0:8080",
                Exception("docker inspect failed"),
            ]
            project_meta = {
                "liferay_container_name": "test-liferay-1",
                "container_name": "test-liferay-1",
                "host_name": "localhost",
                "ssl": "false",
                "share": "false",
            }
            paths = {"root": Path("/fake/project")}
            with patch("pathlib.Path.is_file", return_value=False):
                self.handler.handler.fragments._patch_fragment_overrides(
                    project_meta, paths
                )

    @patch("time.sleep")
    def test_wait_for_ready_detect_project_path_with_id(self, mock_sleep):
        with (
            patch("time.time", side_effect=[0, 1, 2, 3, 4, 5, 6]),
            patch.object(BaseHandler, "run_command", return_value="healthy"),
            patch.object(self.handler.handler.fragments, "_patch_fragment_overrides"),
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ) as mock_detect,
        ):
            project_meta = {
                "project_name": "test-project-123",
                "container_name": "liferay-test",
            }
            self.handler.handler.readiness._wait_for_ready(project_meta, "localhost")

            # Check that detect_project_path was called with project_id="test-project-123"
            mock_detect.assert_any_call(project_id="test-project-123", for_init=True)

    @patch("ldm_core.ui.UI.success")
    def test_wait_for_ready_triggers_share(self, mock_success):
        project_meta = {
            "project_name": "test-project",
            "container_name": "test-project",
            "port": 8080,
            "share": "true",
            "share_subdomain": "custom-tunnel",
        }

        with (
            patch.object(BaseHandler, "run_command") as mock_run_cmd,
            patch.object(self.handler.share, "cmd_start") as mock_share_start,
        ):
            mock_run_cmd.side_effect = [
                "org.apache.catalina.startup.Catalina.start Server startup in 12000 ms",
                "healthy",
            ]

            res = self.handler.handler.readiness._wait_for_ready(
                project_meta, "localhost", timeout=10
            )
            self.assertTrue(res)

            mock_share_start.assert_called_once_with(
                project_id="test-project",
                subdomain="custom-tunnel",
                ports="8080",
                provider="lfr-tunnel",
                image=None,
                inspector=False,
            )

    def test_preflight_port_collision_check(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            root = Path(tmp_root)
            compose_file = root / "docker-compose.yml"
            # Write a mock docker-compose.yml
            compose_file.write_text("""
services:
  liferay:
    container_name: test-project-liferay-1
    ports:
      - "8080:8080"
            """)

            all_paths = {
                "root": root,
                "data": root / "data",
                "deploy": root / "deploy",
                "files": root / "files",
                "scripts": root / "scripts",
                "state": root / "osgi" / "state",
                "cx": root / "osgi" / "client-extensions",
                "ce_dir": root / "osgi" / "client-extensions",
                "configs": root / "osgi" / "configs",
                "modules": root / "osgi" / "modules",
                "backups": root / "snapshots",
                "portal_log4j": root / "osgi" / "log4j",
                "logs": root / "logs",
                "compose": compose_file,
                "common": Path("/tmp/common"),
            }

            from ldm_core.docker_service import DockerService

            self.handler.args.no_wait = True
            self.handler.args.timeout = 900
            self.handler.args.no_up = False

            # Case A: Container is already running -> passes (doesn't check port)
            with (
                patch.object(
                    DockerService, "is_running", return_value=True
                ) as mock_is_running,
                patch.object(
                    self.handler, "check_port", return_value=False
                ) as mock_check_port,
                patch.object(BaseHandler, "run_command"),
                patch.object(
                    self.handler, "get_container_status", return_value="healthy"
                ),
                patch("ldm_core.ui.UI.die") as mock_die,
            ):
                self.handler.handler.orchestration.cmd_run(
                    project_id="test-project-liferay-1",
                    no_up=False,
                    no_wait=True,
                    is_restart=True,
                    paths=all_paths,
                    project_meta={
                        "container_name": "test-project-liferay-1",
                        "tag": "7.4.3.132",
                    },
                )
                mock_is_running.assert_called_with("test-project-liferay-1")
                mock_check_port.assert_not_called()
                mock_die.assert_not_called()

            # Case B: Container is not running, port is bound -> dies
            with (
                patch.object(
                    DockerService, "is_running", return_value=False
                ) as mock_is_running,
                patch.object(
                    self.handler, "check_port", return_value=False
                ) as mock_check_port,
                patch.object(BaseHandler, "run_command"),
                patch("ldm_core.ui.UI.die", side_effect=SystemExit("died")) as mock_die,
            ):
                with self.assertRaises(SystemExit) as cm:
                    self.handler.handler.orchestration.cmd_run(
                        project_id="test-project-liferay-1",
                        no_up=False,
                        no_wait=True,
                        is_restart=True,
                        paths=all_paths,
                        project_meta={
                            "container_name": "test-project-liferay-1",
                            "tag": "7.4.3.132",
                        },
                    )
                self.assertEqual(str(cm.exception), "died")
                mock_is_running.assert_called_with("test-project-liferay-1")
                mock_check_port.assert_called_once_with("127.0.0.1", 8080)
                mock_die.assert_called_once()

            # Case C: Container is not running, port is free -> passes
            with (
                patch.object(
                    DockerService, "is_running", return_value=False
                ) as mock_is_running,
                patch.object(
                    self.handler, "check_port", return_value=True
                ) as mock_check_port,
                patch.object(BaseHandler, "run_command"),
                patch.object(
                    self.handler, "get_container_status", return_value="healthy"
                ),
                patch("ldm_core.ui.UI.die") as mock_die,
            ):
                self.handler.handler.orchestration.cmd_run(
                    project_id="test-project-liferay-1",
                    no_up=False,
                    no_wait=True,
                    is_restart=True,
                    paths=all_paths,
                    project_meta={
                        "container_name": "test-project-liferay-1",
                        "tag": "7.4.3.132",
                    },
                )
                mock_is_running.assert_called_with("test-project-liferay-1")
                mock_check_port.assert_any_call("127.0.0.1", 8080)
                mock_die.assert_not_called()

    def test_late_port_conflict_names_the_port_a_rerun_would_pick(self):
        """LDM-#1350: the fatal late check must not tell you to go kill a process.

        LDM's documented behaviour is to move to the next free port, and the
        pre-flight check really does that. The pre-flight passes silently when
        the port is genuinely free at that moment; what sits between it and the
        compose validation is a seed download that can take minutes, which is
        long enough for something else to take the port. Staying fatal is
        correct (the port is already written into docker-compose.yml by then),
        but the advice has to say what actually resolves it.
        """
        with tempfile.TemporaryDirectory() as tmp_root:
            root = Path(tmp_root)
            compose_file = root / "docker-compose.yml"
            compose_file.write_text("""
services:
  liferay:
    container_name: test-project-liferay-1
    ports:
      - "8080:8080"
            """)
            paths = self.handler.setup_paths(root)
            paths["compose"] = compose_file

            # Models the real timeline: free during pre-flight, taken by the
            # time compose validation runs. 8081 stays free throughout.
            state = {"taken": False}

            def check_port(_ip, port):
                if not state["taken"]:
                    return True
                return port != 8080

            def take_the_port(*_a, **_k):
                state["taken"] = True

            captured = {}

            def capture_die(msg, details=None, tip=None, exit_code=1):
                captured["msg"] = msg
                captured["tip"] = tip
                captured["exit_code"] = exit_code
                raise SystemExit(exit_code)

            self.handler.args.no_wait = True
            self.handler.args.no_up = False
            # LDM-#1397: this models the interactive case the tip describes.
            # It previously ran with non_interactive=True, where the pre-flight
            # refuses rather than re-selecting -- so the assertion passed while
            # describing a scenario the code never reached.
            self.handler.non_interactive = False

            with (
                patch.object(DockerService, "is_running", return_value=False),
                patch.object(self.handler, "check_port", side_effect=check_port),
                patch.object(BaseHandler, "run_command"),
                patch.object(
                    self.handler, "get_container_status", return_value="healthy"
                ),
                patch.object(self.handler.infra, "setup_global_search"),
                patch.object(self.handler.infra, "setup_global_database"),
                patch.object(
                    self.handler.composer,
                    "write_docker_compose",
                    side_effect=take_the_port,
                ),
                patch("ldm_core.ui.UI.die", side_effect=capture_die),
            ):
                with self.assertRaises(SystemExit):
                    self.handler.handler.orchestration.cmd_run(
                        project_id="test-project-liferay-1",
                        no_up=False,
                        no_wait=True,
                        is_restart=True,
                        paths=paths,
                        project_meta={
                            "container_name": "test-project-liferay-1",
                            "tag": "7.4.3.132",
                        },
                    )

            self.assertEqual(4, captured.get("exit_code"), "still a fatal exit 4")
            self.assertIn("Port conflict detected", captured["msg"])

            tip = captured["tip"] or ""
            self.assertIn("8081", tip, "must name the port a re-run would pick")
            self.assertIn("Re-run", tip)
            # The old advice sent the user hunting for a process to kill.
            self.assertNotIn("stop the service currently using", captured["msg"])
            self.assertNotIn("stop the service currently using", tip)

    def _late_conflict_tip(self, svc_name, port, non_interactive):
        """Drives the late compose-validation check and returns the tip (#1397)."""
        with tempfile.TemporaryDirectory() as tmp_root:
            root = Path(tmp_root)
            compose_file = root / "docker-compose.yml"
            compose_file.write_text(
                f"services:\n"
                f"  {svc_name}:\n"
                f"    container_name: test-project-liferay-1\n"
                f"    ports:\n"
                f'      - "{port}:{port}"\n'
            )
            paths = self.handler.setup_paths(root)
            paths["compose"] = compose_file
            state = {"taken": False}

            def check_port(_ip, p):
                return True if not state["taken"] else p != port

            captured = {}

            def capture_die(msg, details=None, tip=None, exit_code=1):
                captured["tip"] = tip
                raise SystemExit(exit_code)

            self.handler.args.no_wait = True
            self.handler.args.no_up = False
            self.handler.non_interactive = non_interactive

            with (
                patch.object(DockerService, "is_running", return_value=False),
                patch.object(self.handler, "check_port", side_effect=check_port),
                patch.object(BaseHandler, "run_command"),
                patch.object(
                    self.handler, "get_container_status", return_value="healthy"
                ),
                patch.object(self.handler.infra, "setup_global_search"),
                patch.object(self.handler.infra, "setup_global_database"),
                patch.object(
                    self.handler.composer,
                    "write_docker_compose",
                    side_effect=lambda *_a, **_k: state.update(taken=True),
                ),
                patch("ldm_core.ui.UI.die", side_effect=capture_die),
            ):
                with contextlib.suppress(SystemExit):
                    self.handler.handler.orchestration.cmd_run(
                        project_id="test-project-liferay-1",
                        no_up=False,
                        no_wait=True,
                        is_restart=True,
                        paths=paths,
                        project_meta={
                            "container_name": "test-project-liferay-1",
                            "tag": "7.4.3.132",
                        },
                    )
            return captured.get("tip") or ""

    def test_the_tip_only_promises_a_reselect_when_that_will_happen(self):
        """LDM-#1350 named the port a re-run would pick. LDM-#1397: that is only
        true for the Liferay port on an interactive run."""
        tip = self._late_conflict_tip("liferay", 8080, non_interactive=False)
        self.assertIn("8081", tip)
        self.assertIn("pre-flight check will", tip)

    def test_under_y_the_tip_says_a_rerun_will_fail_the_same_way(self):
        """The pre-flight refuses rather than moving the port under -y, so the
        old tip sent the user round a loop that could not terminate."""
        tip = self._late_conflict_tip("liferay", 8080, non_interactive=True)
        self.assertIn("fail the same way", tip)
        self.assertNotIn("pre-flight check will select", tip)

    def test_a_fixed_port_service_is_not_promised_a_reselect(self):
        """kibana's 5601 is a literal in the compose builder; a re-run
        regenerates it."""
        tip = self._late_conflict_tip("kibana", 5601, non_interactive=False)
        self.assertIn("fixed port", tip)
        self.assertNotIn("pre-flight check will select", tip)

    def test_preflight_custom_container_port_collision_check(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            root = Path(tmp_root)
            compose_file = root / "docker-compose.yml"
            compose_file.write_text("services:\n  liferay:\n    image: liferay")

            all_paths = {
                "root": root,
                "data": root / "data",
                "deploy": root / "deploy",
                "files": root / "files",
                "scripts": root / "scripts",
                "state": root / "osgi" / "state",
                "cx": root / "osgi" / "client-extensions",
                "configs": root / "osgi" / "configs",
                "modules": root / "osgi" / "modules",
                "backups": root / "snapshots",
                "portal_log4j": root / "osgi" / "log4j",
                "logs": root / "logs",
                "compose": compose_file,
                "common": Path("/tmp/common"),
            }

            from ldm_core.docker_service import DockerService

            self.handler.args.no_wait = True
            self.handler.args.timeout = 900
            self.handler.args.no_up = False

            # Setup custom containers in meta mapping port 9000
            project_meta = {
                "container_name": "test-project-liferay-1",
                "project_name": "test-project",
                "tag": "7.4.3.132",
                "target": "local",
                "custom_containers": [
                    {
                        "service_name": "wordpress",
                        "image": "wordpress:latest",
                        "ports": ["9000:80"],
                    }
                ],
            }

            # Case: Container not running, port is bound -> dies
            with (
                patch.object(
                    DockerService, "is_running", side_effect=lambda _: False
                ) as mock_is_running,
                patch.object(
                    self.handler, "check_port", side_effect=lambda _ip, p: p != 9000
                ) as mock_check_port,
                patch.object(BaseHandler, "run_command"),
                patch("ldm_core.ui.UI.die", side_effect=SystemExit("died")) as mock_die,
            ):
                with self.assertRaises(SystemExit) as cm:
                    self.handler.handler.orchestration.cmd_run(
                        project_id="test-project",
                        no_up=False,
                        no_wait=True,
                        is_restart=True,
                        paths=all_paths,
                        project_meta=project_meta,
                    )
                self.assertEqual(str(cm.exception), "died")
                mock_is_running.assert_any_call("test-project-wordpress")
                mock_check_port.assert_any_call("127.0.0.1", 9000)
                mock_die.assert_called_once()
                self.assertIn(
                    "Custom container port 9000 for 'wordpress' is already in use",
                    mock_die.call_args[0][0],
                )

    def test_scan_for_expected_deployables(self):
        """Test _scan_for_expected_deployables detects jar manifests and client extensions."""
        import tempfile
        import zipfile

        import yaml

        with tempfile.TemporaryDirectory() as tmp_dir:
            root_path = Path(tmp_dir)

            # Create directories
            configs_deploy = root_path / "configs" / "common" / "deploy"
            configs_deploy.mkdir(parents=True)
            deploy = root_path / "deploy"
            deploy.mkdir(parents=True)
            cx_dir = root_path / "client-extensions"
            cx_dir.mkdir(parents=True)

            # Write a normal jar bundle
            jar_path = configs_deploy / "my-bundle.jar"
            with zipfile.ZipFile(jar_path, "w") as z:
                manifest = (
                    "Manifest-Version: 1.0\n"
                    "Bundle-SymbolicName: com.liferay.commerce.payment.card;singleton:=true\n"
                )
                z.writestr("META-INF/MANIFEST.MF", manifest)

            # Write a fragment jar bundle (with wrapped Symbolic Name line to test unfolding)
            frag_path = deploy / "my-fragment.jar"
            with zipfile.ZipFile(frag_path, "w") as z:
                manifest_frag = (
                    "Manifest-Version: 1.0\n"
                    "Bundle-SymbolicName: com.liferay.commerce.payment.\n"
                    " fragment\n"
                    "Fragment-Host: com.liferay.commerce\n"
                )
                z.writestr("META-INF/MANIFEST.MF", manifest_frag)

            # Write a client extension yaml
            cx_proj = cx_dir / "my-cx"
            cx_proj.mkdir()
            yaml_content = {
                "my-cx-id": {
                    "name": "My Custom Element",
                    "type": "customElement",
                }
            }
            with open(cx_proj / "client-extension.yaml", "w") as f:
                yaml.dump(yaml_content, f)

            # Call scanner
            targets = self.handler.handler.orchestration._scan_for_expected_deployables(
                root_path
            )

            self.assertEqual(targets.get("com.liferay.commerce.payment.card"), "Active")
            self.assertEqual(
                targets.get("com.liferay.commerce.payment.fragment"), "Resolved"
            )
            self.assertEqual(targets.get("my-cx-id"), "Active")

    @patch("requests.get")
    @patch("time.sleep")
    @patch("ldm_core.runtime.readiness.time.time")
    def test_cmd_wait_with_deployables_success(self, mock_time, mock_sleep, mock_get):
        """Test cmd_wait checks deploy folder and Gogo console successfully."""
        from ldm_core.docker_service import DockerService

        mock_get.return_value.status_code = 200
        mock_time.side_effect = [100.0 + i for i in range(100)]

        mock_targets = {
            "com.liferay.commerce.payment.card": "Active",
            "my-cx-id": "Active",
        }

        with (
            patch.object(
                self.handler.handler.readiness, "_wait_for_ready", return_value=True
            ),
            patch.object(
                self.handler.handler,
                "_scan_for_expected_deployables",
                return_value=mock_targets,
            ),
            patch.object(DockerService, "exec") as mock_exec,
            patch.object(BaseHandler, "run_command", return_value="10%"),
            patch("ldm_core.ui.UI.die") as mock_die,
        ):
            mock_exec.side_effect = [
                # deploy folder check 1
                "my-module.jar\n",
                # deploy folder check 2
                "",
                # Gogo check 1 (missing client extension)
                "ID|State|Level|Symbolic name\n284|Active|10|com.liferay.commerce.payment.card\n",
                # Gogo check 2 (all active)
                "ID|State|Level|Symbolic name\n284|Active|10|com.liferay.commerce.payment.card\n"
                "285|Active|10|com.liferay.portal.osgi.web.client.extension.internal.model.WebClientExtensionOSGiBundle-my-cx-id\n",
            ]

            res = self.handler.handler.readiness.cmd_wait(
                "test-project", timeout=600, wait_for_deployables=True
            )
            self.assertTrue(res)
            mock_die.assert_not_called()

    @patch("requests.get")
    @patch("time.sleep")
    @patch("ldm_core.runtime.readiness.time.time")
    def test_cmd_wait_with_deployables_gogo_fallback(
        self, mock_time, mock_sleep, mock_get
    ):
        """Test cmd_wait falls back gracefully if Gogo Shell telnet is unavailable."""
        mock_get.return_value.status_code = 200
        mock_time.side_effect = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 1000.0] + [
            1000.0
        ] * 10

        with (
            patch.object(
                self.handler.handler.readiness, "_wait_for_ready", return_value=True
            ),
            patch.object(DockerService, "exec") as mock_exec,
            patch.object(BaseHandler, "run_command", return_value="10%"),
            patch("ldm_core.ui.UI.die") as mock_die,
            patch("ldm_core.ui.UI.warning") as mock_warning,
        ):
            mock_exec.side_effect = ["", Exception("telnet not found")]

            res = self.handler.handler.readiness.cmd_wait(
                "test-project", timeout=600, wait_for_bundles="com.liferay.commerce"
            )
            self.assertTrue(res)
            mock_die.assert_not_called()
            mock_warning.assert_any_call(
                "Some deployable targets did not reach active state via Gogo console verification."
            )

    def test_check_troubleshooting_signatures(self):
        """Test that check_troubleshooting_signatures utility matches error signatures correctly."""
        from ldm_core.utils import check_troubleshooting_signatures

        # POSIX locks
        self.assertIn(
            "POSIX filesystem lock conflict",
            check_troubleshooting_signatures("Unable to create lock manager"),
        )
        self.assertIn(
            "POSIX filesystem lock conflict",
            check_troubleshooting_signatures("access_denied_exception on state file"),
        )

        # Connection refused
        self.assertIn(
            "Database connection refused",
            check_troubleshooting_signatures("Connection to localhost:5432 refused"),
        )
        self.assertIn(
            "Database connection refused",
            check_troubleshooting_signatures(
                "psycopg2.OperationalError: could not connect"
            ),
        )

        # Database missing
        self.assertIn(
            "Target database does not exist",
            check_troubleshooting_signatures('database "lportal" does not exist'),
        )

        # JVM cache
        self.assertIn(
            "JVM CodeCache",
            check_troubleshooting_signatures("ReservedCodeCacheSize=512m exceeded"),
        )

        # Elasticsearch blocks
        self.assertIn(
            "Elasticsearch write block",
            check_troubleshooting_signatures("ClusterBlockException index blocked"),
        )

        # Non-matching line
        self.assertIsNone(
            check_troubleshooting_signatures("Everything is running fine")
        )

    def test_wait_for_ready_http_and_https_ports(self):
        """Verify URL construction handles default and custom ports for both http and https."""
        with (
            patch.object(
                self.handler.handler.readiness, "_wait_for_ready", return_value=True
            ),
            patch.object(self.handler, "run_command", return_value="5.0"),
            patch("time.sleep"),
            patch("requests.get") as mock_get,
            patch("time.time", return_value=100.0),
        ):
            # 1. Custom HTTPS port (e.g. 8443) -> should be appended
            self.handler.infra.get_proxy_ports.return_value = {
                "http": 80,
                "https": 8443,
            }
            with (
                patch.object(
                    self.handler.composer, "_is_ssl_active", return_value=True
                ),
                patch.object(
                    self.handler,
                    "read_meta",
                    return_value={
                        "container_name": "test-runtime",
                        "host_name": "test.local",
                    },
                ),
            ):
                mock_get.reset_mock()
                mock_get.return_value.status_code = 200
                self.handler.handler.readiness.cmd_wait("test-project", timeout=600)
                mock_get.assert_called_with(
                    "https://test.local:8443", timeout=5, verify=False
                )

            # 2. Default HTTPS port (443) -> should not append port
            self.handler.infra.get_proxy_ports.return_value = {"http": 80, "https": 443}
            with (
                patch.object(
                    self.handler.composer, "_is_ssl_active", return_value=True
                ),
                patch.object(
                    self.handler,
                    "read_meta",
                    return_value={
                        "container_name": "test-runtime",
                        "host_name": "test.local",
                    },
                ),
            ):
                mock_get.reset_mock()
                mock_get.return_value.status_code = 200
                self.handler.handler.readiness.cmd_wait("test-project", timeout=600)
                mock_get.assert_called_with(
                    "https://test.local", timeout=5, verify=False
                )

            # 3. Custom HTTP port (e.g. 8080) -> should be appended (ignoring global proxy port 9999)
            self.handler.infra.get_proxy_ports.return_value = {
                "http": 9999,
                "https": 443,
            }
            with (
                patch.object(
                    self.handler.composer, "_is_ssl_active", return_value=False
                ),
                patch.object(
                    self.handler,
                    "read_meta",
                    return_value={
                        "container_name": "test-runtime",
                        "host_name": "localhost",
                        "port": 8080,
                    },
                ),
            ):
                mock_get.reset_mock()
                mock_get.return_value.status_code = 200
                self.handler.handler.readiness.cmd_wait("test-project", timeout=600)
                mock_get.assert_called_with(
                    "http://127.0.0.1:8080", timeout=5, verify=False
                )

            # 4. Default HTTP port (80) -> should not append port (ignoring global proxy port 9999)
            self.handler.infra.get_proxy_ports.return_value = {
                "http": 9999,
                "https": 443,
            }
            with (
                patch.object(
                    self.handler.composer, "_is_ssl_active", return_value=False
                ),
                patch.object(
                    self.handler,
                    "read_meta",
                    return_value={
                        "container_name": "test-runtime",
                        "host_name": "localhost",
                        "port": 80,
                    },
                ),
            ):
                mock_get.reset_mock()
                mock_get.return_value.status_code = 200
                self.handler.handler.readiness.cmd_wait("test-project", timeout=600)
                mock_get.assert_called_with("http://127.0.0.1", timeout=5, verify=False)

    def test_cmd_wait_configurable_cpu_idle(self):
        """Verify cmd_wait respects configurable cpu_idle_threshold and cpu_idle_checks."""
        with (
            patch.object(
                self.handler.handler.readiness, "_wait_for_ready", return_value=True
            ),
            patch("time.sleep"),
            patch("requests.get") as mock_get,
            patch("time.time", return_value=100.0),
        ):
            mock_get.return_value.status_code = 200

            # 1. Custom checks = 5. should check stats 5 times before succeeding.
            with patch.object(
                self.handler, "run_command", return_value="5.0"
            ) as mock_run:
                self.handler.handler.readiness.cmd_wait(
                    "test-project",
                    timeout=600,
                    cpu_idle_threshold=10.0,
                    cpu_idle_checks=5,
                )
                # Count calls to docker stats command: should be exactly 5
                stats_calls = [c for c in mock_run.call_args_list if "stats" in c[0][0]]
                self.assertEqual(len(stats_calls), 5)

            # 2. Custom threshold = 5.0. 8.0 > 5.0 (not idle) -> loops until timeout.
            with (
                patch.object(self.handler, "run_command", return_value="8.0"),
                patch(
                    "time.time",
                    side_effect=[
                        100.0,
                        100.0,
                        100.0,
                        100.0,
                        100.0,
                        700.0,
                        700.0,
                        700.0,
                        700.0,
                    ],
                ),
                patch("ldm_core.ui.UI.warning") as mock_warning,
            ):
                self.handler.handler.readiness.cmd_wait(
                    "test-project",
                    timeout=600,
                    cpu_idle_threshold=5.0,
                    cpu_idle_checks=3,
                )
                mock_warning.assert_any_call(
                    "Project 'test-project' did not reach an idle state within the timeout, but is responding to HTTP."
                )

            # 3. Malformed env vars: should warn and fall back to 15.0 and 3.
            with (
                patch.dict(
                    os.environ,
                    {
                        "LDM_CPU_IDLE_THRESHOLD": "invalid",
                        "LDM_CPU_IDLE_CHECKS": "three",
                    },
                ),
                patch.object(
                    self.handler, "run_command", return_value="5.0"
                ) as mock_run,
                patch("ldm_core.ui.UI.warning") as mock_warning,
            ):
                self.handler.handler.readiness.cmd_wait("test-project", timeout=600)
                mock_warning.assert_any_call(
                    "Malformed LDM_CPU_IDLE_THRESHOLD; falling back to 15.0"
                )
                mock_warning.assert_any_call(
                    "Malformed LDM_CPU_IDLE_CHECKS; falling back to 3"
                )
                # With fallback to 3 checks, run_command should be called exactly 3 times
                stats_calls = [c for c in mock_run.call_args_list if "stats" in c[0][0]]
                self.assertEqual(len(stats_calls), 3)

    def test_cmd_wait_overall_timeout_budgeting(self):
        """Verify that sequential phases in cmd_wait share a single overall timeout budget."""
        with (
            patch.object(
                self.handler.handler.readiness, "_wait_for_ready", return_value=True
            ),
            patch("time.sleep"),
            patch("requests.get"),
            # LDM-#1409: the timeout path dumps container logs with a bare
            # `subprocess.run([... docker logs --tail 200 ...])` in
            # runtime/readiness.py -- not run_command, so no facade stub
            # reaches it. This test asserts how the timeout budget is divided
            # between phases, not what a real container logged.
            patch("subprocess.run"),
            patch("ldm_core.ui.UI.die") as mock_die,
            # Mock time.time() to return progression values that exhaust the budget
            patch(
                "time.time",
                side_effect=[
                    100.0,  # overall_start (cmd_wait start)
                    100.0,  # container log wait elapsed calc
                    100.0,  # container log wait remaining timeout check
                    # Phase 1 starts (inside _wait_for_ready start)
                    100.0,  # start_time
                    300.0,  # inside _wait_for_ready loop checks (elapsed = 200s)
                    300.0,  # inside _wait_for_ready check healthy (returns True)
                    # Phase 1 completes
                    300.0,  # Phase 2 HTTP start
                    700.0,  # HTTP loop check (elapsed = 600s, timeout = 600s!)
                    700.0,  # HTTP loop body
                ],
            ),
        ):
            self.handler.handler.readiness.cmd_wait("test-project", timeout=600)
            mock_die.assert_called_with(
                "Project 'test-project' is running but HTTP http://127.0.0.1:8080 is not responding correctly."
            )
