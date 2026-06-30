import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.infra import InfraService


class MockInfraManager:
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

    def run_command(self, *args, **kwargs):
        pass

    def get_container_status(self, *args, **kwargs):
        pass

    def get_resource_path(self, *args, **kwargs):
        from ldm_core.utils import get_resource_path

        return get_resource_path(*args, **kwargs)

    def check_port(self, ip, port):
        pass

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
            patch.object(self.manager, "run_command") as mock_run,
        ):
            ssl_port = self.infra.setup_infrastructure(
                "127.0.0.1", 443, use_ssl=True, quiet=True
            )
            self.assertEqual(ssl_port, 453)  # 443 + 10
            self.assertTrue(mock_warning.called)
            warn_msgs = [call[0][0] for call in mock_warning.call_args_list]
            self.assertTrue(any("HTTP" in msg and "90" in msg for msg in warn_msgs))
            self.assertTrue(any("HTTPS" in msg and "453" in msg for msg in warn_msgs))

    def test_get_proxy_ports_not_running(self):
        with patch.object(self.manager, "run_command", return_value="") as mock_run:
            ports = self.infra.get_proxy_ports()
            self.assertEqual(ports, {"http": 80, "https": 443, "admin": 18080})

    def test_get_proxy_ports_running(self):
        mock_inspect_json = '{"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}], "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8443"}], "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18081"}]}'
        with patch.object(
            self.manager, "run_command", return_value=mock_inspect_json
        ) as mock_run:
            ports = self.infra.get_proxy_ports()
            self.assertEqual(ports, {"http": 8080, "https": 8443, "admin": 18081})

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


if __name__ == "__main__":
    unittest.main()
