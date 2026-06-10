import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.dashboard.server import app
from ldm_core.handlers.dashboard import DashboardService


class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.manager = MagicMock()
        # Ensure CLI args exist on manager
        self.manager.args = MagicMock()
        self.dashboard_service = DashboardService(self.manager)
        app.config["MANAGER"] = self.manager
        self.client = app.test_client()

    @patch("ldm_core.dashboard.server.run_command")
    def test_dashboard_api_projects(self, mock_run_command):
        # Mock find_dxp_roots
        self.manager.find_dxp_roots.return_value = [
            {"path": Path("/dummy/project1"), "version": "7.4"}
        ]
        # Mock read_meta
        self.manager.read_meta.return_value = {
            "liferay_container_name": "my-project1",
            "host_name": "liferay.local",
            "port": "8080",
            "ssl": "false",
            "db_type": "postgresql",
            "archetype": "theme",
        }
        mock_run_command.return_value = "running\n"

        response = self.client.get("/api/projects")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "my-project1")
        self.assertEqual(data[0]["status"], "Running")
        self.assertEqual(data[0]["url"], "http://liferay.local:8080")
        self.assertEqual(data[0]["db_type"], "postgresql")
        self.assertEqual(data[0]["version"], "7.4")

    @patch("ldm_core.dashboard.server.run_command")
    def test_dashboard_api_logs_success(self, mock_run_command):
        self.manager.find_dxp_roots.return_value = [
            {"path": Path("/dummy/project1"), "version": "7.4"}
        ]
        self.manager.read_meta.return_value = {"liferay_container_name": "my-project1"}
        mock_run_command.return_value = "some container logs\nline 2"

        response = self.client.get("/api/logs/my-project1")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["logs"], "some container logs\nline 2")
        mock_run_command.assert_called_with(
            ["docker", "logs", "--tail", "200", "my-project1"], check=False
        )

    def test_dashboard_api_logs_invalid(self):
        response = self.client.get("/api/logs/invalid@name")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_dashboard_api_logs_not_found(self):
        self.manager.find_dxp_roots.return_value = []
        response = self.client.get("/api/logs/nonexistent")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.get_json())

    @patch("ldm_core.dashboard.server.app.run")
    def test_cmd_dashboard_foreground(self, mock_flask_run):
        # Test foreground execution where Flask start_server is called
        with patch("ldm_core.handlers.dashboard.UI") as mock_ui:
            self.dashboard_service.cmd_dashboard(
                port=19000, host="127.0.0.1", background=False
            )
            mock_flask_run.assert_called_once_with(
                host="127.0.0.1", port=19000, debug=False
            )

    @patch("subprocess.Popen")
    def test_cmd_dashboard_background(self, mock_popen):
        with patch("ldm_core.handlers.dashboard.UI") as mock_ui:
            self.dashboard_service.cmd_dashboard(
                port=19000, host="127.0.0.1", background=True
            )
            mock_popen.assert_called_once()
            args, kwargs = mock_popen.call_args
            self.assertIn("dashboard", args[0])
            self.assertIn("--port", args[0])
            self.assertIn("19000", args[0])
            self.assertIn("--host", args[0])
            self.assertIn("127.0.0.1", args[0])

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_dashboard_index_route(self, mock_read_text, mock_exists):
        mock_exists.return_value = True
        mock_read_text.return_value = "<html>Dashboard UI</html>"
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.decode("utf-8"), "<html>Dashboard UI</html>")

    @patch("pathlib.Path.exists")
    def test_dashboard_index_route_not_found(self, mock_exists):
        mock_exists.return_value = False
        response = self.client.get("/")
        self.assertEqual(response.status_code, 404)
