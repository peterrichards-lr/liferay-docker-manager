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


class TestLogs(unittest.TestCase):
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

    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
    def test_cmd_logs_advanced_flags(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(BaseHandler, "run_command") as mock_run:
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

            with patch.object(BaseHandler, "run_command") as mock_run:
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

    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
    def test_cmd_logs_infra_advanced_flags(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(BaseHandler, "run_command") as mock_run:
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

    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
    def test_cmd_logs_partial_flags(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(BaseHandler, "run_command") as mock_run:
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

    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
    def test_cmd_logs_defaults_not_passed(self, mock_compose):
        mock_compose.return_value = ["docker", "compose"]
        with patch.object(BaseHandler, "run_command") as mock_run:
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

    @patch("subprocess.Popen")
    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
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
                with patch.object(BaseHandler, "run_command") as mock_run:
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
    @patch("ldm_core.runtime.orchestration.get_compose_cmd")
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
                    patch.object(BaseHandler, "run_command") as mock_run,
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
