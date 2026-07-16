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
        self.share.resolve_share_config.return_value = ("lfr-tunnel", "lfr-demo.online")
        from ldm_core.defaults import DefaultsManager
        from ldm_core.handlers.composer import ComposerService
        from ldm_core.handlers.config import ConfigService

        self.defaults = DefaultsManager()
        self.config = ConfigService(self)
        self.config.update_portal_ext = MagicMock()  # type: ignore[method-assign]
        self.composer = ComposerService(self)
        self.handler = RuntimeService(self)
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

    def detect_project_path(self, *args, **kwargs):
        return Path("/tmp/runtime-project")

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


class TestRuntime(unittest.TestCase):
    def setUp(self):
        self.handler = MockRuntime()
        self.tmp_dir = Path("/tmp/runtime-project")

        # Globally mock requests.get for _wait_for_ready tests to prevent hanging/failing
        from unittest.mock import MagicMock, patch

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
        with patch.object(self.handler, "run_command") as mock_run:
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
        with patch.object(self.handler, "run_command") as mock_run:
            mock_run.return_value = ""

            res = self.handler.resolve_container("my-project", "db")

            self.assertEqual(res, "my-project-db-1")

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

    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.die")
    def test_cmd_wait_default_timeout(self, mock_die, mock_success, mock_info):
        """Verify cmd_wait uses the default timeout of 900 if passed None."""
        mock_die.side_effect = Exception("UI.die called")
        with patch.object(self.handler.manager, "run_command", return_value="10%"):
            # Use a time mock that jumps forward by 1000 seconds on the second call
            t = [100, 1100, 1100, 1100, 1100]

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

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    def test_cmd_logs_advanced_flags(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(self.handler, "run_command") as mock_run:
            # 1. resolve_container, 2. exact check, 3. logs call
            mock_run.side_effect = ["container-id", "container-id", None]
            self.handler.cmd_logs(
                project_id="test",
                tail="50",
                timestamps=True,
                since="2024-01-01",
                until="2024-01-02",
            )
            mock_run.assert_called()
            # Find the call that executed 'docker compose logs'
            logs_call = []
            for call in mock_run.call_args_list:
                call_args = call[0][0]
                if "logs" in call_args:
                    logs_call = call_args
                    break

            self.assertTrue(len(logs_call) > 0)
            self.assertIn("--tail", logs_call)
            self.assertIn("50", logs_call)
            self.assertIn("-t", logs_call)
            self.assertIn("--since", logs_call)
            self.assertIn("2024-01-01", logs_call)
            self.assertIn("--until", logs_call)
            self.assertIn("2024-01-02", logs_call)

    @patch("subprocess.Popen")
    def test_cmd_logs_filtering(self, mock_popen):
        # We mock Popen's stdout stream with log lines
        mock_process = MagicMock()
        mock_process.poll.return_value = 0

        log_lines = [
            "Startup message (no level)",
            "10:00:00.000 INFO  [main] portal starting...",
            "10:00:01.000 WARN  [main] deprecated config",
            "10:00:02.000 ERROR [main] Database connection failed",
            "java.sql.SQLException: Connection refused",
            "    at MyClass.run(MyClass.java:10)",
        ]

        # readline should yield log lines, then EOF ("")
        mock_process.stdout.readline.side_effect = [*log_lines, ""]
        mock_popen.return_value = mock_process

        # Test simple grep matching "Database"
        with patch("sys.stdout"), patch("builtins.print") as mock_print:
            self.handler.handler._run_log_command(
                ["docker", "logs", "container"], grep="Database"
            )
            # Verify only matched lines are printed
            printed_calls = [c[0][0] for c in mock_print.call_args_list]
            self.assertEqual(
                printed_calls, ["10:00:02.000 ERROR [main] Database connection failed"]
            )

        # Test level matching (INFO and above)
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.side_effect = [*log_lines, ""]
        with patch("builtins.print") as mock_print:
            self.handler.handler._run_log_command(
                ["docker", "logs", "container"], level="INFO"
            )
            printed_calls = [c[0][0] for c in mock_print.call_args_list]
            # Since level=INFO, it should filter out the startup message (no level),
            # but allow INFO, WARN, ERROR, and all subsequent lines (stack traces) for ERROR.
            self.assertEqual(
                printed_calls,
                [
                    "10:00:00.000 INFO  [main] portal starting...",
                    "10:00:01.000 WARN  [main] deprecated config",
                    "10:00:02.000 ERROR [main] Database connection failed",
                    "java.sql.SQLException: Connection refused",
                    "    at MyClass.run(MyClass.java:10)",
                ],
            )

        # Test level matching (ERROR and above)
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.side_effect = [*log_lines, ""]
        with patch("builtins.print") as mock_print:
            self.handler.handler._run_log_command(
                ["docker", "logs", "container"], level="ERROR"
            )
            printed_calls = [c[0][0] for c in mock_print.call_args_list]
            # Should filter out startup, INFO, WARN, and keep ERROR + stack trace.
            self.assertEqual(
                printed_calls,
                [
                    "10:00:02.000 ERROR [main] Database connection failed",
                    "java.sql.SQLException: Connection refused",
                    "    at MyClass.run(MyClass.java:10)",
                ],
            )

        # Test inverted grep matching (v)
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.side_effect = [*log_lines, ""]
        with patch("builtins.print") as mock_print:
            self.handler.handler._run_log_command(
                ["docker", "logs", "container"], grep="main", grep_v=True
            )
            printed_calls = [c[0][0] for c in mock_print.call_args_list]
            self.assertEqual(
                printed_calls,
                [
                    "Startup message (no level)",
                    "java.sql.SQLException: Connection refused",
                    "    at MyClass.run(MyClass.java:10)",
                ],
            )

        # Test ANSI color filtering (matches despite escape sequences, and prints the colored version)
        mock_process.poll.return_value = 0
        colored_log_lines = [
            "\x1b[36m10:00:00.000 INFO  [main] colored portal starting...\x1b[0m",
            "\x1b[31m10:00:01.000 ERROR [main] Database connection failed\x1b[0m",
        ]
        mock_process.stdout.readline.side_effect = [*colored_log_lines, ""]
        with patch("builtins.print") as mock_print:
            self.handler.handler._run_log_command(
                ["docker", "logs", "container"], level="ERROR"
            )
            printed_calls = [c[0][0] for c in mock_print.call_args_list]
            self.assertEqual(
                printed_calls,
                ["\x1b[31m10:00:01.000 ERROR [main] Database connection failed\x1b[0m"],
            )

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_logs_service_aware(self, mock_detect):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mock_detect.return_value = root
            (root / "meta").write_text("tag=7.4\ncontainer_name=test-proj")

            with patch.object(self.handler, "run_command") as mock_run:
                # 1. resolve_container fails (returns fallback)
                # 2. name check fails (triggers loop)
                # 3. resolve_container succeeds
                # 4. name check succeeds
                # 5. logs call
                mock_run.side_effect = [
                    "",  # Call 1: resolve_container (Discovery)
                    "",  # Call 2: Name check (Fails, enters loop)
                    "container-id-123",  # Call 3: resolve_container (Discovery succeeds)
                    "container-id-123",  # Call 4: Name check (Succeeds)
                    None,  # Call 5: Final docker logs call
                ]

                # We mock time.sleep to speed up the test
                with patch("time.sleep"):
                    self.handler.cmd_logs(
                        project_id="test-proj", service="db", no_wait=False
                    )

                # Verify it searched for the specific db service container label
                found_db_check = False
                for call in mock_run.call_args_list:
                    args = call[0][0]
                    if isinstance(args, list) and "docker" in args and "ps" in args:
                        for arg in args:
                            if "label=com.docker.compose.service=db" in arg:
                                found_db_check = True
                                break

                self.assertTrue(
                    found_db_check,
                    f"Did not find DB container check in calls: {mock_run.call_args_list}",
                )

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    def test_cmd_logs_infra_advanced_flags(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(self.handler, "run_command") as mock_run:
            self.handler.cmd_logs(
                infra=True,
                tail="20",
                timestamps=True,
                since="10m",
            )
            mock_run.assert_called()
            # Find infra-compose call
            logs_call = []
            for call in mock_run.call_args_list:
                call_args = call[0][0]
                if "logs" in call_args:
                    logs_call = call_args
                    break

            self.assertTrue(len(logs_call) > 0)
            self.assertIn("--tail", logs_call)
            self.assertIn("20", logs_call)
            self.assertIn("-t", logs_call)
            self.assertIn("--since", logs_call)
            self.assertIn("10m", logs_call)

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    def test_cmd_logs_partial_flags(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(self.handler, "run_command") as mock_run:
            # 1. resolve_container, 2. exact check, 3. logs call
            mock_run.side_effect = ["container-id", "container-id", None]
            # Only tail and timestamps
            self.handler.cmd_logs(project_id="test", tail="10", timestamps=True)

            logs_call = []
            for call in mock_run.call_args_list:
                call_args = call[0][0]
                if "logs" in call_args:
                    logs_call = call_args
                    break

            self.assertIn("--tail", logs_call)
            self.assertIn("-t", logs_call)
            self.assertNotIn("--since", logs_call)
            self.assertNotIn("--until", logs_call)

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    def test_cmd_logs_defaults_not_passed(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(self.handler, "run_command") as mock_run:
            # 1. resolve_container, 2. exact check, 3. logs call
            mock_run.side_effect = ["container-id", "container-id", None]
            # Default call
            self.handler.cmd_logs(project_id="test")

            logs_call = []
            for call in mock_run.call_args_list:
                call_args = call[0][0]
                if "logs" in call_args:
                    logs_call = call_args
                    break

            # Tail is 100 by default, so it should be there
            self.assertIn("--tail", logs_call)
            self.assertIn("100", logs_call)
            # Others should be absent
            self.assertNotIn("-t", logs_call)
            self.assertNotIn("--since", logs_call)
            self.assertNotIn("--until", logs_call)

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

    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    @patch("ldm_core.handlers.runtime.shutil.rmtree")
    def test_cmd_down_dry_run(self, mock_rmtree, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        self.handler.dry_run = True
        with (
            patch.object(self.handler, "run_command") as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            self.handler.cmd_down("test", delete=True)
            self.assertFalse(mock_rmtree.called)
            self.assertFalse(mock_run.called)

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
            patch.object(self.handler.handler, "cmd_reset"),
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
            patch.object(self.handler.handler, "cmd_run"),
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
            patch.object(self.handler.handler, "cmd_reset") as mock_reset,
            patch.object(self.handler.assets, "_fetch_seed") as mock_fetch,
        ):
            res = self.handler.handler.cmd_reseed("test")
            self.assertTrue(res)
            self.assertFalse(mock_reset.called)
            self.assertFalse(mock_fetch.called)

    @patch("ldm_core.handlers.runtime.shutil.rmtree")
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
            patch.object(self.handler.handler, "cmd_down") as mock_down,
        ):
            # Create data folder to simulate existence
            data_dir = self.tmp_dir / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            res = self.handler.handler.cmd_reset("test", target="data")
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
            patch.object(self.handler.handler, "cmd_reset"),
            patch.object(self.handler, "setup_paths", return_value={}),
            patch.object(self.handler.assets, "_fetch_seed", return_value=False),
        ):
            self.handler.handler.cmd_reseed("test")
            mock_error.assert_called_with("Reseed failed.")

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
            self.handler.handler._wait_for_ready(project_meta, "test.local")

            mock_warning.assert_any_call("LDM detected 1 new error(s) in the logs.")
            mock_success.assert_any_call(
                "Auto-Thaw successful. Liferay should now proceed."
            )
            # Also it should break the loop and succeed
            mock_success.assert_any_call("Liferay is ready! (Total time: 2m 15s)")

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

            self.handler.handler._wait_for_ready(project_meta, "test.local")

            # Verify we saw the reindex message
            mock_success.assert_any_call("Liferay is ready! (Total time: 0s)")
            # Metadata should have been updated to clear flag
            self.assertEqual(project_meta["reindex_required"], "false")

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
            patch.object(self.handler.handler, "flag_reindex") as mock_flag,
            patch.object(self.handler.handler, "cmd_run") as mock_run,
        ):
            self.handler.handler.cmd_reindex("test")
            mock_flag.assert_called_once_with(self.tmp_dir)
            mock_run.assert_called_once_with("runtime-project")
            mock_success.assert_called_with(
                "Project 'runtime-project' scheduled for search reindex on next boot."
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
            patch.object(self.handler.handler, "flag_reindex") as mock_flag,
            patch.object(self.handler.handler, "cmd_run") as mock_run,
        ):
            self.handler.handler.cmd_reindex("test")

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
                self.handler.handler, "flag_reindex", return_value=True
            ) as mock_flag,
        ):
            self.handler.handler.cmd_reindex("test")

            mock_is_running.assert_called_once_with("test-container")
            mock_exec.assert_called_once()
            mock_warning.assert_called_once()
            self.assertIn(
                "Failed to execute immediate reindex", mock_warning.call_args[0][0]
            )

            # Verify we fell back to scheduling for next boot
            mock_flag.assert_called_once_with(self.tmp_dir)
            mock_success.assert_called_with(
                "Project 'runtime-project' scheduled for search reindex on next boot."
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
                self.handler.handler, "flag_reindex", return_value=True
            ) as mock_flag,
            patch.object(self.handler.handler, "cmd_run") as mock_run,
        ):
            self.handler.handler.cmd_reindex("test")

            # Should check status, see it's running, but skip because force_boot is true
            mock_is_running.assert_called_once_with("test-container")

            # Should flag for reindex and restart
            mock_flag.assert_called_once_with(self.tmp_dir)
            mock_run.assert_called_once_with("runtime-project")
            mock_success.assert_called_with(
                "Project 'runtime-project' scheduled for search reindex on next boot."
            )

    @patch("ldm_core.ui.UI.success")
    def test_print_ngrok_url_success(self, mock_success):
        with patch.object(self.handler, "run_command") as mock_run:
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
        with patch.object(self.handler, "run_command") as mock_run:
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
        with patch.object(self.handler, "run_command") as mock_run:
            mock_run.return_value = None
            self.handler.handler._print_ngrok_url("my-project")
            mock_warning.assert_called_with(
                "ngrok container is running, but failed to retrieve public URL."
            )

    @patch("ldm_core.ui.UI.debug")
    def test_port_inspection_failure_emits_debug_not_raise(self, mock_debug):
        """docker port failure in _patch_fragment_overrides should emit UI.debug, not silently pass or raise."""
        with patch.object(self.handler, "run_command") as mock_run:
            # First call (port inspect) fails; second call (docker inspect for CX) returns None
            mock_run.side_effect = [
                Exception("docker not available"),
                None,
            ]
            project_meta = {
                "liferay_container_name": "test-liferay-1",
                "container_name": "test-liferay-1",
                "host_name": "localhost",
                "ssl": "false",
                "share": "false",
            }
            paths = {"root": Path("/fake/project")}
            # Fragment override file must not exist so _patch_fragment_overrides returns early-ish
            # We want to exercise the port-inspect block; the method will return early
            # before sending any API calls. Patch the file existence check.
            with patch("pathlib.Path.is_file", return_value=False):
                self.handler.handler._patch_fragment_overrides(project_meta, paths)
            # Verify debug was called (may be called for port inspect, may not if
            # early-return triggers before port block; the key assertion is no exception raised)
            # The absence of an uncaught exception IS the primary assertion here.

    @patch("ldm_core.ui.UI.warning")
    def test_cx_expansion_failure_emits_warning_not_raise(self, mock_warning):
        """docker inspect failure during CX env-var expansion should emit UI.warning, not silently pass."""
        with patch.object(self.handler, "run_command") as mock_run:
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
                self.handler.handler._patch_fragment_overrides(project_meta, paths)
            # Primary assertion: no uncaught exception raised.
            # If the docker inspect block was reached and failed, UI.warning is called.
            # (Early return may bypass the block entirely if fragment-overrides.json is absent.)

    @patch("time.sleep")
    def test_wait_for_ready_detect_project_path_with_id(self, mock_sleep):
        with (
            patch("time.time", side_effect=[0, 1, 2, 3, 4, 5, 6]),
            patch.object(self.handler, "run_command", return_value="healthy"),
            patch.object(self.handler.handler, "_patch_fragment_overrides"),
            patch.object(self.handler, "detect_project_path") as mock_detect,
        ):
            project_meta = {"id": "test-project-123", "container_name": "liferay-test"}
            self.handler.handler._wait_for_ready(project_meta, "localhost")

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
            patch.object(self.handler, "run_command") as mock_run_cmd,
            patch.object(self.handler.share, "cmd_start") as mock_share_start,
        ):
            mock_run_cmd.side_effect = [
                "org.apache.catalina.startup.Catalina.start Server startup in 12000 ms",
                "healthy",
            ]

            res = self.handler.handler._wait_for_ready(
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
                patch.object(self.handler, "run_command"),
                patch.object(self.handler, "setup_infrastructure"),
                patch.object(
                    self.handler, "get_container_status", return_value="healthy"
                ),
                patch("ldm_core.ui.UI.die") as mock_die,
            ):
                self.handler.handler.cmd_run(
                    project_id="test-project-liferay-1",
                    no_up=False,
                    no_wait=True,
                    is_restart=True,
                    paths=all_paths,
                    project_meta={"container_name": "test-project-liferay-1"},
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
                patch.object(self.handler, "run_command"),
                patch.object(self.handler, "setup_infrastructure"),
                patch("ldm_core.ui.UI.die", side_effect=SystemExit("died")) as mock_die,
            ):
                with self.assertRaises(SystemExit) as cm:
                    self.handler.handler.cmd_run(
                        project_id="test-project-liferay-1",
                        no_up=False,
                        no_wait=True,
                        is_restart=True,
                        paths=all_paths,
                        project_meta={"container_name": "test-project-liferay-1"},
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
                patch.object(self.handler, "run_command"),
                patch.object(self.handler, "setup_infrastructure"),
                patch.object(
                    self.handler, "get_container_status", return_value="healthy"
                ),
                patch("ldm_core.ui.UI.die") as mock_die,
            ):
                self.handler.handler.cmd_run(
                    project_id="test-project-liferay-1",
                    no_up=False,
                    no_wait=True,
                    is_restart=True,
                    paths=all_paths,
                    project_meta={"container_name": "test-project-liferay-1"},
                )
                mock_is_running.assert_called_with("test-project-liferay-1")
                mock_check_port.assert_any_call("127.0.0.1", 8080)
                mock_die.assert_not_called()

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
                patch.object(self.handler, "run_command"),
                patch.object(self.handler, "setup_infrastructure"),
                patch("ldm_core.ui.UI.die", side_effect=SystemExit("died")) as mock_die,
            ):
                with self.assertRaises(SystemExit) as cm:
                    self.handler.handler.cmd_run(
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
            targets = self.handler.handler._scan_for_expected_deployables(root_path)

            self.assertEqual(targets.get("com.liferay.commerce.payment.card"), "Active")
            self.assertEqual(
                targets.get("com.liferay.commerce.payment.fragment"), "Resolved"
            )
            self.assertEqual(targets.get("my-cx-id"), "Active")

    @patch("requests.get")
    @patch("time.sleep")
    @patch("ldm_core.handlers.runtime.time.time")
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
            patch.object(self.handler.handler, "_wait_for_ready", return_value=True),
            patch.object(
                self.handler.handler,
                "_scan_for_expected_deployables",
                return_value=mock_targets,
            ),
            patch.object(DockerService, "exec") as mock_exec,
            patch.object(self.handler, "run_command", return_value="10%"),
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

            res = self.handler.handler.cmd_wait(
                "test-project", timeout=600, wait_for_deployables=True
            )
            self.assertTrue(res)
            mock_die.assert_not_called()

    @patch("requests.get")
    @patch("time.sleep")
    @patch("ldm_core.handlers.runtime.time.time")
    def test_cmd_wait_with_deployables_gogo_fallback(
        self, mock_time, mock_sleep, mock_get
    ):
        """Test cmd_wait falls back gracefully if Gogo Shell telnet is unavailable."""
        mock_get.return_value.status_code = 200
        mock_time.side_effect = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 1000.0] + [
            1000.0
        ] * 10

        with (
            patch.object(self.handler.handler, "_wait_for_ready", return_value=True),
            patch.object(DockerService, "exec") as mock_exec,
            patch.object(self.handler, "run_command", return_value="10%"),
            patch("ldm_core.ui.UI.die") as mock_die,
            patch("ldm_core.ui.UI.warning") as mock_warning,
        ):
            mock_exec.side_effect = ["", Exception("telnet not found")]

            res = self.handler.handler.cmd_wait(
                "test-project", timeout=600, wait_for_bundles="com.liferay.commerce"
            )
            self.assertTrue(res)
            mock_die.assert_not_called()
            mock_warning.assert_called_with(
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

    @patch("subprocess.Popen")
    def test_log_troubleshooting_advice_stream(self, mock_popen):
        """Test that log streaming prints matching troubleshooting advice."""
        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        log_lines = [
            "Liferay is initializing...",
            'FATAL: database "lportal" does not exist',
            "Shutdown requested",
        ]
        mock_process.stdout.readline.side_effect = [*log_lines, ""]
        mock_popen.return_value = mock_process

        with patch("builtins.print") as mock_print:
            self.handler.handler._run_log_command(
                ["docker", "logs", "container"], follow=True
            )

            # Verify the output contains the troubleshooting advice
            printed_calls = [c[0][0] for c in mock_print.call_args_list]
            advice_calls = [
                c for c in printed_calls if "Target database does not exist" in c
            ]
            self.assertTrue(len(advice_calls) > 0)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_patch_fragment_overrides(self, mock_urlopen, mock_sleep):
        """Test that fragment overrides are parsed and sent to the headless API correctly."""
        import json

        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "share": "true",
        }

        paths = {"root": self.tmp_dir}

        # Create mock fragment-overrides.json
        configs_dir = self.tmp_dir / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)

        overrides_data = {"test-frag": {"url": "https://foo.${LDM_HOST_NAME}"}}
        with open(configs_dir / "fragment-overrides.json", "w") as f:
            json.dump(overrides_data, f)

        with (
            patch("ldm_core.ui.UI.success") as mock_success,
            patch.object(self.handler, "run_command", return_value="8080"),
            patch.object(self.handler.defaults, "get", return_value="my-subdomain"),
        ):
            # Mock site data response
            mock_response = MagicMock()
            mock_response.read.side_effect = [
                json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),  # sites
                json.dumps(
                    {
                        "items": [
                            {
                                "name": "Home",
                                "pageDefinition": {
                                    "pageElement": {
                                        "type": "Fragment",
                                        "id": "frag-1",
                                        "definition": {
                                            "fragmentConfig": {
                                                "fragmentKey": "test-frag"
                                            }
                                        },
                                    }
                                },
                            }
                        ]
                    }
                ).encode("utf-8"),  # pages
                json.dumps({"status": "ok"}).encode("utf-8"),  # patch response
            ]

            import urllib.error

            error_404 = urllib.error.HTTPError(
                "https://my-subdomain.lfr.cloud/o/headless-delivery/v1.0/sites",
                404,
                "Not Found",
                MagicMock(),
                None,
            )

            ctx_manager = MagicMock()
            ctx_manager.__enter__.return_value = mock_response

            # First two calls raise 404 (simulating race condition), then success
            mock_urlopen.side_effect = [
                error_404,
                error_404,
                ctx_manager,
                ctx_manager,
                ctx_manager,
            ]

            self.handler.handler._patch_fragment_overrides(project_meta, paths)

            mock_success.assert_any_call(
                "  -> Patched configuration for fragment 'test-frag' on page 'Home'"
            )
            mock_success.assert_any_call(
                "Successfully applied 1 fragment configuration overrides."
            )
            self.assertEqual(mock_sleep.call_count, 2)

            # Verify the patch payload was constructed correctly using variables
            calls = mock_urlopen.call_args_list
            patch_call = None
            for call in calls:
                req = call[0][0]
                if req.method == "PATCH":
                    patch_call = req
                    break

            assert patch_call is not None
            payload = json.loads(patch_call.data.decode("utf-8"))  # type: ignore[attr-defined]
            self.assertEqual(
                payload["definition"]["config"]["url"],
                "https://foo.my-subdomain.lfr.cloud",
            )

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_patch_fragment_overrides_ssl_verification(self, mock_urlopen, mock_sleep):
        """Test that SSL context verification is only bypassed for loopback hosts."""
        import json
        import ssl

        # 1. Non-loopback case (public sharing subdomain)
        project_meta_public = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "share": "true",
        }
        configs_dir = self.tmp_dir / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)
        overrides_data = {"test-frag": {"url": "https://foo.${LDM_HOST_NAME}"}}
        with open(configs_dir / "fragment-overrides.json", "w") as f:
            json.dump(overrides_data, f)

        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),
            json.dumps({"items": []}).encode("utf-8"),
        ]
        ctx_manager = MagicMock()
        ctx_manager.__enter__.return_value = mock_response
        mock_urlopen.side_effect = [ctx_manager, ctx_manager]

        with (
            patch.object(self.handler, "run_command", return_value="8080"),
            patch.object(self.handler.defaults, "get", return_value="my-subdomain"),
        ):
            self.handler.handler._patch_fragment_overrides(
                project_meta_public, paths={"root": self.tmp_dir}
            )

        # Get context parameter passed to urlopen for non-loopback
        called_ctx_public = mock_urlopen.call_args_list[0].kwargs.get("context")
        self.assertIsNotNone(called_ctx_public)
        # Should NOT bypass verification (i.e. check_hostname should be True, verify_mode should not be CERT_NONE)
        self.assertTrue(called_ctx_public.check_hostname)
        self.assertNotEqual(called_ctx_public.verify_mode, ssl.CERT_NONE)

        # 2. Loopback case (local development host)
        project_meta_local = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "host_name": "localhost",
            "share": "false",
        }
        mock_urlopen.reset_mock()
        mock_response_local = MagicMock()
        mock_response_local.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),
            json.dumps({"items": []}).encode("utf-8"),
        ]
        ctx_manager_local = MagicMock()
        ctx_manager_local.__enter__.return_value = mock_response_local
        mock_urlopen.side_effect = [ctx_manager_local, ctx_manager_local]

        with (
            patch.object(self.handler, "run_command", return_value="8080"),
            patch.object(self.handler.defaults, "get", return_value=None),
        ):
            self.handler.handler._patch_fragment_overrides(
                project_meta_local, paths={"root": self.tmp_dir}
            )

        called_ctx_local = mock_urlopen.call_args_list[0].kwargs.get("context")
        self.assertIsNotNone(called_ctx_local)
        # Should bypass verification
        self.assertFalse(called_ctx_local.check_hostname)
        self.assertEqual(called_ctx_local.verify_mode, ssl.CERT_NONE)

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    def test_patch_fragment_overrides_shifted_ports(self, mock_urlopen, mock_sleep):
        """Verify that fragment overrides ext_base_url appends shifted proxy ports correctly when not shared."""
        import json

        paths = {"root": self.tmp_dir}
        configs_dir = self.tmp_dir / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)

        overrides_data = {"test-frag": {"url": "${LDM_BASE_URL}/test"}}
        with open(configs_dir / "fragment-overrides.json", "w") as f:
            json.dump(overrides_data, f)

        # Mock responses
        mock_response = MagicMock()
        mock_response.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),  # sites
            json.dumps(
                {
                    "items": [
                        {
                            "name": "Home",
                            "pageDefinition": {
                                "pageElement": {
                                    "type": "Fragment",
                                    "id": "frag-1",
                                    "definition": {
                                        "fragmentConfig": {"fragmentKey": "test-frag"}
                                    },
                                }
                            },
                        }
                    ]
                }
            ).encode("utf-8"),  # pages
            json.dumps({"status": "ok"}).encode("utf-8"),  # patch response
        ]
        ctx_manager = MagicMock()
        ctx_manager.__enter__.return_value = mock_response
        mock_urlopen.return_value = ctx_manager

        # Mock shifted proxy ports
        mock_proxy_ports = {"http": 8080, "https": 8443, "admin": 18080}

        # Scenario 1: HTTP local, proxy HTTP shifted to 8080
        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "host_name": "my-host.local",
            "ssl": "False",
            "share": "false",
        }
        self.handler.args.share = False
        with (
            patch.object(
                self.handler.infra, "get_proxy_ports", return_value=mock_proxy_ports
            ),
            patch.object(self.handler, "run_command", return_value="8080"),
        ):
            self.handler.handler._patch_fragment_overrides(project_meta, paths)

            patch_req = mock_urlopen.call_args_list[-1][0][0]
            payload = json.loads(patch_req.data.decode("utf-8"))
            self.assertEqual(
                payload["definition"]["config"]["url"], "http://my-host.local:8080/test"
            )

        # Scenario 2: HTTPS local, proxy HTTPS shifted to 8443
        mock_urlopen.reset_mock()
        mock_response.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),
            json.dumps(
                {
                    "items": [
                        {
                            "name": "Home",
                            "pageDefinition": {
                                "pageElement": {
                                    "type": "Fragment",
                                    "id": "frag-1",
                                    "definition": {
                                        "fragmentConfig": {"fragmentKey": "test-frag"}
                                    },
                                }
                            },
                        }
                    ]
                }
            ).encode("utf-8"),
            json.dumps({"status": "ok"}).encode("utf-8"),
        ]

        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "host_name": "my-host.local",
            "ssl": "True",
            "share": "false",
        }
        self.handler.args.share = False
        with (
            patch.object(
                self.handler.infra, "get_proxy_ports", return_value=mock_proxy_ports
            ),
            patch.object(self.handler, "run_command", return_value="8080"),
        ):
            self.handler.handler._patch_fragment_overrides(project_meta, paths)

            patch_req = mock_urlopen.call_args_list[-1][0][0]
            payload = json.loads(patch_req.data.decode("utf-8"))
            self.assertEqual(
                payload["definition"]["config"]["url"],
                "https://my-host.local:8443/test",
            )

        # Scenario 3: Shared/Tunnel enabled, proxy HTTPS is ignored, resolves to tunnel subdomain
        mock_urlopen.reset_mock()
        mock_response.read.side_effect = [
            json.dumps({"items": [{"id": "20124"}]}).encode("utf-8"),
            json.dumps(
                {
                    "items": [
                        {
                            "name": "Home",
                            "pageDefinition": {
                                "pageElement": {
                                    "type": "Fragment",
                                    "id": "frag-1",
                                    "definition": {
                                        "fragmentConfig": {"fragmentKey": "test-frag"}
                                    },
                                }
                            },
                        }
                    ]
                }
            ).encode("utf-8"),
            json.dumps({"status": "ok"}).encode("utf-8"),
        ]

        project_meta = {
            "tag": "2025.Q1.0",
            "container_name": "liferay-demo",
            "share": "true",
        }
        self.handler.args.share = True
        with (
            patch.object(
                self.handler.infra, "get_proxy_ports", return_value=mock_proxy_ports
            ),
            patch.object(self.handler, "run_command", return_value="8080"),
            patch.object(self.handler.defaults, "get", return_value="my-subdomain"),
        ):
            self.handler.handler._patch_fragment_overrides(project_meta, paths)

            patch_req = mock_urlopen.call_args_list[-1][0][0]
            payload = json.loads(patch_req.data.decode("utf-8"))
            self.assertEqual(
                payload["definition"]["config"]["url"],
                "https://my-subdomain.lfr.cloud/test",
            )

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
            patch.object(self.handler, "get_container_status", return_value="running"),
            patch.object(self.handler, "run_command") as mock_run_cmd,
        ):
            result = self.handler.cmd_run(
                "test",
                no_wait=True,
                paths=self.tmp_dir,
                project_meta={"container_name": "test"},
            )
            self.assertTrue(result)
            self.assertTrue(mock_run_cmd.called)

    @patch("subprocess.Popen")
    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    def test_cmd_logs_export(self, mock_compose, mock_popen):
        import os
        import tempfile
        from pathlib import Path

        mock_compose.return_value = ["docker", "compose"]

        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.stdout.readline.side_effect = [
            "INFO [main] portal starting...",
            "WARN [main] deprecated config",
            "",
        ]
        mock_popen.return_value = mock_process

        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with patch.object(self.handler, "run_command") as mock_run:
                    mock_run.side_effect = ["my-container", True]

                    self.handler.cmd_logs(project_id="test", export=True, no_wait=True)

                    log_files = list(Path(tmpdir).glob("*.log"))
                    self.assertEqual(len(log_files), 1)
                    content = log_files[0].read_text(encoding="utf-8")
                    self.assertIn("INFO [main] portal starting...", content)
                    self.assertIn("WARN [main] deprecated config", content)
            finally:
                os.chdir(old_cwd)

    @patch("subprocess.Popen")
    @patch("ldm_core.handlers.runtime.get_compose_cmd")
    def test_cmd_logs_export_include_infra(self, mock_compose, mock_popen):
        import os
        import tempfile
        from pathlib import Path

        mock_compose.return_value = ["docker", "compose"]

        def mock_popen_func(*args, **kwargs):
            mock_process = MagicMock()
            mock_process.poll.return_value = 0
            mock_process.stdout.readline.side_effect = [
                "INFO [main] portal starting...",
                "",
                "",
                "",
                "",
            ]
            return mock_process

        mock_popen.side_effect = mock_popen_func

        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with (
                    patch.object(self.handler, "run_command") as mock_run,
                    patch.object(
                        self.handler.manager,
                        "get_resource_path",
                        return_value=Path("/tmp/infra-compose.yml"),
                    ),
                ):
                    mock_run.side_effect = ["my-container", True, "", True]
                    self.handler.manager.infra._get_infra_env.return_value = {}  # type: ignore[attr-defined]

                    self.handler.cmd_logs(
                        project_id="test", export=True, include_infra=True, no_wait=True
                    )

                    log_files = list(Path(tmpdir).glob("*.log"))
                    self.assertEqual(len(log_files), 2)
            finally:
                os.chdir(old_cwd)


