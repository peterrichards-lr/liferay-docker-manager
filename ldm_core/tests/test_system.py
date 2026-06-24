import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.system import SystemService


class MockSystemManager:
    def __init__(self):
        self.args = MagicMock()
        self.args.project = None
        self.verbose = False
        self.non_interactive = True

        # Mock services
        self.runtime = MagicMock()
        self.infra = MagicMock()

        # Mock methods
        self.find_dxp_roots = MagicMock(return_value=[])
        self.read_meta = MagicMock(return_value={})
        self.run_command = MagicMock()
        self.cmd_doctor = MagicMock()


class TestSystemService(unittest.TestCase):
    def setUp(self):
        self.manager = MockSystemManager()
        self.system = SystemService(self.manager)

        # Create temp dir for actual home
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_home = Path(self.temp_dir.name)

        # Patch get_actual_home
        self.patcher_home = patch(
            "ldm_core.utils.get_actual_home", return_value=self.temp_home
        )
        self.mock_get_home = self.patcher_home.start()

    def tearDown(self):
        self.patcher_home.stop()
        self.temp_dir.cleanup()

    @patch("ldm_core.ui.UI.ask", return_value="n")
    def test_cmd_nuke_interactive_confirm_aborted(self, mock_ask):
        res = self.system.cmd_nuke(force=False)
        self.assertFalse(res)
        mock_ask.assert_called_once()

    @patch("ldm_core.ui.UI.ask", return_value="y")
    @patch("ldm_core.handlers.system.SystemService._remove_hosts_entries")
    def test_cmd_nuke_forced_success(self, mock_remove_hosts, mock_ask):
        # Setup mock project directories
        project_dir = Path(self.temp_home) / "mock_project"
        project_dir.mkdir()

        self.manager.find_dxp_roots.return_value = [{"path": project_dir}]
        self.manager.read_meta.return_value = {
            "liferay_container_name": "test-project-liferay"
        }

        # Setup global caches and configs
        ldm_dir = self.temp_home / ".ldm"
        ldm_dir.mkdir()
        (ldm_dir / "certs").mkdir()
        (ldm_dir / "cache").mkdir()
        (ldm_dir / "registry.json").write_text("{}")

        ldmrc = self.temp_home / ".ldmrc"
        ldmrc.write_text("config=true")

        # Run nuke
        res = self.system.cmd_nuke(force=True, keep_config=False)
        self.assertTrue(res)

        # Verify projects down
        self.manager.runtime.cmd_down.assert_any_call(
            project_id=project_dir.name, delete=False
        )
        # Verify infra down
        self.manager.runtime.cmd_down.assert_any_call(infra=True)

        # Verify hosts entries removed
        mock_remove_hosts.assert_called_once_with(all_ldm=True)

        # Verify Docker commands
        self.assertTrue(self.manager.run_command.called)

        # Verify global resources deleted
        self.assertFalse((ldm_dir / "certs").exists())
        self.assertFalse((ldm_dir / "cache").exists())
        self.assertFalse((ldm_dir / "registry.json").exists())
        self.assertFalse(ldmrc.exists())

    @patch("ldm_core.handlers.system.SystemService._remove_hosts_entries")
    def test_cmd_nuke_keep_config(self, mock_remove_hosts):
        self.manager.find_dxp_roots.return_value = []

        ldm_dir = self.temp_home / ".ldm"
        ldm_dir.mkdir()
        (ldm_dir / "certs").mkdir()

        ldmrc = self.temp_home / ".ldmrc"
        ldmrc.write_text("config=true")

        res = self.system.cmd_nuke(force=True, keep_config=True)
        self.assertTrue(res)

        self.assertFalse((ldm_dir / "certs").exists())
        # config file should still exist!
        self.assertTrue(ldmrc.exists())

    @patch("socket.socket")
    def test_cmd_rescue_global_infra(self, mock_socket_class):
        # Mock port conflict check
        mock_socket = MagicMock()
        mock_socket_class.return_value = mock_socket

        # Call global rescue
        res = self.system.cmd_rescue(project_id=None)
        self.assertTrue(res)

        # Verify shared network creation
        self.manager.run_command.assert_any_call(
            ["docker", "network", "create", "liferay-net"], check=False
        )

        # Verify SSL renewal and infra setup
        self.manager.runtime.cmd_renew_ssl.assert_called_once_with(all_projects=True)
        self.manager.infra.cmd_infra_setup.assert_called_once()

    @patch("socket.socket")
    @patch("ldm_core.ui.UI.warning")
    def test_cmd_rescue_global_infra_port_conflict(
        self, mock_warning, mock_socket_class
    ):
        mock_socket = MagicMock()
        # Make bind raise socket.error to simulate port conflict
        mock_socket.bind.side_effect = OSError("Mock port conflict")
        mock_socket_class.return_value = mock_socket

        res = self.system.cmd_rescue(project_id=None)
        self.assertTrue(res)

        # Warning should be printed twice (once for port 80, once for port 443)
        self.assertEqual(mock_warning.call_count, 2)

    @patch("ldm_core.handlers.system.SystemService.detect_project_path")
    @patch("ldm_core.handlers.system.SystemService.read_meta")
    def test_cmd_rescue_project_success(self, mock_read_meta, mock_detect_path):
        # Create temp project root
        project_root = Path(self.temp_home) / "my_project"
        project_root.mkdir()

        # Create postgres lock
        pg_data = project_root / "data"
        pg_data.mkdir()
        postmaster_pid = pg_data / "postmaster.pid"
        postmaster_pid.write_text("12345")

        # Create OSGi lock
        osgi_state = project_root / "osgi" / "state"
        osgi_state.mkdir(parents=True)
        osgi_lock = osgi_state / ".lock"
        osgi_lock.write_text("")

        mock_detect_path.return_value = project_root
        mock_read_meta.return_value = {"liferay_container_name": "my-project-liferay"}

        res = self.system.cmd_rescue(project_id="my_project")
        self.assertTrue(res)

        # Verify containers stopped
        self.manager.runtime.cmd_down.assert_called_once_with(
            project_id=project_root.name, delete=False
        )

        # Verify postgres lock and OSGi lock deleted
        self.assertFalse(postmaster_pid.exists())
        self.assertFalse(osgi_lock.exists())

        # Verify SSL renewed
        self.manager.runtime.cmd_renew_ssl.assert_called_once_with(
            project_id=project_root.name
        )

        # Verify containers started
        self.manager.runtime.cmd_run.assert_called_once_with(
            project_id=project_root.name
        )

        # Verify doctor verification run
        self.manager.cmd_doctor.assert_called_once_with(project_id=project_root.name)

    @patch(
        "ldm_core.handlers.system.SystemService.detect_project_path", return_value=None
    )
    @patch("ldm_core.ui.UI.die", side_effect=SystemExit)
    def test_cmd_rescue_project_not_found(self, mock_die, mock_detect_path):
        with self.assertRaises(SystemExit):
            self.system.cmd_rescue(project_id="nonexistent")
        mock_die.assert_called_once_with("Project 'nonexistent' not found.")

    def test_properties_file_auto_repair_success(self):
        # Create a properties file with syntax errors
        pe_file = Path(self.temp_home) / "portal-ext.properties"
        content = (
            "key1=val1\\\n"
            "    # Comments here\n"
            "key2=val2\\\n"
            "    \n"
            "key3=val3\\\n"
            "company.security.auth.type=email\n"
            "key4=val4\\\n"
            "  param1=value1;\\\n"  # Valid multiline - indented
            "  param2=value2;\n"
            "key5=val5\\"  # Ends with backslash at end of file
        )
        pe_file.write_text(content)

        # Run properties rescue helper
        repaired = self.system._rescue_properties_file(pe_file)
        self.assertTrue(repaired)

        # Read back content and verify corrections
        new_content = pe_file.read_text()

        # Verify line 1 backslash is stripped (followed by comment)
        self.assertIn("key1=val1\n", new_content)
        # Verify line 3 backslash is stripped (followed by empty line)
        self.assertIn("key2=val2\n", new_content)
        # Verify line 5 backslash is stripped (followed by key3 starting key4)
        self.assertIn("key3=val3\n", new_content)
        # Verify key4 multiline is PRESERVED (since next lines start with spaces)
        self.assertIn("key4=val4\\\n  param1=value1;\\\n  param2=value2;", new_content)
        # Verify key5 backslash is stripped (last line of file)
        self.assertIn("key5=val5\n", new_content)


if __name__ == "__main__":
    unittest.main()
