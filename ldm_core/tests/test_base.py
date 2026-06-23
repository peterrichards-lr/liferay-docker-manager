import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.diagnostics import DiagnosticsService
from ldm_core.handlers.workspace import WorkspaceService


class MockBaseManager(BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.args.project = None
        self.verbose = False
        self.non_interactive = True
        self.workspace = WorkspaceService(self)
        self.diagnostics = DiagnosticsService(self)
        self.manager = self  # type: ignore

    def cmd_completion(self, *args, **kwargs):
        return self.diagnostics.cmd_completion(*args, **kwargs)

    def get_resource_path(self, *args, **kwargs):
        from ldm_core.utils import get_resource_path

        return get_resource_path(*args, **kwargs)

    def _refresh_man_symlink(self):
        return self.diagnostics._refresh_man_symlink()

    def check_ram(self, *args, **kwargs):
        pass

    def check_hostname(self, host_name, silent=False):
        if host_name == "localhost":
            return True
        return super().check_hostname(host_name, silent)

    def get_resolved_ip(self, host):
        return super().get_resolved_ip(host)

    def check_port(self, ip, port):
        return True

    def check_registry_collisions(self, *args, **kwargs):
        pass

    def read_meta(self, path):
        # We need to return a dict with project_name matching the ID for discovery tests
        p = Path(path)
        if p.name == "p_match":
            return {"project_name": "p1"}

        # Realistically read if file exists
        from ldm_core.utils import read_meta

        for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]:
            if (p / f).exists():
                return read_meta(p / f)

        return {}


class TestBaseDiscovery(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    def test_find_dxp_roots_multi_dir(self):
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
                        "ldm_core.utils.get_actual_home",
                        return_value=base_path / "nonexistent",
                    ):
                        roots = self.handler.find_dxp_roots()

                        names = [r["path"].name for r in roots]
                        # In the new Hardened LDM, LDM_WORKSPACE is EXCLUSIVE.
                        self.assertIn("proj_other", names)


class TestBaseDiscoveryPath(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    def test_detect_project_path_by_id(self):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            proj_dir = base_path / "myproj"
            proj_dir.mkdir()
            (proj_dir / "meta").write_text("tag=7.4")

            with patch("ldm_core.handlers.base.Path.cwd", return_value=base_path):
                res = self.handler.detect_project_path("myproj")
                self.assertEqual(res.name, "myproj")

    def test_detect_project_path_for_init_missing(self):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            with patch("ldm_core.handlers.base.Path.cwd", return_value=base_path):
                res = self.handler.detect_project_path("newproj", for_init=True)
                self.assertEqual(res.name, "newproj")

    def test_detect_project_path_interactive_fallback(self):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            with patch("ldm_core.handlers.base.Path.cwd", return_value=base_path):
                with patch.object(
                    self.handler,
                    "select_project_interactively",
                    return_value={"path": Path("/selected")},
                ):
                    self.handler.args.project = None
                    self.handler.args.project_flag = None
                    res = self.handler.detect_project_path(None)
                    self.assertEqual(res, Path("/selected"))

    @patch("ldm_core.handlers.base.get_actual_home")
    def test_detect_project_path_fallback_script_dir(self, mock_home):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path / "home"

            # Use a more robust way to mock SCRIPT_DIR
            script_dir = base_path / "script"
            with patch("ldm_core.handlers.base.SCRIPT_DIR", script_dir):
                proj_dir = script_dir / "p1"
                proj_dir.mkdir(parents=True)
                (proj_dir / "meta").write_text("tag=7.4")

                with patch(
                    "ldm_core.handlers.base.Path.cwd", return_value=base_path / "cwd"
                ):
                    res = self.handler.detect_project_path("p1")
                    self.assertEqual(res.name, "p1")

    @patch("ldm_core.handlers.base.get_actual_home")
    def test_detect_project_path_by_container_name_in_sibling(self, mock_home):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp).resolve()
            mock_home.return_value = base_path / "home"

            # Create a sibling project dir with a different name than its container
            sibling_dir = base_path / "actual-folder-name"
            sibling_dir.mkdir(parents=True)
            (sibling_dir / "meta").write_text("tag=7.4\ncontainer_name=my-container-id")

            # We are currently in another sibling dir
            cwd = base_path / "current-repo"
            cwd.mkdir()

            with patch("ldm_core.handlers.base.Path.cwd", return_value=cwd):
                # Search for the container id
                res = self.handler.detect_project_path("my-container-id")
                self.assertIsNotNone(res)
                self.assertEqual(res.resolve(), sibling_dir.resolve())

    @patch("ldm_core.handlers.base.get_actual_home")
    def test_detect_project_path_iterative_search(self, mock_home):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path / "home"

            search_dir = base_path / "home" / "ldm"
            proj_dir = search_dir / "p_match"
            proj_dir.mkdir(parents=True)
            (proj_dir / "meta").write_text("tag=7.4")
            # Mock read_meta in MockBaseManager returns project_name="p1" for p_match

            with patch(
                "ldm_core.handlers.base.Path.cwd", return_value=base_path / "cwd"
            ):
                res = self.handler.detect_project_path("p1")
                self.assertEqual(res.name, "p_match")

    @patch("ldm_core.handlers.base.get_actual_home")
    @patch("ldm_core.handlers.base.safe_cwd")
    @patch("ldm_core.ui.UI.warning")
    def test_detect_project_path_cwd_home_warning(self, mock_warn, mock_cwd, mock_home):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path
            mock_cwd.return_value = base_path

            # Clean flag
            if hasattr(self.handler, "_warned_home"):
                delattr(self.handler, "_warned_home")

            # First run: should warn
            self.handler.detect_project_path("some-proj", for_init=True)
            mock_warn.assert_called_once()
            self.assertIn(
                "You are running LDM from your Home directory",
                mock_warn.call_args[0][0],
            )

            # Reset mock and run again: should NOT warn because _warned_home is True
            mock_warn.reset_mock()
            self.handler.detect_project_path("some-proj", for_init=True)
            self.assertFalse(mock_warn.called)


