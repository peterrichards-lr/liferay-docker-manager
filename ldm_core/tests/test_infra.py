import unittest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

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

    @patch.object(InfraHandler, "cmd_infra_down")
    @patch.object(InfraHandler, "cmd_infra_setup")
    def test_cmd_infra_restart(self, mock_setup, mock_down):
        self.manager.cmd_infra_restart()
        self.assertTrue(mock_down.called)
        self.assertTrue(mock_setup.called)

    @patch("ldm_core.handlers.infra.get_actual_home")
    @patch.object(BaseHandler, "run_command")
    def test_setup_global_search_starts_stopped(self, mock_run, mock_home):
        mock_home.return_value = Path("/tmp/home")

        # 1. existence check (ps -a): returns id (exists)
        # 2. running check (ps): returns empty (not running)
        # 3. docker start
        # 4. repo registration (PUT)
        mock_run.side_effect = ["container_id", "", "started", "repo_ok"]

        self.manager.setup_global_search()

        mock_run.assert_any_call(["docker", "start", "liferay-search-global"])

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
    @patch.object(BaseHandler, "run_command")
    def test_setup_global_search_full_init(self, mock_run, mock_sleep, mock_home):
        mock_home.return_value = Path("/tmp/home")

        # 1. exists check (ps -a) -> empty (new container)
        # 2. docker run
        # 3. _reclaim_permissions (data)
        # 4. _reclaim_permissions (backup)
        # 5. status check (inspect) -> running
        # 6. health check 1 -> success ("cluster_name": ...)
        # 7. repo registration (PUT)
        # 8. plugin list
        # 9. plugin install (x4)
        # 10. docker restart
        # 11. status check after restart (inspect) -> running
        # 12. health check after restart -> success
        # 13. Final repo registration check -> success
        mock_run.side_effect = [
            "",  # 1
            "new_id",  # 2
            "reclaimed_data",  # 3
            "reclaimed_backup",  # 4
            "running",  # 5
            '{"cluster_name": "liferay-cluster"}',  # 6
            "repo_ok",  # 7
            "plugins...",  # 8
            "",
            "",
            "",
            "",  # 9
            "restarted",  # 10
            "running",  # 11
            '{"cluster_name": "liferay-cluster"}',  # 12
            "repo_ok",  # 13
        ]

        with patch("pathlib.Path.mkdir"):
            self.manager.setup_global_search()

        # Robust check: Just verify that the expected types of calls happened
        # instead of fragile exact counting of readiness loop iterations.
        status_checks = 0
        repo_registrations = 0
        for call in mock_run.call_args_list:
            cmd_str = " ".join([str(x) for x in call[0][0]])
            if "curl" in cmd_str and "9200" in cmd_str:
                if "PUT" in cmd_str:
                    repo_registrations += 1
                else:
                    status_checks += 1

        self.assertGreaterEqual(status_checks, 2)  # At least initial and post-restart
        self.assertGreaterEqual(repo_registrations, 1)  # At least one registration

    @patch("platform.system", return_value="Linux")
    @patch.object(BaseHandler, "run_command")
    def test_reclaim_permissions(self, mock_run, mock_platform):
        from pathlib import Path

        test_path = Path("/tmp/test-dir")

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "parent", new_callable=PropertyMock) as mock_parent,
        ):
            mock_parent.return_value.resolve.return_value = Path("/tmp")

            self.manager._reclaim_permissions(test_path)

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
