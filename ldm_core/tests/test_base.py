import unittest
import os
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.workspace import WorkspaceHandler


class MockBaseManager(WorkspaceHandler, BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True


class TestBaseDiscovery(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    def test_find_dxp_roots_multi_dir(self):
        import tempfile

        # Create a temporary environment
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            cwd_dir = base_path / "cwd"
            other_dir = base_path / "other"

            for d in [cwd_dir, other_dir]:
                d.mkdir()
                # Create a project in each
                proj = d / f"proj_{d.name}"
                proj.mkdir()
                (proj / ".liferay-docker.meta").write_text("tag=latest")

            with patch("ldm_core.handlers.base.Path.cwd", return_value=cwd_dir):
                with patch.dict(os.environ, {"LDM_WORKSPACE": str(other_dir)}):
                    with patch(
                        "ldm_core.handlers.base.Path.home",
                        return_value=base_path / "nonexistent",
                    ):
                        roots = self.handler.find_dxp_roots()

                        names = [r["path"].name for r in roots]
                        # In the new Hardened LDM, LDM_WORKSPACE is EXCLUSIVE.
                        # It should find projects in 'other' but NOT 'cwd'.
                        self.assertIn("proj_other", names)
                        self.assertNotIn("proj_cwd", names)

    def test_detect_project_path_scenarios(self):
        import tempfile

        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            cwd_dir = base_path / "cwd"
            workspace_dir = base_path / "ws"

            for d in [cwd_dir, workspace_dir]:
                d.mkdir()

            # 1. Test Absolute Path
            proj_abs = base_path / "abs-proj"
            proj_abs.mkdir()
            (proj_abs / ".liferay-docker.meta").write_text("tag=latest")
            res = self.handler.detect_project_path(str(proj_abs))
            self.assertEqual(res.resolve(), proj_abs.resolve())

            # 2. Test Relative to CWD
            proj_rel = cwd_dir / "rel-proj"
            proj_rel.mkdir()
            (proj_rel / ".liferay-docker.meta").write_text("tag=latest")
            with patch("ldm_core.handlers.base.Path.cwd", return_value=cwd_dir):
                res = self.handler.detect_project_path("rel-proj")
                self.assertEqual(res.resolve(), proj_rel.resolve())

            # 3. Test in LDM_WORKSPACE
            proj_ws = workspace_dir / "ws-proj"
            proj_ws.mkdir()
            (proj_ws / ".liferay-docker.meta").write_text("tag=latest")
            with patch("ldm_core.handlers.base.Path.cwd", return_value=cwd_dir):
                with patch.dict(os.environ, {"LDM_WORKSPACE": str(workspace_dir)}):
                    # Mock find_dxp_roots search dirs logic (it will use the env var)
                    res = self.handler.detect_project_path("ws-proj")
                    self.assertEqual(res.resolve(), proj_ws.resolve())

    @patch("ldm_core.handlers.base.os.chmod")
    def test_migrate_layout_routes_permissions(self, mock_chmod):
        # Verify that routes/default/dxp is created and 777'd
        root = Path("/tmp/proj")
        paths = {
            "root": root,
            "routes": root / "osgi" / "routes",
            "marketplace": root / "osgi" / "marketplace",
        }

        with patch.object(Path, "mkdir"):
            with patch.object(Path, "exists", return_value=False):
                self.handler.migrate_layout(paths)

                # Check if chmod was called with 0o777 for routes
                # 0o777 is 511 in decimal
                chmod_calls = [str(c) for c in mock_chmod.call_args_list]
                self.assertTrue(any("511" in call for call in chmod_calls))


class TestBaseHardening(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    @unittest.skipIf(
        platform.system().lower() == "windows", "Colima tests only valid on POSIX"
    )
    def test_get_colima_mount_flags_home(self):
        # Path in home directory
        with patch.dict(os.environ, {"USER": "peter", "SUDO_USER": ""}):
            paths = [Path("/Users/peter/repos/project")]
            flags = self.handler.get_colima_mount_flags(paths)
            self.assertIn("--mount /Users/$(whoami):w", flags)

    @unittest.skipIf(
        platform.system().lower() == "windows", "Colima tests only valid on POSIX"
    )
    def test_get_colima_mount_flags_volumes(self):
        # Path on external volume
        paths = [Path("/Volumes/SanDisk/projects")]
        flags = self.handler.get_colima_mount_flags(paths)
        self.assertIn("--mount /Volumes/SanDisk:w", flags)

    @unittest.skipIf(
        platform.system().lower() == "windows", "Colima tests only valid on POSIX"
    )
    def test_get_colima_mount_flags_multiple(self):
        # Mixed paths
        with patch.dict(os.environ, {"USER": "peter"}):
            paths = [Path("/Users/peter/certs"), Path("/Volumes/SanDisk/project")]
            flags = self.handler.get_colima_mount_flags(paths)
            self.assertIn("--mount /Users/$(whoami):w", flags)
            self.assertIn("--mount /Volumes/SanDisk:w", flags)

    def test_detect_project_path_hardening(self):
        # Case: A file exists with the project name, but no directory
        # This used to cause a crash in ldm init
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file that looks like a project name
            fake_project_file = Path(tmpdir) / "some-file-project"
            fake_project_file.touch()

            # Temporarily change CWD so relative paths hit the tmpdir
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                # Should exit via UI.die if it's a file and for_init is True
                with self.assertRaises(SystemExit):
                    self.handler.detect_project_path("some-file-project", for_init=True)
            finally:
                os.chdir(old_cwd)

    @patch("ldm_core.handlers.base.BaseHandler.get_resolved_ip")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.workspace.WorkspaceHandler.scan_client_extensions")
    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_validate_project_dns_filtering(
        self, mock_detect, mock_scan, mock_meta, mock_resolve
    ):
        # Setup: forge.demo with 3 extensions
        mock_detect.return_value = Path(".")
        mock_meta.return_value = {"host_name": "forge.demo"}
        mock_resolve.return_value = "127.0.0.1"  # All resolve initially

        mock_scan.return_value = [
            {
                "id": "active-ext",
                "kind": "Deployment",
                "deploy": True,
                "has_load_balancer": True,
            },
            {
                "id": "job-ext",
                "kind": "Job",
                "deploy": True,
                "has_load_balancer": False,
            },
            {
                "id": "disabled-ext",
                "kind": "Deployment",
                "deploy": False,
                "has_load_balancer": True,
            },
        ]

        # Run validation
        with patch.object(
            BaseHandler,
            "setup_paths",
            return_value={"root": Path("."), "cx": Path("."), "ce_dir": Path(".")},
        ):
            with patch.object(Path, "exists", return_value=True):
                ok, unresolved, non_local = self.handler.validate_project_dns(".")

        # Verify: Only "active-ext" should have been checked
        self.assertTrue(ok)
        self.assertEqual(len(non_local), 0)

        # Now mock failure for the active one
        def resolve_side_effect(host):
            if host == "active-ext.forge.demo":
                return None
            return "127.0.0.1"

        mock_resolve.side_effect = resolve_side_effect
        with patch.object(
            BaseHandler,
            "setup_paths",
            return_value={"root": Path("."), "cx": Path("."), "ce_dir": Path(".")},
        ):
            with patch.object(Path, "exists", return_value=True):
                ok, unresolved, non_local = self.handler.validate_project_dns(".")

        self.assertFalse(ok)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0], "active-ext.forge.demo")

    @patch("ldm_core.handlers.base.platform.system")
    @patch("ldm_core.handlers.base.get_actual_home")
    @patch("ldm_core.handlers.base.BaseHandler.get_resource_path")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.symlink_to")
    @patch("pathlib.Path.unlink")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.is_symlink")
    def test_refresh_man_symlink(
        self,
        mock_is_symlink,
        mock_exists,
        mock_unlink,
        mock_symlink,
        mock_mkdir,
        mock_res_path,
        mock_home,
        mock_system,
    ):
        # Setup mocks for Linux scenario
        mock_system.return_value = "Linux"
        mock_home.return_value = Path("/tmp/home")
        mock_res_path.return_value = Path("/app/resources/ldm.1")
        mock_is_symlink.return_value = True
        mock_exists.return_value = True

        # Run the logic
        self.handler._refresh_man_symlink()

        # Verify interactions
        mock_mkdir.assert_called()
        mock_unlink.assert_called()
        mock_symlink.assert_called_with(Path("/app/resources/ldm.1"))


class TestBaseCompletion(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/bin/zsh"})
    def test_cmd_completion_zsh_instructions(self):
        # No argument: should show instructions
        import io
        from contextlib import redirect_stdout
        from ldm_core.utils import strip_ansi

        f = io.StringIO()
        with redirect_stdout(f):
            self.handler.cmd_completion(target_shell=None)

        clean_output = strip_ansi(f.getvalue())
        self.assertIn('eval "$(ldm completion zsh)"', clean_output)
        self.assertIn("=== LDM Shell Completion ===", clean_output)

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/bin/bash"})
    def test_cmd_completion_bash_instructions(self, mock_stdout):
        # No argument: should show instructions
        self.handler.cmd_completion(target_shell=None)
        output = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertIn('eval "$(ldm completion bash)"', output)

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/usr/bin/fish"})
    def test_cmd_completion_fish_instructions(self, mock_stdout):
        # No argument: should show instructions
        self.handler.cmd_completion(target_shell=None)
        output = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertIn("ldm completion fish | source", output)

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "powershell.exe"})
    def test_cmd_completion_powershell_instructions(self, mock_stdout):
        # No argument: should show instructions
        self.handler.cmd_completion(target_shell=None)
        output = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertIn(
            "ldm completion powershell | Out-String | Invoke-Expression", output
        )

    @patch("sys.stdout", new_callable=MagicMock)
    def test_cmd_completion_powershell_code(self, mock_stdout):
        # Specific argument: should show the bridge script
        self.handler.cmd_completion(target_shell="powershell")
        output = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
        self.assertIn("Register-ArgumentCompleter", output)
        self.assertIn("$env:COMP_LINE", output)
        self.assertNotIn("=== LDM Shell Completion ===", output)

    @patch("sys.stdout", new_callable=MagicMock)
    def test_cmd_completion_zsh_code(self, mock_stdout):
        # Specific argument: should show raw code
        with patch("argcomplete.shellcode", return_value="# ZSH CODE"):
            self.handler.cmd_completion(target_shell="zsh")
            output = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
            self.assertIn("# ZSH CODE", output)
            self.assertNotIn("=== LDM Shell Completion ===", output)

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("sys.stderr", new_callable=MagicMock)
    def test_cmd_completion_generation_suppresses_ui(self, mock_stderr, mock_stdout):
        # Verify that providing a shell argument DOES NOT print UI headings to stdout
        with patch("argcomplete.shellcode", return_value="# CODE"):
            self.handler.cmd_completion(target_shell="bash")
            output = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
            self.assertEqual("# CODE\n", output)
            self.assertNotIn("=== LDM Shell Completion ===", output)

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("sys.stderr", new_callable=MagicMock)
    def test_cmd_completion_generation_zsh_boilerplate(self, mock_stderr, mock_stdout):
        # Verify that providing zsh includes the necessary boilerplate
        with patch("argcomplete.shellcode", return_value="# CODE"):
            self.handler.cmd_completion(target_shell="zsh")
            output = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
            self.assertIn("compinit", output)
            self.assertIn("# CODE", output)

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("sys.stderr", new_callable=MagicMock)
    def test_cmd_completion_error_goes_to_stderr(self, mock_stderr, mock_stdout):
        # Verify that a failure in shellcode generation doesn't dump instructions to stdout
        with patch("argcomplete.shellcode", side_effect=Exception("Failed")):
            self.handler.cmd_completion(target_shell="zsh")
            stdout_val = "".join(
                call.args[0] for call in mock_stdout.write.call_args_list
            )
            stderr_val = "".join(
                call.args[0] for call in mock_stderr.write.call_args_list
            )

            self.assertEqual("", stdout_val)
            self.assertIn("Error generating completion: Failed", stderr_val)


if __name__ == "__main__":
    unittest.main()
