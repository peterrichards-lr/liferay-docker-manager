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
        self.share.resolve_share_config.return_value = ("lfr-tunnel", "lfr-demo.online")
        from ldm_core.defaults import DefaultsManager
        from ldm_core.handlers.composer import ComposerService
        from ldm_core.handlers.config import ConfigService

        self.defaults = DefaultsManager()
        self.config = ConfigService(self)
        self.config.update_portal_ext = MagicMock()  # type: ignore[method-assign]
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

    def cmd_logs(self, *args, **kwargs):
        return self.handler.cmd_logs(*args, **kwargs)

    def cmd_wait(self, *args, **kwargs):
        return self.handler.cmd_wait(*args, **kwargs)

    def _wait_for_ready(self, *args, **kwargs):
        return self.handler._wait_for_ready(*args, **kwargs)

    def sync_stack(self, *args, **kwargs):
        return self.handler.sync_stack(*args, **kwargs)

    def detect_project_path(self, *args, **kwargs):
        return Path("/tmp/runtime-project")

    def get_resource_path(self, name):
        return Path("/tmp/res") / name

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
                with patch("requests.get") as mock_get:
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

    @patch("socket.gethostbyname")
    def test_cmd_run_seeding_persistence(self, mock_gethost):
        mock_gethost.return_value = "127.0.0.1"
        # Case: New project initialization with seeding
        with tempfile.TemporaryDirectory() as tmp_root:
            root = Path(tmp_root)
            all_paths = {
                "root": root,
                "data": root / "data",
                "deploy": root / "deploy",
                "files": root / "files",
                "state": root / "osgi" / "state",
                "cx": root / "osgi" / "client-extensions",
                "ce_dir": root / "osgi" / "client-extensions",
                "scripts": root / "scripts",
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
                patch.object(self.handler.handler, "sync_stack"),
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
                self.handler.args.archetype = None

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
                self.handler.args.archetype = None
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
                self.handler,
                "setup_paths",
                return_value={
                    "root": self.tmp_dir,
                    "data": self.tmp_dir / "data",
                    "state": self.tmp_dir / "osgi" / "state",
                },
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
            self.handler.args.archetype = None

            # The test checks that we don't die and discover_latest_tag is called
            self.handler.cmd_run("test")

            mock_discover.assert_called_once()
            # verify prefix_filter was passed
            call_kwargs = mock_discover.call_args[1]
            self.assertEqual(call_kwargs.get("prefix_filter"), "2026.q1")
            self.assertEqual(call_kwargs.get("release_type"), "any")

    @patch("ldm_core.utils.discover_latest_tag")
    def test_cmd_run_tag_latest_overrides_metadata(self, mock_discover):
        mock_discover.return_value = "2026.q1.10-lts"
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={
                    "root": self.tmp_dir,
                    "data": self.tmp_dir / "data",
                    "state": self.tmp_dir / "osgi" / "state",
                },
            ),
            patch.object(
                self.handler, "read_meta", return_value={"tag": "2026.q1.9-lts"}
            ),
            patch.object(self.handler, "_pre_flight_checks", return_value=8080),
            patch.object(self.handler, "verify_runtime_environment"),
            patch.object(self.handler.handler, "sync_stack"),
        ):
            self.handler.args.project = "test"
            self.handler.args.tag = None
            self.handler.args.tag_latest = True
            self.handler.args.tag_prefix = None
            self.handler.args.release_type = None
            self.handler.args.no_up = True
            self.handler.args.samples = False
            self.handler.args.db = None
            self.handler.args.host_name = None
            self.handler.args.jvm_args = None
            self.handler.args.port = None
            self.handler.args.snapshot = None
            self.handler.args.archetype = None

            self.handler.cmd_run("test")

            mock_discover.assert_called_once()

    def test_cmd_run_with_reindex_flag(self):
        """Verify that --reindex flag sets the metadata flag."""
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={
                    "root": self.tmp_dir,
                    "data": self.tmp_dir / "data",
                    "state": self.tmp_dir / "osgi" / "state",
                },
            ),
            patch.object(self.handler, "read_meta", return_value={}),
            patch.object(self.handler, "_pre_flight_checks", return_value=8080),
            patch.object(self.handler, "verify_runtime_environment"),
            patch.object(self.handler.handler, "sync_stack"),
            patch.object(self.handler.handler, "flag_reindex") as mock_flag,
        ):
            self.handler.args.reindex = True
            self.handler.args.project = "test"
            self.handler.args.tag = "latest"
            self.handler.args.tag_latest = False
            self.handler.args.tag_prefix = None
            self.handler.args.release_type = None
            self.handler.args.no_up = True
            self.handler.args.samples = False
            self.handler.args.db = None
            self.handler.args.host_name = None
            self.handler.args.jvm_args = None
            self.handler.args.port = None
            self.handler.args.snapshot = None
            self.handler.args.archetype = None

            self.handler.cmd_run("test")

            mock_flag.assert_called_once_with(self.tmp_dir)

    @patch("ldm_core.ui.UI.warning")
    @patch("ldm_core.utils.validate_liferay_tag")
    def test_cmd_run_tag_validation_warning(self, mock_validate, mock_warning):
        """Verify that a warning is logged if the tag is not an official Liferay tag."""
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={
                    "root": self.tmp_dir,
                    "data": self.tmp_dir / "data",
                    "state": self.tmp_dir / "osgi" / "state",
                },
            ),
            patch.object(
                self.handler, "read_meta", return_value={"tag": "2026.q1.6-lts"}
            ),
            patch.object(self.handler, "_pre_flight_checks", return_value=8080),
            patch.object(self.handler, "verify_runtime_environment"),
            patch.object(self.handler.handler, "sync_stack"),
        ):
            # Setup args
            self.handler.args.project = "test"
            self.handler.args.tag = "invalid-tag"
            self.handler.args.tag_latest = False
            self.handler.args.tag_prefix = None
            self.handler.args.release_type = None
            self.handler.args.no_up = True
            self.handler.args.samples = False
            self.handler.args.db = None
            self.handler.args.host_name = None
            self.handler.args.jvm_args = None
            self.handler.args.port = None
            self.handler.args.snapshot = None
            self.handler.args.archetype = None

            # Scenario 1: Tag is invalid -> should trigger warning
            mock_validate.return_value = False
            self.handler.cmd_run("test")
            mock_warning.assert_any_call(
                "Tag 'invalid-tag' is not listed in official Liferay releases. If this is not a custom image, the Docker pull may fail."
            )
            mock_warning.reset_mock()

            # Scenario 2: Tag is valid -> should not trigger warning
            self.handler.args.tag = "2026.q1.7-lts"
            mock_validate.return_value = True
            self.handler.cmd_run("test")
            # Assert warning was not called for tag validation
            for call in mock_warning.call_args_list:
                self.assertNotIn("official Liferay releases", call[0][0])

    @patch("ldm_core.ui.UI.die")
    def test_cmd_run_invalid_archetype(self, mock_die):
        """Verify that an invalid archetype dies."""
        mock_die.side_effect = SystemExit
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={
                    "root": self.tmp_dir,
                    "data": self.tmp_dir / "data",
                    "state": self.tmp_dir / "osgi" / "state",
                },
            ),
            patch.object(self.handler, "read_meta", return_value={}),
        ):
            self.handler.args.project = "test"
            self.handler.args.archetype = "does-not-exist"
            self.handler.args.db = None
            with self.assertRaises(SystemExit):
                self.handler.cmd_run("test")
            mock_die.assert_called_once()

    def test_cmd_run_valid_archetype(self):
        """Verify that a valid archetype sets the project meta."""
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={
                    "root": self.tmp_dir,
                    "data": self.tmp_dir / "data",
                    "state": self.tmp_dir / "osgi" / "state",
                },
            ),
            patch.object(self.handler, "read_meta", return_value={}),
            patch.object(self.handler, "write_meta") as mock_write,
            patch.object(self.handler, "_pre_flight_checks", return_value=8080),
            patch.object(self.handler, "verify_runtime_environment"),
            patch.object(self.handler.handler, "sync_stack"),
            patch("ldm_core.constants.SCRIPT_DIR", self.tmp_dir),
        ):
            # Scaffold fake archetype dir
            arch_dir = (
                self.tmp_dir / "ldm_core" / "resources" / "archetypes" / "keycloak-sso"
            )
            arch_dir.mkdir(parents=True, exist_ok=True)

            self.handler.args.project = "test"
            self.handler.args.archetype = "keycloak-sso"
            self.handler.args.db = None
            self.handler.args.tag = "latest"
            self.handler.args.tag_latest = False
            self.handler.args.tag_prefix = None
            self.handler.args.release_type = None
            self.handler.args.no_up = True
            self.handler.args.samples = False
            self.handler.args.host_name = None
            self.handler.args.jvm_args = None
            self.handler.args.port = None
            self.handler.args.snapshot = None

            self.handler.cmd_run("test")

            # Verify the meta update call included the archetype
            call_args = mock_write.call_args[0][1]
            self.assertEqual(call_args.get("archetype"), "keycloak-sso")

    @patch("ldm_core.ui.UI.die")
    def test_cmd_run_select_non_interactive_dies(self, mock_die):
        mock_die.side_effect = SystemExit
        self.handler.args.select = True
        self.handler.args.project = None
        self.handler.args.project_flag = None
        self.handler.non_interactive = True
        with self.assertRaises(SystemExit):
            self.handler.cmd_run()
        mock_die.assert_called_with(
            "Project selection is not supported in non-interactive mode."
        )

    @patch("ldm_core.ui.UI.die")
    def test_cmd_run_no_project_found_dies(self, mock_die):
        mock_die.side_effect = SystemExit
        self.handler.args.select = False
        self.handler.args.project = "notfound"
        self.handler.args.project_flag = None
        self.handler.non_interactive = True
        with patch.object(self.handler, "detect_project_path", return_value=None):
            with self.assertRaises(SystemExit):
                self.handler.cmd_run("notfound")
            mock_die.assert_called_with(
                "Project not found and no name provided to initialize."
            )

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

            mock_warning.assert_any_call("LDM detected 1 error(s) in the logs.")
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
            mock_run.return_value = MagicMock(
                stdout='{"tunnels": [{"public_url": "https://foo.ngrok.app"}]}'
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
    def test_print_ngrok_url_failure(self, mock_warning):
        with patch.object(self.handler, "run_command") as mock_run:
            mock_run.side_effect = Exception("failed")
            self.handler.handler._print_ngrok_url("my-project")
            mock_warning.assert_called_with(
                "ngrok container is running, but failed to retrieve public URL."
            )

    @patch("ldm_core.ui.UI.ask")
    @patch("ldm_core.ui.UI.success")
    def test_cmd_run_expose_prompt_save(self, mock_success, mock_ask):
        class Args:
            expose = True
            no_up = True
            archetype = None
            project = "test"
            project_flag = None
            select = False
            scale_list = None
            tag = None
            portal = False
            host_name = None
            db = None
            ssl = None
            open = False

        self.handler.args = Args()
        self.handler.handler.args = Args()

        print("DEBUG self.handler:", type(self.handler), self.handler)
        print("DEBUG self.handler.args:", type(self.handler.args), self.handler.args)
        print(
            "DEBUG self.handler.handler.manager:",
            type(self.handler.handler.manager),
            self.handler.handler.manager,
        )
        print(
            "DEBUG self.handler.handler.manager.args:",
            type(self.handler.handler.manager.args),
            self.handler.handler.manager.args,
        )

        # Mock config to have no token
        self.handler.config.get_ngrok_auth_token = MagicMock(return_value=None)  # type: ignore[method-assign]
        self.handler.config.set_ngrok_auth_token = MagicMock()  # type: ignore[method-assign]
        mock_ask.return_value = "my-new-authtoken"

        with (
            patch.object(self.handler, "run_command", return_value="OK"),
            patch.object(
                self.handler.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler.handler,
                "read_meta",
                return_value={"container_name": "test-project"},
            ),
            patch.object(
                self.handler.handler.manager.assets,
                "_ensure_seeded",
                return_value=False,
            ),
            patch.object(self.handler.composer, "write_docker_compose"),
            patch.object(self.handler.handler, "sync_stack"),
        ):
            self.handler.cmd_run("test")
            mock_ask.assert_called_once_with("Enter your ngrok Auth Token")
            self.handler.config.set_ngrok_auth_token.assert_called_with(
                "my-new-authtoken"
            )
            mock_success.assert_called_with(
                "Saved ngrok token to global configuration."
            )

    @patch("socket.gethostbyname")
    def test_cmd_run_persist_osgi_logic(self, mock_gethost):
        mock_gethost.return_value = "127.0.0.1"
        with tempfile.TemporaryDirectory() as tmp_root:
            root = Path(tmp_root)
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

            # Setup directory structure
            all_paths["state"].mkdir(parents=True, exist_ok=True)
            # Create a mock file in state dir to see if it gets wiped
            mock_file = all_paths["state"] / "some_bundle.jar"
            mock_file.write_text("dummy")

            # 1. Test when saved tag is different
            tag_marker = all_paths["state"] / ".ldm_tag"
            tag_marker.write_text("old-tag-version")

            with (
                patch.object(self.handler, "detect_project_path", return_value=root),
                patch.object(self.handler, "setup_paths", return_value=all_paths),
                patch.object(self.handler, "read_meta") as mock_read,
                patch.object(self.handler, "_ensure_seeded", return_value=False),
                patch.object(self.handler, "write_meta") as mock_write,
                patch.object(self.handler, "verify_runtime_environment"),
                patch.object(self.handler, "run_command"),
                patch.object(self.handler.handler, "sync_stack"),
            ):
                mock_read.return_value = {
                    "host_name": "localhost",
                    "container_name": "test-project",
                    "persist_osgi": "true",
                }

                # Mock run arguments
                self.handler.args.no_up = True
                self.handler.args.host_name = None
                self.handler.args.tag = "new-tag-version"
                self.handler.args.samples = False
                self.handler.args.archetype = None
                self.handler.args.persist_osgi = True

                self.handler.cmd_run("test-project")

                # The state should be wiped since tag changed
                self.assertFalse(mock_file.exists())
                self.assertEqual(tag_marker.read_text().strip(), "new-tag-version")

            # 2. Test when saved tag is the same
            mock_file = all_paths["state"] / "some_bundle.jar"
            mock_file.write_text("dummy")

            with (
                patch.object(self.handler, "detect_project_path", return_value=root),
                patch.object(self.handler, "setup_paths", return_value=all_paths),
                patch.object(self.handler, "read_meta") as mock_read,
                patch.object(self.handler, "_ensure_seeded", return_value=False),
                patch.object(self.handler, "write_meta") as mock_write,
                patch.object(self.handler, "verify_runtime_environment"),
                patch.object(self.handler, "run_command"),
                patch.object(self.handler.handler, "sync_stack"),
            ):
                mock_read.return_value = {
                    "host_name": "localhost",
                    "container_name": "test-project",
                    "persist_osgi": "true",
                }

                # Mock run arguments
                self.handler.args.no_up = True
                self.handler.args.host_name = None
                self.handler.args.tag = "new-tag-version"
                self.handler.args.samples = False
                self.handler.args.archetype = None
                self.handler.args.persist_osgi = True

                self.handler.cmd_run("test-project")

                # The mock file should NOT be wiped since tag is the same
                self.assertTrue(mock_file.exists())
                self.assertEqual(tag_marker.read_text().strip(), "new-tag-version")

    @patch("socket.gethostbyname")
    def test_cmd_run_share_metadata(self, mock_gethost):
        mock_gethost.return_value = "127.0.0.1"
        with tempfile.TemporaryDirectory() as tmp_root:
            root = Path(tmp_root)
            all_paths = {
                "root": root,
                "data": root / "data",
                "state": root / "state",
                "deploy": root / "deploy",
                "logs": root / "logs",
                "osgi": root / "osgi",
                "files": root / "files",
            }
            meta_file = root / ".liferay-docker.meta"
            meta_file.write_text("{}")

            class Args:
                share = True
                share_subdomain = "my-custom-sub"
                no_up = True
                project = "test-project"
                project_flag = None
                select = False
                scale_list = None
                tag = "2024.q4.0"
                portal = False
                host_name = None
                db = None
                ssl = None
                open = False
                expose = False
                persist_osgi = False
                reindex = False
                no_vol_cache = False
                internal_state = False
                no_jvm_verify = False
                no_tld_skip = False
                env_type = "dev"
                cpu_limit = None
                mem_limit = None
                archetype = None
                samples = False
                snapshot = None
                timeout = 900

            self.handler.args = Args()
            self.handler.handler.args = Args()

            with (
                patch.object(
                    self.handler.handler, "detect_project_path", return_value=root
                ),
                patch.object(
                    self.handler.handler, "setup_paths", return_value=all_paths
                ),
                patch.object(self.handler.handler, "read_meta", return_value={}),
                patch.object(
                    self.handler.handler.manager.assets,
                    "_ensure_seeded",
                    return_value=False,
                ),
                patch.object(self.handler, "write_meta") as mock_write,
                patch.object(
                    self.handler.handler.manager, "verify_runtime_environment"
                ),
                patch.object(self.handler.handler, "run_command"),
                patch.object(self.handler.handler, "sync_stack"),
            ):
                self.handler.handler.cmd_run("test-project")

                mock_write.assert_called_once()
                meta_arg = mock_write.call_args[0][1]
                self.assertEqual(meta_arg["share"], "true")
                self.assertEqual(meta_arg["share_subdomain"], "my-custom-sub")

    def test_wait_for_ready_triggers_share(self):
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
                self.handler.handler.sync_stack(
                    all_paths,
                    {"container_name": "test-project-liferay-1"},
                    no_up=False,
                    no_wait=True,
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
                    self.handler.handler.sync_stack(
                        all_paths,
                        {"container_name": "test-project-liferay-1"},
                        no_up=False,
                        no_wait=True,
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
                self.handler.handler.sync_stack(
                    all_paths,
                    {"container_name": "test-project-liferay-1"},
                    no_up=False,
                    no_wait=True,
                )
                mock_is_running.assert_called_with("test-project-liferay-1")
                mock_check_port.assert_called_once_with("127.0.0.1", 8080)
                mock_die.assert_not_called()

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
            patch.object(
                self.handler, "run_command", return_value="10%"
            ) as mock_run_cmd,
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
            patch.object(
                self.handler, "run_command", return_value="10%"
            ) as mock_run_cmd,
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


if __name__ == "__main__":
    unittest.main()
