import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from ldm_core.handlers.infra import InfraHandler
from ldm_core.handlers.base import BaseHandler


class MockInfraManager(InfraHandler, BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.args.search = False
        self.verbose = False
        self.non_interactive = True

    def get_resource_path(self, filename):
        return Path(f"/tmp/resources/{filename}")


class TestInfraHandler(unittest.TestCase):
    def setUp(self):
        self.manager = MockInfraManager()

    @patch("ldm_core.handlers.infra.get_compose_cmd")
    @patch("ldm_core.handlers.infra.get_actual_home")
    @patch("ldm_core.handlers.infra.os.environ.copy")
    @patch.object(BaseHandler, "run_command")
    def test_setup_infrastructure_basic(
        self, mock_run, mock_env, mock_home, mock_compose
    ):
        mock_compose.return_value = ["docker", "compose"]
        mock_home.return_value = Path("/home/user")
        mock_env.return_value = {}

        # 1. ensure_network check
        # 2. ensure_docker_proxy exists check (ps -a)
        # 3. ensure_docker_proxy running check (ps)
        # 4. compose up
        mock_run.side_effect = [
            "liferay-net",  # network exists
            "proxy_id",  # proxy exists
            "proxy_id",  # proxy running
            "",  # compose up
        ]

        with patch.object(Path, "exists", return_value=True):
            self.manager.setup_infrastructure("127.0.0.1", 443)

        # Verify network check
        mock_run.assert_any_call(["docker", "network", "ls", "--format", "{{.Name}}"])
        # Verify proxy check
        mock_run.assert_any_call(
            ["docker", "ps", "-a", "-q", "-f", "name=liferay-docker-proxy"]
        )

    @patch("ldm_core.handlers.infra.get_compose_cmd")
    @patch.object(BaseHandler, "run_command")
    def test_cmd_infra_down(self, mock_run, mock_compose):
        mock_compose.return_value = ["docker", "compose"]

        with patch.object(Path, "exists", return_value=True):
            with patch.object(self.manager, "_get_infra_env", return_value={}):
                self.manager.cmd_infra_down()

        # Verify compose down was called
        mock_run.assert_any_call(
            [
                "docker",
                "compose",
                "-f",
                "/tmp/resources/infra-compose.yml",
                "down",
                "-v",
            ],
            env={},
            capture_output=False,
        )
        # Verify proxy cleanup
        mock_run.assert_any_call(
            ["docker", "stop", "liferay-docker-proxy"], check=False, capture_output=True
        )

    @patch("ldm_core.handlers.infra.get_actual_home")
    @patch.object(BaseHandler, "run_command")
    def test_setup_global_search_starts_stopped(self, mock_run, mock_home):
        mock_home.return_value = Path("/tmp/home")

        # 1. existence check (ps -a): returns id (exists)
        # 2. running check (ps): returns empty (not running)
        # 3. docker start
        mock_run.side_effect = ["container_id", "", "started"]

        self.manager.setup_global_search()

        mock_run.assert_any_call(["docker", "start", "liferay-search-global"])


if __name__ == "__main__":
    unittest.main()
