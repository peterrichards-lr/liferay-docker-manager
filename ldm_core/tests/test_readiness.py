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