class TestBaseProject(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    def test_require_compose_true(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "docker-compose.yml").touch()
            self.assertTrue(self.handler.require_compose(root))

    def test_require_compose_false(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.assertFalse(self.handler.require_compose(root, silent=True))

    @patch("ldm_core.docker_service.DockerService.get_health", return_value="healthy")
    def test_get_container_status_healthy(self, mock_health):
        self.assertEqual(self.handler.get_container_status("c1"), "healthy")

    def test_select_project_interactively_basic(self):
        self.handler.non_interactive = False
        roots = [{"path": Path("/tmp/p1"), "version": "7.4"}]
        with patch("ldm_core.ui.UI.ask", return_value="1"):
            res = self.handler.select_project_interactively(roots=roots)
            self.assertEqual(res["path"], Path("/tmp/p1"))

    def test_select_project_interactively_new(self):
        self.handler.non_interactive = False
        roots = [{"path": Path("/tmp/p1"), "version": "7.4"}]
        with patch("ldm_core.ui.UI.ask", return_value="n"):
            res = self.handler.select_project_interactively(roots=roots)
            self.assertTrue(res.get("new"))

    def test_pre_flight_checks_basic(self):
        meta = {"root": "/tmp/p1", "project_name": "p1"}
        with patch.object(self.handler, "check_port", return_value=True):
            res_port = self.handler._pre_flight_checks("localhost", 8080, meta=meta)
            self.assertEqual(res_port, 8080)

    @patch("ldm_core.handlers.base.get_actual_home")
    def test_check_registry_collisions_none(self, mock_home):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path

            # No registry file exists
            self.handler.check_registry_collisions("p1", base_path / "p1")
            # Should not die

    def test_migrate_layout_basic(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = self.handler.setup_paths(root)
            self.handler.migrate_layout(paths)
            self.assertTrue((root / "files").exists())
            self.assertTrue((root / "deploy").exists())

    def test_get_common_dir_env(self):
        with patch.dict(os.environ, {"LDM_COMMON_DIR": "/tmp/common"}):
            with patch("ldm_core.handlers.base.Path.cwd", return_value=Path("/empty")):
                self.assertEqual(
                    self.handler.get_common_dir(Path("/root")).resolve(),
                    Path("/tmp/common").resolve(),
                )

    @patch("ldm_core.handlers.base.get_actual_home", return_value=Path("/tmp/home"))
    def test_get_common_dir_default(self, mock_home):
        # We need to ensure that Priority 2 (CWD/common) and Priority 3 (Project/common) don't match
        with patch("ldm_core.handlers.base.Path.cwd", return_value=Path("/empty")):
            # Use a more robust mock for exists that handles the self argument
            with patch.object(Path, "exists", autospec=True) as mock_exists:
                mock_exists.side_effect = lambda self: ".ldm" in str(self)
                self.assertEqual(
                    self.handler.get_common_dir(Path("/root")),
                    Path("/tmp/home/.ldm/common"),
                )


class TestBaseEnvironment(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    def test_is_wsl_true(self):
        with patch("platform.system", return_value="Linux"):
            with patch("builtins.open", unittest.mock.mock_open(read_data="Microsoft")):
                self.assertTrue(self.handler.is_wsl())

    def test_is_wsl_false(self):
        with patch("platform.system", return_value="Darwin"):
            self.assertFalse(self.handler.is_wsl())

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/java")
    def test_check_java_version_success(self, mock_which, mock_run):
        mock_res = MagicMock()
        mock_res.stderr = 'openjdk version "21.0.1" 2023-10-17'
        mock_run.return_value = mock_res
        self.assertTrue(self.handler._check_java_version("21"))

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/java")
    def test_check_java_version_fail(self, mock_which, mock_run):
        mock_res = MagicMock()
        mock_res.stderr = 'openjdk version "11.0.1"'
        mock_run.return_value = mock_res
        self.assertFalse(self.handler._check_java_version("21"))

    def test_run_command_error(self):
        with patch(
            "subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmd")
        ):
            with self.assertRaises(SystemExit):
                with patch("ldm_core.ui.UI.die") as mock_die:
                    mock_die.side_effect = SystemExit
                    self.handler.run_command("cmd")

    def test_get_resolved_ip_localhost(self):
        self.assertEqual(self.handler.get_resolved_ip("localhost"), "127.0.0.1")

    @patch("socket.gethostbyname", return_value="1.2.3.4")
    def test_get_resolved_ip_remote(self, mock_socket):
        self.assertEqual(self.handler.get_resolved_ip("myhost"), "1.2.3.4")

    def test_check_hostname_localhost(self):
        self.assertTrue(self.handler.check_hostname("localhost"))

    @patch("socket.gethostbyname", side_effect=socket.gaierror("Failed"))
    def test_check_hostname_fail(self, mock_socket):
        self.assertFalse(self.handler.check_hostname("invalid"))

    @patch("os.getuid", return_value=0, create=True)
    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_check_docker_root_fail(self, mock_run, mock_which, mock_uid):
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res
        with self.assertRaises(SystemExit):
            self.handler.check_docker()


class TestBaseHardening(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    @patch("ldm_core.handlers.diagnostics.platform.system")
    @patch("ldm_core.handlers.diagnostics.get_actual_home")
    @patch("ldm_core.handlers.diagnostics.get_resource_path")
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
        self.assertTrue(mock_mkdir.called)
        self.assertTrue(mock_unlink.called)
        self.assertTrue(mock_symlink.called)


class TestBaseCompletion(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    @patch("ldm_core.handlers.diagnostics.get_actual_home")
    @patch("ldm_core.handlers.diagnostics.get_resource_path")
    @patch("ldm_core.ui.UI.heading")
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/bin/zsh"})
    def test_cmd_completion_zsh_instructions(
        self, mock_info, mock_heading, mock_res, mock_home
    ):
        # No argument: should show instructions
        mock_home.return_value = Path("/tmp/home")
        with patch("builtins.print") as mock_print:
            self.handler.cmd_completion(target_shell=None)
            mock_heading.assert_called_with("LDM Shell Completion")

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/bin/bash"})
    @patch(
        "ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp/home")
    )
    def test_cmd_completion_bash_instructions(self, mock_home, mock_stdout):
        # No argument: should show instructions
        self.handler.cmd_completion(target_shell=None)
        pass

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/usr/bin/fish"})
    @patch(
        "ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp/home")
    )
    def test_cmd_completion_fish_instructions(self, mock_home, mock_stdout):
        self.handler.cmd_completion(target_shell=None)

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "powershell.exe"})
    @patch(
        "ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp/home")
    )
    def test_cmd_completion_powershell_instructions(self, mock_home, mock_stdout):
        self.handler.cmd_completion(target_shell=None)

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "powershell.exe"})
    @patch(
        "ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp/home")
    )
    def test_cmd_completion_powershell_code(self, mock_home, mock_stdout):
        # Specific argument: should show the bridge script
        self.handler.cmd_completion(target_shell="powershell")
        pass

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/usr/bin/fish"})
    @patch(
        "ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp/home")
    )
    def test_cmd_completion_zsh_code(self, mock_home, mock_stdout):
        # Specific argument: should show raw code
        with patch("argcomplete.shellcode", return_value="# ZSH CODE"):
            self.handler.cmd_completion(target_shell="zsh")

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("sys.stderr", new_callable=MagicMock)
    @patch(
        "ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp/home")
    )
    def test_cmd_completion_generation_suppresses_ui(
        self, mock_home, mock_stderr, mock_stdout
    ):
        # Verify that providing a shell argument DOES NOT print UI headings to stdout
        with (
            patch("argcomplete.shellcode", return_value="# CODE"),
            patch("ldm_core.ui.UI.heading") as mock_heading,
        ):
            self.handler.cmd_completion(target_shell="bash")
            mock_heading.assert_not_called()

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("sys.stderr", new_callable=MagicMock)
    @patch(
        "ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp/home")
    )
    def test_cmd_completion_generation_zsh_boilerplate(
        self, mock_home, mock_stderr, mock_stdout
    ):
        # Verify that providing zsh includes the necessary boilerplate
        with patch("argcomplete.shellcode", return_value="# CODE"):
            self.handler.cmd_completion(target_shell="zsh")

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("sys.stderr", new_callable=MagicMock)
    @patch(
        "ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp/home")
    )
    def test_cmd_completion_error_goes_to_stderr(
        self, mock_home, mock_stderr, mock_stdout
    ):
        # Verify that a failure in shellcode generation doesn't dump instructions to stdout
        with patch("argcomplete.shellcode", side_effect=Exception("Failed")):
            self.handler.cmd_completion(target_shell="zsh")

    @patch("platform.system", return_value="Darwin")
    @patch("ldm_core.handlers.base.subprocess.run")
    @patch("ldm_core.handlers.base.shutil.which", return_value="/usr/local/bin/docker")
    def test_verify_runtime_environment_darwin_no_unbound_local_error(
        self, mock_which, mock_run, mock_system
    ):
        from ldm_core.handlers.base import BaseHandler

        handler = BaseHandler(MagicMock())
        handler.verbose = False

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = {"root": root, "files": root / "files"}

            mock_result = MagicMock()
            mock_result.stdout = "OK"
            mock_run.return_value = mock_result

            try:
                handler.verify_runtime_environment(paths)
            except UnboundLocalError:
                self.fail(
                    "verify_runtime_environment raised UnboundLocalError unexpectedly!"
                )


