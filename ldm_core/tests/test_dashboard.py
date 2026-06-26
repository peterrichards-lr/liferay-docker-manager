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
        self.manager.config.get_global_config.return_value = {}
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

    @patch("ldm_core.dashboard.server.run_background_ldm_cmd")
    def test_api_start_project_success(self, mock_run_background):
        self.manager.find_dxp_roots.return_value = [
            {"path": Path("/dummy/project1"), "version": "7.4"}
        ]
        self.manager.read_meta.return_value = {"liferay_container_name": "my-project1"}
        response = self.client.post("/api/projects/my-project1/start")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "Starting")
        mock_run_background.assert_called_once_with(["run", "my-project1", "-y"])

    def test_api_start_project_not_found(self):
        self.manager.find_dxp_roots.return_value = []
        response = self.client.post("/api/projects/my-project1/start")
        self.assertEqual(response.status_code, 404)

    @patch("ldm_core.dashboard.server.run_background_ldm_cmd")
    def test_api_stop_project_success(self, mock_run_background):
        self.manager.find_dxp_roots.return_value = [
            {"path": Path("/dummy/project1"), "version": "7.4"}
        ]
        self.manager.read_meta.return_value = {"liferay_container_name": "my-project1"}
        response = self.client.post("/api/projects/my-project1/stop")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "Stopping")
        mock_run_background.assert_called_once_with(["stop", "my-project1", "-y"])

    def test_api_stop_project_not_found(self):
        self.manager.find_dxp_roots.return_value = []
        response = self.client.post("/api/projects/my-project1/stop")
        self.assertEqual(response.status_code, 404)

    @patch("ldm_core.dashboard.server.get_dir_size")
    def test_api_list_snapshots_success(self, mock_get_size):
        mock_get_size.return_value = "15.0 MB"
        self.manager.find_dxp_roots.return_value = [
            {"path": Path("/dummy/project1"), "version": "7.4"}
        ]
        self.manager.read_meta.side_effect = lambda x: (
            {"liferay_container_name": "my-project1"}
            if "dummy" in str(x)
            else {
                "name": "Test Snap",
                "includes_database": "true",
                "includes_volume_assets": "true",
                "includes_client_extensions": "false",
                "includes_osgi_modules": "false",
            }
        )
        # Mock paths setup
        backups_dir = MagicMock()
        backups_dir.exists.return_value = True

        snap_dir = MagicMock()
        snap_dir.is_dir.return_value = True
        snap_dir.name = "2026-06-22T12-00-00Z"

        backups_dir.iterdir.return_value = [snap_dir]
        self.manager.setup_paths.return_value = {"backups": backups_dir}

        response = self.client.get("/api/projects/my-project1/snapshots")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], "2026-06-22T12-00-00Z")
        self.assertEqual(data[0]["name"], "Test Snap")
        self.assertEqual(data[0]["size"], "15.0 MB")
        self.assertTrue(data[0]["includes_database"])
        self.assertTrue(data[0]["includes_volume_assets"])
        self.assertFalse(data[0]["includes_client_extensions"])
        self.assertFalse(data[0]["includes_osgi_modules"])

    @patch("ldm_core.dashboard.server.get_dir_size")
    def test_api_list_snapshots_missing_flags_default_false(self, mock_get_size):
        mock_get_size.return_value = "1.0 MB"
        self.manager.find_dxp_roots.return_value = [
            {"path": Path("/dummy/project1"), "version": "7.4"}
        ]
        self.manager.read_meta.side_effect = lambda x: (
            {"liferay_container_name": "my-project1"}
            if "dummy" in str(x)
            else {"name": "Legacy Snap"}
        )
        # Mock paths setup
        backups_dir = MagicMock()
        backups_dir.exists.return_value = True

        snap_dir = MagicMock()
        snap_dir.is_dir.return_value = True
        snap_dir.name = "2026-06-22T12-00-00Z"

        backups_dir.iterdir.return_value = [snap_dir]
        self.manager.setup_paths.return_value = {"backups": backups_dir}

        response = self.client.get("/api/projects/my-project1/snapshots")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "Legacy Snap")
        # Missing flags should be assumed false
        self.assertFalse(data[0]["includes_database"])
        self.assertFalse(data[0]["includes_volume_assets"])
        self.assertFalse(data[0]["includes_client_extensions"])
        self.assertFalse(data[0]["includes_osgi_modules"])

    def test_api_list_snapshots_not_found(self):
        self.manager.find_dxp_roots.return_value = []
        response = self.client.get("/api/projects/my-project1/snapshots")
        self.assertEqual(response.status_code, 404)

    @patch("ldm_core.dashboard.server.run_background_ldm_cmd")
    def test_api_create_snapshot_success(self, mock_run_background):
        self.manager.find_dxp_roots.return_value = [
            {"path": Path("/dummy/project1"), "version": "7.4"}
        ]
        self.manager.read_meta.return_value = {"liferay_container_name": "my-project1"}

        # Test default (no custom name)
        response = self.client.post("/api/projects/my-project1/snapshot")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "Creating")
        mock_run_background.assert_called_with(["snapshot", "my-project1", "-y"])

        # Test with custom name
        mock_run_background.reset_mock()
        response = self.client.post(
            "/api/projects/my-project1/snapshot", json={"name": "Custom Snap"}
        )
        self.assertEqual(response.status_code, 200)
        mock_run_background.assert_called_with(
            ["snapshot", "my-project1", "-y", "--name", "Custom Snap"]
        )

    @patch("ldm_core.dashboard.server.run_background_ldm_cmd")
    def test_api_restore_snapshot_success(self, mock_run_background):
        self.manager.find_dxp_roots.return_value = [
            {"path": Path("/dummy/project1"), "version": "7.4"}
        ]
        self.manager.read_meta.return_value = {"liferay_container_name": "my-project1"}

        backups_dir = MagicMock()
        backups_dir.exists.return_value = True

        snap_dir = MagicMock()
        snap_dir.is_dir.return_value = True
        snap_dir.name = "2026-06-22T12-00-00Z"

        backups_dir.iterdir.return_value = [snap_dir]
        self.manager.setup_paths.return_value = {"backups": backups_dir}

        response = self.client.post(
            "/api/projects/my-project1/restore/2026-06-22T12-00-00Z"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "Restoring")
        mock_run_background.assert_called_once_with(
            ["restore", "my-project1", "--index", "1", "-y"]
        )

    def test_api_restore_snapshot_not_found(self):
        self.manager.find_dxp_roots.return_value = [
            {"path": Path("/dummy/project1"), "version": "7.4"}
        ]
        self.manager.read_meta.return_value = {"liferay_container_name": "my-project1"}

        backups_dir = MagicMock()
        backups_dir.exists.return_value = True
        backups_dir.iterdir.return_value = []
        self.manager.setup_paths.return_value = {"backups": backups_dir}

        response = self.client.post("/api/projects/my-project1/restore/nonexistent")
        self.assertEqual(response.status_code, 404)

    @patch("ldm_core.dashboard.server._find_project_path")
    def test_api_update_project_property_success(self, mock_find_path):
        mock_find_path.return_value = Path("/dummy/project1")
        self.manager.setup_paths.return_value = {
            "files": Path("/dummy/project1/files"),
            "root": Path("/dummy/project1"),
            "common_dirs": [],
        }
        self.manager.__file__ = "/dummy/manager.py"
        self.manager.config._get_properties_with_metadata.return_value = ({}, set())

        # Mock meta reads for properties GET return
        self.manager.read_meta.return_value = {
            "liferay_container_name": "my-project1",
        }

        # Test PUT payload
        payload = {
            "key": "portal.security.manager.enabled",
            "value": "true",
            "important": True,
        }

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=""),
            patch("pathlib.Path.mkdir"),
            patch("ldm_core.handlers.config.safe_write_text"),
        ):
            response = self.client.put(
                "/api/projects/my-project1/properties", json=payload
            )

            self.assertEqual(response.status_code, 200)
            self.manager.config.update_portal_ext.assert_called_once()
            self.manager.config.cmd_rebuild_properties.assert_called_once_with(
                "my-project1"
            )

    @patch("ldm_core.dashboard.server._find_project_path")
    def test_api_delete_project_property_success(self, mock_find_path):
        mock_find_path.return_value = Path("/dummy/project1")
        self.manager.setup_paths.return_value = {
            "files": Path("/dummy/project1/files"),
            "root": Path("/dummy/project1"),
            "common_dirs": [],
        }
        self.manager.__file__ = "/dummy/manager.py"
        self.manager.config._get_properties_with_metadata.return_value = ({}, set())

        # Mock meta reads for properties GET return
        self.manager.read_meta.return_value = {
            "liferay_container_name": "my-project1",
        }

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=""),
        ):
            response = self.client.delete(
                "/api/projects/my-project1/properties/portal.security.manager.enabled"
            )

            self.assertEqual(response.status_code, 200)
            self.manager.config.remove_portal_ext.assert_called_once()
            self.manager.config.cmd_rebuild_properties.assert_called_once_with(
                "my-project1"
            )

    @patch("ldm_core.dashboard.server._find_project_path")
    def test_api_project_properties_error(self, mock_find_path):
        mock_find_path.return_value = Path("/dummy/project1")
        self.manager.setup_paths.side_effect = Exception(
            "Sensitive filesystem path error: /opt/liferay/data"
        )

        response = self.client.get("/api/projects/my-project1/properties")
        self.assertEqual(response.status_code, 500)
        json_data = response.get_json()
        self.assertEqual(json_data["error"], "Failed to retrieve properties: Exception")

    @patch("ldm_core.dashboard.server._find_project_path")
    def test_api_update_project_property_error(self, mock_find_path):
        mock_find_path.return_value = Path("/dummy/project1")
        self.manager.setup_paths.return_value = {
            "files": Path("/dummy/project1/files"),
            "root": Path("/dummy/project1"),
            "common_dirs": [],
        }
        self.manager.config.update_portal_ext.side_effect = Exception(
            "Sensitive update error"
        )

        payload = {
            "key": "portal.security.manager.enabled",
            "value": "true",
            "important": True,
        }
        with patch("pathlib.Path.exists", return_value=True):
            response = self.client.put(
                "/api/projects/my-project1/properties", json=payload
            )
            self.assertEqual(response.status_code, 500)
            json_data = response.get_json()
            self.assertEqual(json_data["error"], "Failed to update property: Exception")

    @patch("ldm_core.dashboard.server._find_project_path")
    def test_api_delete_project_property_error(self, mock_find_path):
        mock_find_path.return_value = Path("/dummy/project1")
        self.manager.setup_paths.return_value = {
            "files": Path("/dummy/project1/files"),
            "root": Path("/dummy/project1"),
            "common_dirs": [],
        }
        self.manager.config.remove_portal_ext.side_effect = Exception(
            "Sensitive delete error"
        )

        response = self.client.delete(
            "/api/projects/my-project1/properties/portal.security.manager.enabled"
        )
        self.assertEqual(response.status_code, 500)
        json_data = response.get_json()
        self.assertEqual(json_data["error"], "Failed to delete property: Exception")