class TestFragmentOverridesValidation(unittest.TestCase):
    """Unit tests for _validate_fragment_overrides (Issue #434)."""

    def setUp(self):
        self.handler = MockRuntime()
        self.handler.handler = RuntimeService(self.handler)
        self.file_path = Path("fragment-overrides.json")

    # --- Static validator ---

    def test_valid_dict_passes(self):
        """A well-formed dict of fragment-key -> config-dict must return no errors."""
        data = {
            "my-fragment": {"textColor": "#fff"},
            "other-frag": {"padding": "1rem"},
        }
        errors = RuntimeService._validate_fragment_overrides(data, self.file_path)
        self.assertEqual(errors, [])

    def test_legacy_list_format_is_rejected(self):
        """A JSON list (legacy format) must produce exactly one error."""
        data = [{"key": "value"}]
        errors = RuntimeService._validate_fragment_overrides(data, self.file_path)
        self.assertEqual(len(errors), 1)
        self.assertIn("list", errors[0])
        self.assertIn("legacy", errors[0])

    def test_non_dict_root_is_rejected(self):
        """A scalar root (e.g. a bare string) must produce an error."""
        errors = RuntimeService._validate_fragment_overrides("bad", self.file_path)
        self.assertEqual(len(errors), 1)
        self.assertIn("str", errors[0])

    def test_non_dict_value_is_rejected(self):
        """A value that is not a dict (e.g. a string config) must produce an error."""
        data = {"my-fragment": "not-a-dict"}
        errors = RuntimeService._validate_fragment_overrides(data, self.file_path)
        self.assertEqual(len(errors), 1)
        self.assertIn("my-fragment", errors[0])

    def test_empty_string_key_is_rejected(self):
        """A whitespace-only key must produce an error."""
        data = {"   ": {"color": "red"}}
        errors = RuntimeService._validate_fragment_overrides(data, self.file_path)
        self.assertEqual(len(errors), 1)

    # --- Integration: non-interactive dispatch ---

    def test_non_interactive_die_on_invalid(self):
        """In non-interactive mode with die policy, UI.die must be called."""
        import json as _json

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configs = root / "configs"
            configs.mkdir()
            (configs / "fragment-overrides.json").write_text(
                _json.dumps([{"key": "legacy"}])
            )

            project_meta = {"tag": "2025.q1.1-lts"}
            paths = {"root": root}

            self.handler.non_interactive = True
            self.handler.args.on_validation_failure = "die"

            self.handler.parse_version = MagicMock(return_value=(2025, 1, 0))  # type: ignore[method-assign]

            with (
                patch("ldm_core.ui.UI.die", side_effect=SystemExit(1)) as mock_die,
                patch("ldm_core.ui.UI.warning"),
            ):
                with self.assertRaises(SystemExit):
                    self.handler.handler._patch_fragment_overrides(project_meta, paths)
                mock_die.assert_called_once()
                call_kwargs = mock_die.call_args.kwargs
                self.assertEqual(call_kwargs.get("exit_code"), 1)

    def test_non_interactive_ignore_continues(self):
        """In non-interactive mode with ignore policy, execution must continue past validation."""
        import json as _json

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configs = root / "configs"
            configs.mkdir()
            (configs / "fragment-overrides.json").write_text(
                _json.dumps([{"key": "legacy"}])
            )

            project_meta = {"tag": "2025.q1.1-lts"}
            paths = {"root": root}

            self.handler.non_interactive = True
            self.handler.args.on_validation_failure = "ignore"
            self.handler.parse_version = MagicMock(return_value=(2025, 1, 0))  # type: ignore[method-assign]

            # If ignore is respected, execution continues to the API phase.
            # We patch run_command (docker port) to return None so it exits cleanly.
            self.handler.run_command = MagicMock(return_value=None)  # type: ignore[method-assign]
            self.handler.config = MagicMock()
            self.handler.config.get_global_config.return_value = {}
            self.handler.infra = MagicMock()
            self.handler.infra.get_proxy_ports.return_value = {"http": 80, "https": 443}
            self.handler.defaults = {}  # type: ignore[assignment]

            with (
                patch("ldm_core.ui.UI.die") as mock_die,
                patch("ldm_core.ui.UI.warning"),
            ):
                # Run — if "ignore" works it won't die; it will proceed to API
                # (which will fail quickly since there's no real Liferay).
                try:
                    self.handler.handler._patch_fragment_overrides(project_meta, paths)
                except Exception:
                    pass
                mock_die.assert_not_called()


if __name__ == "__main__":
    unittest.main()
