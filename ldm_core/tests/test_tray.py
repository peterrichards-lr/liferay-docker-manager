import os

os.environ["PYSTRAY_BACKEND"] = "dummy"

from unittest.mock import MagicMock, patch

import pytest

from ldm_core.gui.tray import LdmTrayApp
from ldm_core.handlers.tray import TrayService


class TestTrayService:
    @pytest.fixture
    def manager(self):
        m = MagicMock()
        m.dashboard = MagicMock()
        return m

    def test_cmd_tray_wsl(self, manager):
        tray_service = TrayService(manager)
        with patch.object(tray_service, "_is_unsupported_gui_env", return_value=True):
            tray_service.cmd_tray()
            manager.dashboard.cmd_dashboard.assert_called_once_with(
                port=19000, host="127.0.0.1", background=False, token=None
            )

    def test_cmd_tray_gui(self, manager):
        tray_service = TrayService(manager)
        with patch.object(tray_service, "_is_unsupported_gui_env", return_value=False):
            with patch("ldm_core.gui.tray.LdmTrayApp") as MockApp:
                mock_app_instance = MockApp.return_value
                tray_service.cmd_tray()
                mock_app_instance.run.assert_called_once()

    def test_setup_autostart_macos(self, manager, tmp_path):
        tray_service = TrayService(manager)
        with patch("platform.system", return_value="Darwin"):
            with patch("ldm_core.utils.get_actual_home", return_value=tmp_path):
                with patch("subprocess.run"):
                    tray_service.setup_autostart()
                    app_dir = tmp_path / "Applications" / "Liferay Docker Manager.app"
                    plist_file = (
                        tmp_path
                        / "Library"
                        / "LaunchAgents"
                        / "com.liferay.dockermanager.plist"
                    )
                    assert app_dir.exists()
                    assert plist_file.exists()
                    assert (
                        "CFBundleDisplayName"
                        in (app_dir / "Contents" / "Info.plist").read_text()
                    )

    def test_remove_autostart_macos(self, manager, tmp_path):
        tray_service = TrayService(manager)
        plist_dir = tmp_path / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist_file = plist_dir / "com.liferay.dockermanager.plist"
        plist_file.write_text("test")

        with patch("platform.system", return_value="Darwin"):
            with patch("ldm_core.utils.get_actual_home", return_value=tmp_path):
                with patch("subprocess.run"):
                    tray_service.remove_autostart()
                    assert not plist_file.exists()


class TestLdmTrayApp:
    @pytest.fixture
    def manager(self):
        m = MagicMock()
        m.dashboard = MagicMock()
        m.check_docker.return_value = True
        m.docker = MagicMock()
        m.docker.get_running_containers.return_value = ["liferay-1"]
        return m

    def test_init(self, manager):
        with patch("PIL.Image.open"):
            app = LdmTrayApp(manager)
            assert app.current_state == "stopped"

    def test_stop_project_thread(self, manager):
        with patch("PIL.Image.open"):
            app = LdmTrayApp(manager)
            app._stop_project_thread("project")
            manager.runtime.cmd_stop.assert_called_once_with(project_id="project")

    def test_run_dashboard(self, manager):
        with patch("PIL.Image.open"):
            app = LdmTrayApp(manager)
            app._run_dashboard()
            manager.dashboard.cmd_dashboard.assert_called_once()