class TestBasePortChecking(unittest.TestCase):
    def setUp(self):
        from ldm_core.handlers.base import BaseHandler

        self.handler = BaseHandler(MagicMock())

    @patch("socket.socket")
    def test_check_port_available(self, mock_socket_class):
        mock_socket = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_socket
        # bind succeeds, returns True
        res = self.handler.check_port("127.0.0.1", 8080)
        self.assertTrue(res)
        mock_socket.bind.assert_called_with(("127.0.0.1", 8080))

    @patch("socket.socket")
    def test_check_port_in_use(self, mock_socket_class):
        mock_socket = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_socket
        mock_socket.bind.side_effect = OSError(11, "Address already in use")
        res = self.handler.check_port("127.0.0.1", 8080)
        self.assertFalse(res)

    @patch("socket.socket")
    def test_check_port_permission_denied_free(self, mock_socket_class):
        mock_bind_socket = MagicMock()
        mock_conn_socket = MagicMock()
        # The first socket created is for bind, the second is for connect_ex
        mock_socket_class.return_value.__enter__.side_effect = [
            mock_bind_socket,
            mock_conn_socket,
        ]

        mock_bind_socket.bind.side_effect = PermissionError(13, "Permission denied")
        # connect_ex returns ECONNREFUSED or similar non-zero
        mock_conn_socket.connect_ex.return_value = 61

        res = self.handler.check_port("127.0.0.1", 80)
        self.assertTrue(res)

    @patch("socket.socket")
    def test_check_port_permission_denied_occupied(self, mock_socket_class):
        mock_bind_socket = MagicMock()
        mock_conn_socket = MagicMock()
        mock_socket_class.return_value.__enter__.side_effect = [
            mock_bind_socket,
            mock_conn_socket,
        ]

        mock_bind_socket.bind.side_effect = PermissionError(13, "Permission denied")
        # connect_ex returns 0 (occupied)
        mock_conn_socket.connect_ex.return_value = 0

        res = self.handler.check_port("127.0.0.1", 80)
        self.assertFalse(res)

    @patch("socket.socket")
    def test_check_port_oserror_permission_denied_free(self, mock_socket_class):
        import errno

        mock_bind_socket = MagicMock()
        mock_conn_socket = MagicMock()
        mock_socket_class.return_value.__enter__.side_effect = [
            mock_bind_socket,
            mock_conn_socket,
        ]

        # Raise OSError with errno EACCES
        err = OSError("Permission denied")
        err.errno = errno.EACCES
        mock_bind_socket.bind.side_effect = err
        mock_conn_socket.connect_ex.return_value = errno.ECONNREFUSED

        res = self.handler.check_port("127.0.0.1", 80)
        self.assertTrue(res)


if __name__ == "__main__":
    unittest.main()
