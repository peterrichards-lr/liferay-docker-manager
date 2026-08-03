import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.infra import InfraService


class MockInfraManager:
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True
        self.defaults = MagicMock()
        self.target: str | None = None

    def run_command(self, *args, **kwargs):
        pass

    def get_container_status(self, *args, **kwargs):
        pass

    def get_resource_path(self, *args, **kwargs):
        from ldm_core.utils import get_resource_path

        return get_resource_path(*args, **kwargs)

    def check_port(self, ip, port):
        pass

    def get_resolved_ip(self, *args, **kwargs):
        return "127.0.0.1"

    def find_available_port(self, ip, start_port, exclude=None):
        pass


class TestInfraService(unittest.TestCase):
    def setUp(self):
        self.manager = MockInfraManager()
        self.infra = InfraService(self.manager)

    @patch("ldm_core.ui.UI.confirm", return_value=True)
    def test_fix_cert_permissions_success(self, mock_confirm):
        with (
            patch("os.getuid", return_value=1000, create=True),
            patch("os.getgid", return_value=1000, create=True),
            patch.object(self.manager, "run_command") as mock_run,
        ):
            res = self.infra._fix_cert_permissions(Path("/tmp/certs"))
            self.assertTrue(res)
            self.assertTrue(mock_run.called)
            cmd = mock_run.call_args[0][0]
            self.assertIn("chown", cmd)

    def test_get_infra_env_basic(self):
        env = self.infra._get_infra_env("192.168.1.1", 8443)
        self.assertEqual(env["LDM_RESOLVED_IP"], "192.168.1.1")
        self.assertEqual(env["LDM_SSL_PORT"], "8443")

    @patch("ldm_core.docker_service.DockerService.is_running", return_value=False)
    @patch("ldm_core.ui.UI.warning")
    def test_setup_infrastructure_port_conflict(self, mock_warning, mock_is_running):
        self.manager.args.search = False
        with (
            patch.object(
                self.manager,
                "check_port",
                side_effect=lambda _, port: port not in {80, 443},
            ),
            patch.object(
                self.manager,
                "find_available_port",
                side_effect=lambda _, port, exclude=None: port + 10,  # noqa: ARG005
            ),
            patch.object(self.manager, "run_command"),
        ):
            ssl_port = self.infra.setup_infrastructure(
                "127.0.0.1", 443, use_ssl=True, quiet=True
            )
            self.assertEqual(ssl_port, 453)  # 443 + 10
            self.assertTrue(mock_warning.called)
            warn_msgs = [call[0][0] for call in mock_warning.call_args_list]
            self.assertTrue(any("HTTP" in msg and "90" in msg for msg in warn_msgs))
            self.assertTrue(any("HTTPS" in msg and "453" in msg for msg in warn_msgs))

    @patch("ldm_core.docker_service.DockerService.is_running", return_value=False)
    def test_setup_infrastructure_custom_ssl_port(self, mock_is_running):
        self.manager.args.search = False
        with (
            patch.object(self.manager, "check_port", return_value=True),
            patch.object(self.manager, "run_command") as mock_run_cmd,
        ):
            ssl_port = self.infra.setup_infrastructure(
                "127.0.0.1", 8443, use_ssl=True, quiet=True
            )
            self.assertEqual(ssl_port, 8443)
            # Verify custom ssl_port was passed to Docker Compose env
            self.assertTrue(mock_run_cmd.called)
            called_env = mock_run_cmd.call_args[1].get("env", {})
            self.assertEqual(called_env.get("LDM_SSL_PORT"), "8443")

    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    @patch("ldm_core.handlers.infra.InfraService.get_proxy_ports")
    def test_setup_infrastructure_force_recreate(self, mock_get_ports, mock_is_running):
        self.manager.args.search = False
        mock_get_ports.return_value = {"http": 80, "https": 443, "admin": 18080}
        with (
            patch.object(self.manager, "check_port", return_value=True),
            patch.object(self.manager, "run_command") as mock_run_cmd,
        ):
            # Without force_recreate, port reverts to running port (443)
            ssl_port = self.infra.setup_infrastructure(
                "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=False
            )
            self.assertEqual(ssl_port, 443)

            # With force_recreate, port stays 8443 and --force-recreate is passed
            ssl_port_recreate = self.infra.setup_infrastructure(
                "127.0.0.1", 8443, use_ssl=True, quiet=True, force_recreate=True
            )
            self.assertEqual(ssl_port_recreate, 8443)
            called_args = mock_run_cmd.call_args[0][0]
            self.assertIn("--force-recreate", called_args)

    @patch("ldm_core.handlers.infra.InfraService.setup_infrastructure")
    def test_cmd_infra_setup_ssl_port_from_arg(self, mock_setup):
        import sys

        self.manager.check_docker = MagicMock(return_value=True)
        self.manager.get_resolved_ip = MagicMock(return_value="127.0.0.1")
        self.manager.detect_project_path = MagicMock(return_value=None)
        self.manager.args.ssl_port = 8443
        self.manager.args.force_recreate = True
        self.manager.args.database_mode = "local"
        self.infra.cmd_infra_setup()
        mock_setup.assert_called_once_with(
            "0.0.0.0" if sys.platform == "darwin" else "127.0.0.1",
            8443,
            use_ssl=True,
            use_shared_db=False,
            force_recreate=True,
        )

    @patch("ldm_core.handlers.infra.InfraService.setup_infrastructure")
    def test_cmd_infra_setup_ssl_port_from_env(self, mock_setup):
        import os
        import sys

        self.manager.check_docker = MagicMock(return_value=True)
        self.manager.get_resolved_ip = MagicMock(return_value="127.0.0.1")
        self.manager.detect_project_path = MagicMock(return_value=None)
        self.manager.args.ssl_port = None
        self.manager.args.force_recreate = False
        self.manager.args.database_mode = "local"
        with patch.dict(os.environ, {"LDM_SSL_PORT": "9443"}):
            self.infra.cmd_infra_setup()
            mock_setup.assert_called_once_with(
                "0.0.0.0" if sys.platform == "darwin" else "127.0.0.1",
                9443,
                use_ssl=True,
                use_shared_db=False,
                force_recreate=False,
            )

    def test_get_proxy_ports_not_running(self):
        with patch("ldm_core.docker_service.DockerService.inspect", return_value=""):
            ports = self.infra.get_proxy_ports()
            self.assertEqual(ports, {"http": 80, "https": 443, "admin": 18080})

    def test_get_proxy_ports_running(self):
        mock_inspect_json = '{"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}], "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8443"}], "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18081"}]}'
        with patch(
            "ldm_core.docker_service.DockerService.inspect",
            return_value=mock_inspect_json,
        ):
            ports = self.infra.get_proxy_ports(target_name="aws-1")
            self.assertEqual(ports, {"http": 8080, "https": 8443, "admin": 18081})

    def test_get_proxy_ports_null_settings(self):
        with patch(
            "ldm_core.docker_service.DockerService.inspect", return_value="null"
        ):
            ports = self.infra.get_proxy_ports()
            self.assertEqual(ports, {"http": 80, "https": 443, "admin": 18080})

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.detail")
    def test_setup_global_database(self, _mock_detail, _mock_info, _mock_exists):
        with (
            patch.object(
                self.manager, "run_command", return_value="accepting connections"
            ) as mock_run,
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            self.infra.setup_global_database()

            self.assertTrue(mock_run.called)
            run_cmd = None
            for call in mock_run.call_args_list:
                args = call[0][0]
                if (
                    isinstance(args, list)
                    and "run" in args
                    and "liferay-db-global" in args
                ):
                    run_cmd = args
                    break
            self.assertIsNotNone(run_cmd)
            assert isinstance(run_cmd, list)
            self.assertIn("POSTGRES_DB=lportal", run_cmd)
            self.assertIn("-v", run_cmd)
            self.assertIn("liferay-db-global-data:/var/lib/postgresql/data", run_cmd)

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.handlers.infra.get_actual_home", return_value=Path("/tmp"))
    @patch("ldm_core.utils.reclaim_volume_permissions")
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.detail")
    def test_setup_global_search_defaults(
        self, _mock_detail, _mock_info, _mock_reclaim, _mock_home, _mock_exists
    ):
        self.manager.defaults.get.side_effect = lambda _key, default=None: default
        with (
            patch.object(
                self.manager,
                "run_command",
                return_value='{"cluster_name": "liferay-cluster"}',
            ) as mock_run,
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            self.infra.setup_global_search(force=True)

            self.assertTrue(mock_run.called)
            # Find the docker run command invocation
            run_cmd = None
            for call in mock_run.call_args_list:
                args = call[0][0]
                if (
                    isinstance(args, list)
                    and "run" in args
                    and "liferay-search-global" in args
                ):
                    run_cmd = args
                    break
            self.assertIsNotNone(run_cmd)
            assert isinstance(run_cmd, list)
            self.assertIn("ES_JAVA_OPTS=-Xms512m -Xmx512m", run_cmd)
            self.assertIn("processors=1", run_cmd)

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.handlers.infra.get_actual_home", return_value=Path("/tmp"))
    @patch("ldm_core.utils.reclaim_volume_permissions")
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.ui.UI.detail")
    def test_setup_global_search_overrides(
        self, _mock_detail, _mock_info, _mock_reclaim, _mock_home, _mock_exists
    ):
        with (
            patch.object(
                self.manager,
                "run_command",
                return_value='{"cluster_name": "liferay-cluster"}',
            ) as mock_run,
            patch.object(self.manager, "get_container_status", return_value="running"),
            patch.object(self.manager.defaults, "get", return_value="256m"),
        ):
            self.infra.setup_global_search(force=True)

            self.assertTrue(mock_run.called)
            run_cmd = None
            for call in mock_run.call_args_list:
                args = call[0][0]
                if (
                    isinstance(args, list)
                    and "run" in args
                    and "liferay-search-global" in args
                ):
                    run_cmd = args
                    break
            self.assertIsNotNone(run_cmd)
            assert isinstance(run_cmd, list)
            self.assertIn("ES_JAVA_OPTS=-Xms256m -Xmx256m", run_cmd)
            self.assertIn("processors=1", run_cmd)

    @patch("ldm_core.handlers.infra.UI")
    @patch("ldm_core.utils.has_shared_projects")
    def test_cmd_infra_down_guard(self, mock_has_shared, mock_ui):
        """Verify cmd_infra_down guards properly."""
        mock_has_shared.return_value = True
        mock_ui.confirm.return_value = False
        self.manager.non_interactive = False

        # Should abort
        res = self.infra.cmd_infra_down()
        self.assertFalse(res)

        # Should proceed
        mock_ui.confirm.return_value = True
        with patch.object(self.manager, "run_command") as mock_run:
            self.infra.cmd_infra_down()
            mock_run.assert_called()

    @patch("ldm_core.docker_service.DockerService.exists", return_value=False)
    @patch("ldm_core.handlers.infra.get_actual_home", return_value=Path("/tmp"))
    @patch("ldm_core.utils.reclaim_volume_permissions")
    @patch("ldm_core.ui.UI.die")
    @patch("shutil.rmtree")
    @patch("time.sleep")
    def test_setup_global_search_recursion_limit(
        self, mock_sleep, mock_rmtree, mock_die, mock_reclaim, mock_home, mock_exists
    ):
        mock_die.side_effect = SystemExit(3)
        with (
            patch.object(self.manager, "run_command", return_value=""),
            patch.object(self.manager, "get_container_status", return_value="running"),
        ):
            with self.assertRaises(SystemExit):
                self.infra.setup_global_search(force=True)
            mock_die.assert_called_once()
            self.assertEqual(mock_die.call_args[1].get("exit_code"), 3)

    def test_cmd_infra_setup_remote_target(self) -> None:
        """Test cmd_infra_setup passes target context prefix to docker compose command."""
        self.manager.target = "aws-1"
        self.manager.args.search = False
        self.manager.check_docker = MagicMock(return_value=True)  # type: ignore[attr-defined]
        self.manager.detect_project_path = MagicMock(return_value=None)  # type: ignore[attr-defined]
        with (
            patch.object(self.manager, "run_command") as mock_run,
            patch("ldm_core.docker_service.get_active_target") as mock_target,
        ):
            from ldm_core.config import TargetNode

            mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
            mock_run.return_value = "OK"

            self.infra.cmd_infra_setup()
            mock_run.assert_called()
            called_cmds = [call[0][0] for call in mock_run.call_args_list]
            has_context = any(
                "--context" in cmd and "aws-1" in cmd
                for cmd in called_cmds
                if isinstance(cmd, list)
            )
            self.assertTrue(has_context)


if __name__ == "__main__":
    unittest.main()
