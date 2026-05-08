import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.infra import InfraHandler


class MockInfraManager(InfraHandler, BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

    def run_command(self, *args, **kwargs):
        # Allow individual tests to override this via patch.object
        return super().run_command(*args, **kwargs)


class TestInfraHandler(unittest.TestCase):
    def setUp(self):
        self.manager = MockInfraManager()

    @patch("ldm_core.handlers.infra.get_actual_home")
    @patch.object(InfraHandler, "setup_global_search")
    @patch.object(BaseHandler, "run_command")
    def test_cmd_infra_setup_basic(self, mock_run, mock_search, mock_home):
        mock_home.return_value = Path("/tmp/home")

        # 1. Network check (docker network ls)
        # 2. Proxy existence check (docker ps -a -q -f name=liferay-docker-proxy)
        # 3. Docker Run for proxy (docker run ...)
        # 4. Compose Up for traefik (docker compose up ...)
        mock_run.side_effect = ["liferay-net", "", "proxy_started", "compose_ok"]

        self.manager.cmd_infra_setup()

        # Check if compose was called
        self.assertTrue(
            any("compose" in str(c) and "up" in str(c) for c in mock_run.call_args_list)
        )

    @patch("ldm_core.handlers.infra.get_actual_home")
    @patch.object(InfraHandler, "setup_global_search")
    @patch.object(BaseHandler, "run_command")
    def test_setup_infrastructure_quiet(self, mock_run, mock_search, mock_home):
        mock_home.return_value = Path("/tmp/home")

        # Just return a generic success value for all run_command calls
        mock_run.return_value = "ok"

        # Call with quiet=True
        self.manager.setup_infrastructure("127.0.0.1", 443, use_ssl=True, quiet=True)

        # Verify that compose up was called with capture_output=True
        compose_call_found = False
        for call in mock_run.call_args_list:
            if "compose" in str(call) and "up" in str(call):
                compose_call_found = True
                self.assertTrue(call.kwargs.get("capture_output", False))
                break

        self.assertTrue(compose_call_found, "Docker compose up should have been called")

    @patch.object(InfraHandler, "cmd_infra_down")
    @patch.object(InfraHandler, "cmd_infra_setup")
    def test_cmd_infra_restart(self, mock_setup, mock_down):
        self.manager.cmd_infra_restart()
        self.assertTrue(mock_down.called)
        self.assertTrue(mock_setup.called)

    @patch("ldm_core.handlers.infra.get_actual_home")
    @patch("ldm_core.docker_service.run_command")
    @patch.object(BaseHandler, "run_command")
    def test_setup_global_search_starts_stopped(
        self, mock_run, mock_utils_run, mock_home
    ):
        mock_home.return_value = Path("/tmp/home")

        # 1. existence check (ps -a): returns id (exists)
        # 2. running check (ps): returns empty (not running)
        # 3. docker start
        # 4. repo registration (PUT)
        mock_utils_run.side_effect = ["container_id", "", "started", "repo_ok"]
        mock_run.side_effect = ["repo_ok"]  # For self.run_command

        self.manager.setup_global_search()

        mock_utils_run.assert_any_call(
            ["docker", "start", "liferay-search-global"],
            check=False,
            capture_output=True,
        )

        # Verify repo registration via robust check
        found_reg = False
        for call in mock_run.call_args_list:
            cmd_str = " ".join([str(x) for x in call[0][0]])
            if "PUT" in cmd_str and "_snapshot/liferay_backup" in cmd_str:
                found_reg = True
                break
        self.assertTrue(found_reg)

    @patch("ldm_core.handlers.infra.get_actual_home")
    @patch("time.sleep")
    @patch("ldm_core.docker_service.run_command")
    @patch.object(BaseHandler, "run_command")
    def test_setup_global_search_full_init(
        self, mock_run, mock_utils_run, mock_sleep, mock_home
    ):
        mock_home.return_value = Path("/tmp/home")

        # Provide side_effect to ensure exists check returns empty first, then 'running'
        def utils_run_mock(*args, **kwargs):
            cmd = args[0]
            if "ps" in cmd and "-a" in cmd:
                return ""
            return "running"

        mock_utils_run.side_effect = utils_run_mock
        mock_run.return_value = '{"cluster_name": "liferay-cluster"}'

        with patch("pathlib.Path.mkdir"):
            self.manager.setup_global_search()

        # Robust check: Just verify that the expected types of calls happened
        # instead of fragile exact counting of readiness loop iterations.
        status_checks = 0
        repo_registrations = 0

        # Combine calls from both mock_run and mock_utils_run depending on who made them
        all_calls = list(mock_run.call_args_list) + list(mock_utils_run.call_args_list)

        for call in all_calls:
            cmd_str = " ".join([str(x) for x in call[0][0]])
            if "curl" in cmd_str and "9200" in cmd_str:
                if "PUT" in cmd_str:
                    repo_registrations += 1
                else:
                    status_checks += 1

        self.assertGreaterEqual(status_checks, 2)  # At least initial and post-restart
        self.assertGreaterEqual(repo_registrations, 1)  # At least one registration

    @patch("platform.system", return_value="Linux")
    @patch("ldm_core.utils.run_command")
    def test_reclaim_permissions(self, mock_run, mock_platform):
        from pathlib import Path

        from ldm_core.utils import reclaim_volume_permissions

        test_path = Path("/tmp/test-dir")

        with (
            patch.object(Path, "exists", return_value=True),
        ):
            reclaim_volume_permissions(test_path)

            # Verify it called docker run to chown and chmod
            found_docker = False
            for call in mock_run.call_args_list:
                args = call[0][0]
                if "docker" in args and "run" in args and "chown" in str(args):
                    found_docker = True
                    break

            self.assertTrue(found_docker)


if __name__ == "__main__":
    unittest.main()
