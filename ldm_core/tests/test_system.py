import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.system import SystemService


class MockSystemManager:
    defaults: MagicMock
    workspace: MagicMock

    def __init__(self):
        self.args = MagicMock()
        self.args.project = None
        self.verbose = False
        self.non_interactive = True
        self.dry_run = False

        # Mock services
        self.runtime = MagicMock()
        self.infra = MagicMock()
        self.diagnostics = MagicMock()

        # Mock methods
        self.find_dxp_roots = MagicMock(return_value=[])
        self.read_meta = MagicMock(return_value={})
        self.run_command = MagicMock()


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
        self.manager.diagnostics.cmd_doctor.assert_called_once_with(
            project_id=project_root.name
        )

    @patch(
        "ldm_core.handlers.system.SystemService.detect_project_path", return_value=None
    )
    @patch("ldm_core.ui.UI.die", side_effect=SystemExit)
    def test_cmd_rescue_project_not_found(self, mock_die, mock_detect_path):
        with self.assertRaises(SystemExit):
            self.system.cmd_rescue(project_id="nonexistent")
        mock_die.assert_called_once_with("Project 'nonexistent' not found.")

    @patch("ldm_core.ui.UI.success")
    def test_cmd_rescue_clear_lock_exists(self, mock_success):
        # Setup temp project dir and lock file
        project_root = Path(self.temp_home) / "my_project"
        project_root.mkdir()
        lock_file = project_root / ".liferay-docker" / ".ldm_lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text("PID: 12345", encoding="utf-8")

        with patch.object(
            self.system, "detect_project_path", return_value=project_root
        ):
            res = self.system.cmd_rescue(project_id="my_project", clear_lock=True)
            self.assertTrue(res)
            self.assertFalse(lock_file.exists())
            mock_success.assert_called_once()
            self.assertIn("Cleared project lock", mock_success.call_args[0][0])

    @patch("ldm_core.ui.UI.detail")
    def test_cmd_rescue_clear_lock_not_found(self, mock_detail):
        project_root = Path(self.temp_home) / "my_project"
        project_root.mkdir()

        with patch.object(
            self.system, "detect_project_path", return_value=project_root
        ):
            res = self.system.cmd_rescue(project_id="my_project", clear_lock=True)
            self.assertTrue(res)
            mock_detail.assert_called_once_with("No active project lock file found.")

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

    @patch("ldm_core.ui.UI.success")
    def test_cmd_init_ci_success(self, mock_success):
        self.manager.defaults = MagicMock()
        self.manager.defaults.get.return_value = "release"
        self.manager.workspace = MagicMock()
        self.manager.workspace._parse_github_repo.return_value = ("my-org", "my-repo")

        def run_cmd_side_effect(args, **kwargs):
            if "rev-parse" in args:
                return str(self.temp_home)
            if "remote" in args:
                return "https://github.com/my-org/my-repo.git"
            return ""

        self.manager.run_command.side_effect = run_cmd_side_effect

        res = self.system.cmd_init_ci()
        self.assertTrue(res)

        target_file = (
            self.temp_home / ".github" / "workflows" / "ldm-package-release.yml"
        )
        self.assertTrue(target_file.exists())
        content = target_file.read_text()
        self.assertIn("name: LDM Package Release", content)
        self.assertIn("on:\n  release:", content)
        self.assertIn('--repo "my-org/my-repo"', content)

    @patch("ldm_core.ui.UI.success")
    def test_cmd_init_ci_trigger_presets(self, mock_success):
        self.manager.defaults = MagicMock()
        self.manager.defaults.get.return_value = "release"
        self.manager.workspace = MagicMock()
        self.manager.workspace._parse_github_repo.return_value = ("my-org", "my-repo")

        def run_cmd_side_effect(args, **kwargs):
            if "rev-parse" in args:
                return str(self.temp_home)
            if "remote" in args:
                return "https://github.com/my-org/my-repo.git"
            return ""

        self.manager.run_command.side_effect = run_cmd_side_effect

        # Test tag trigger
        res = self.system.cmd_init_ci(trigger="tag", workflow_name="tag.yml")
        self.assertTrue(res)
        content = (self.temp_home / ".github" / "workflows" / "tag.yml").read_text()
        self.assertIn("tags:\n      - 'v*'", content)

        # Test push trigger
        res = self.system.cmd_init_ci(trigger="push", workflow_name="push.yml")
        self.assertTrue(res)
        content = (self.temp_home / ".github" / "workflows" / "push.yml").read_text()
        self.assertIn("branches:\n      - master", content)

        # Test manual trigger
        res = self.system.cmd_init_ci(trigger="manual", workflow_name="manual.yml")
        self.assertTrue(res)
        content = (self.temp_home / ".github" / "workflows" / "manual.yml").read_text()
        self.assertIn("on:\n  workflow_dispatch:", content)
        self.assertNotIn("push:", content)

    @patch("ldm_core.ui.UI.success")
    def test_cmd_init_ci_defaults_preset(self, mock_success):
        self.manager.defaults = MagicMock()
        self.manager.defaults.get.return_value = "manual"
        self.manager.workspace = MagicMock()
        self.manager.workspace._parse_github_repo.return_value = ("my-org", "my-repo")

        def run_cmd_side_effect(args, **kwargs):
            if "rev-parse" in args:
                return str(self.temp_home)
            if "remote" in args:
                return "https://github.com/my-org/my-repo.git"
            return ""

        self.manager.run_command.side_effect = run_cmd_side_effect

        res = self.system.cmd_init_ci()
        self.assertTrue(res)
        content = (
            self.temp_home / ".github" / "workflows" / "ldm-package-release.yml"
        ).read_text()
        self.assertIn("on:\n  workflow_dispatch:", content)
        self.assertNotIn("release:", content)

    @patch("ldm_core.ui.UI.confirm")
    @patch("ldm_core.ui.UI.success")
    def test_cmd_init_ci_collision_interactive(self, mock_success, mock_confirm):
        self.manager.defaults = MagicMock()
        self.manager.defaults.get.return_value = "release"
        self.manager.workspace = MagicMock()
        self.manager.workspace._parse_github_repo.return_value = ("my-org", "my-repo")
        self.manager.non_interactive = False

        def run_cmd_side_effect(args, **kwargs):
            if "rev-parse" in args:
                return str(self.temp_home)
            if "remote" in args:
                return "https://github.com/my-org/my-repo.git"
            return ""

        self.manager.run_command.side_effect = run_cmd_side_effect

        workflows_dir = self.temp_home / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        target_file = workflows_dir / "ldm-package-release.yml"
        target_file.write_text("existing content")

        # 1. Reject prompt
        mock_confirm.return_value = False
        res = self.system.cmd_init_ci()
        self.assertFalse(res)
        self.assertEqual(target_file.read_text(), "existing content")

        # 2. Accept prompt
        mock_confirm.return_value = True
        res = self.system.cmd_init_ci()
        self.assertTrue(res)
        self.assertNotEqual(target_file.read_text(), "existing content")

    @patch("ldm_core.ui.UI.warning")
    @patch("ldm_core.ui.UI.success")
    def test_cmd_init_ci_collision_non_interactive(self, mock_success, mock_warning):
        self.manager.defaults = MagicMock()
        self.manager.defaults.get.return_value = "release"
        self.manager.workspace = MagicMock()
        self.manager.workspace._parse_github_repo.return_value = ("my-org", "my-repo")
        self.manager.non_interactive = True

        def run_cmd_side_effect(args, **kwargs):
            if "rev-parse" in args:
                return str(self.temp_home)
            if "remote" in args:
                return "https://github.com/my-org/my-repo.git"
            return ""

        self.manager.run_command.side_effect = run_cmd_side_effect

        workflows_dir = self.temp_home / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        target_file = workflows_dir / "ldm-package-release.yml"
        target_file.write_text("existing content")

        res = self.system.cmd_init_ci()
        self.assertTrue(res)
        self.assertNotEqual(target_file.read_text(), "existing content")
        mock_warning.assert_called_once()

    def test_cmd_nuke_dry_run(self):
        self.manager.dry_run = True
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

        ldmrc = self.temp_home / ".ldmrc"
        ldmrc.write_text("config=true")

        res = self.system.cmd_nuke(force=False, keep_config=False)
        self.assertTrue(res)

        # Assert no deletions happened
        self.assertTrue(project_dir.exists())
        self.assertTrue((ldm_dir / "certs").exists())
        self.assertTrue(ldmrc.exists())
        # Assert no mutating helper calls
        self.assertFalse(self.manager.runtime.cmd_down.called)

    def test_cmd_rescue_global_dry_run(self):
        self.manager.dry_run = True
        res = self.system.cmd_rescue(project_id=None)
        self.assertTrue(res)

        # Assert no shared network creation or ssl renewal called
        self.assertFalse(self.manager.run_command.called)
        self.assertFalse(self.manager.runtime.cmd_renew_ssl.called)
        self.assertFalse(self.manager.infra.cmd_infra_setup.called)

    @patch("ldm_core.handlers.system.SystemService.detect_project_path")
    @patch("ldm_core.handlers.system.SystemService.read_meta")
    def test_cmd_rescue_project_dry_run(self, mock_read_meta, mock_detect_path):
        self.manager.dry_run = True
        # Create temp project root
        project_root = Path(self.temp_home) / "my_project"
        project_root.mkdir()

        # Create postgres lock
        pg_data = project_root / "data"
        pg_data.mkdir()
        postmaster_pid = pg_data / "postmaster.pid"
        postmaster_pid.write_text("12345")

        mock_detect_path.return_value = project_root
        mock_read_meta.return_value = {"liferay_container_name": "my-project-liferay"}

        res = self.system.cmd_rescue(project_id="my_project")
        self.assertTrue(res)

        # Assert postmaster.pid lock file was NOT deleted
        self.assertTrue(postmaster_pid.exists())
        # Assert cmd_down, cmd_renew_ssl, cmd_run, cmd_doctor were NOT called
        self.assertFalse(self.manager.runtime.cmd_down.called)
        self.assertFalse(self.manager.runtime.cmd_renew_ssl.called)
        self.assertFalse(self.manager.runtime.cmd_run.called)
        self.assertFalse(self.manager.diagnostics.cmd_doctor.called)

    @patch("ldm_core.handlers.system.UI")
    @patch("ldm_core.utils.has_shared_projects")
    def test_nuke_global_volume_guard(self, mock_has_shared, mock_ui):
        """Verify cmd_nuke guards global volume drops correctly."""
        mock_ui.ask.return_value = "y"  # Confirm nuke
        mock_ui.confirm.return_value = False  # Deny global volume drop
        mock_has_shared.return_value = True  # Ensure prompt triggers

        self.system.cmd_nuke(force=False)
        # Should not drop global volumes
        self.manager.run_command.assert_any_call(
            ["docker", "volume", "prune", "-f"], check=False
        )
        run_calls = self.manager.run_command.call_args_list
        for call in run_calls:
            if (
                "docker" in call.args[0]
                and "volume" in call.args[0]
                and "rm" in call.args[0]
            ):
                self.assertNotIn("liferay-db-global-data", call.args[0])

        # Now confirm it
        mock_ui.confirm.return_value = True
        self.system.cmd_nuke(force=False)
        self.manager.run_command.assert_any_call(
            [
                "docker",
                "volume",
                "rm",
                "-f",
                "liferay-db-global-data",
                "liferay-search-global-data",
            ],
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
