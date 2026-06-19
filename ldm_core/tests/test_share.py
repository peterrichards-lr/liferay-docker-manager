import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.share import ShareService


class MockConfig:
    def get_global_config(self):
        return {}


class MockManager:
    def __init__(self):
        self.non_interactive = True
        self.verbose = False
        self.config = MockConfig()
        self.args = MagicMock()
        self.runtime = MagicMock()

    def detect_project_path(self, project_id=None):
        return None

    def read_meta(self, root):
        return {}

    def write_meta(self, root, meta):
        pass

    def setup_paths(self, root):
        return {"root": Path(root)}


class TestShareService(unittest.TestCase):
    def setUp(self):
        self.mock_manager = MockManager()
        self.service = ShareService(self.mock_manager)

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch("platform.system")
    def test_get_binary_path(self, mock_system, mock_home):
        mock_home.return_value = Path("/fake/home")

        # Unix
        mock_system.return_value = "Darwin"
        self.assertEqual(
            self.service._get_binary_path(),
            Path("/fake/home/.ldm/bin/lfr-tunnel"),
        )

        # Windows
        mock_system.return_value = "Windows"
        self.assertEqual(
            self.service._get_binary_path(),
            Path("/fake/home/.ldm/bin/lfr-tunnel.exe"),
        )

    @patch("subprocess.run")
    def test_get_installed_version(self, mock_run):
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        # Successful version query
        mock_res = MagicMock()
        mock_res.stdout = "lfr-tunnel version v1.2.3"
        mock_res.stderr = ""
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        ver = self.service._get_installed_version(mock_bin)
        self.assertEqual(ver, "1.2.3")

        # Version query with leading version prefix or raw number
        mock_res.stdout = "0.5.4"
        ver = self.service._get_installed_version(mock_bin)
        self.assertEqual(ver, "0.5.4")

        # Missing binary
        mock_bin.exists.return_value = False
        self.assertIsNone(self.service._get_installed_version(mock_bin))

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch("urllib.request.urlopen")
    @patch("ssl._create_unverified_context")
    @patch("subprocess.run")
    @patch("platform.system")
    @patch("platform.machine")
    def test_ensure_binary_download(
        self,
        mock_machine,
        mock_system,
        mock_run,
        mock_ssl,
        mock_urlopen,
        mock_home,
    ):
        mock_home.return_value = Path("/fake/home")
        mock_system.return_value = "Darwin"
        mock_machine.return_value = "arm64"

        # Mock installed version query (returns None -> trigger download, then returns "1.3.0" after download)
        mock_ver_res = MagicMock()
        mock_ver_res.stdout = "v1.3.0"
        mock_run.return_value = mock_ver_res

        # We need _get_installed_version to return None first, then "1.3.0"
        with patch.object(
            self.service,
            "_get_installed_version",
            side_effect=[None, "1.3.0"],
        ) as mock_get_ver:
            # Mock file operations inside open()
            with patch("builtins.open", unittest.mock.mock_open()) as mock_file:
                with patch("pathlib.Path.chmod") as mock_chmod:
                    with patch("pathlib.Path.mkdir") as mock_mkdir:
                        with patch("pathlib.Path.stat") as mock_stat:
                            mock_stat.return_value.st_mode = 0o100644
                            bin_path = self.service._ensure_binary()

                        self.assertEqual(
                            bin_path,
                            Path("/fake/home/.ldm/bin/lfr-tunnel"),
                        )
                        # Should request the correct Go binary for darwin-arm64
                        mock_urlopen.assert_called_once()
                        req_url = mock_urlopen.call_args[0][0]
                        self.assertIn("lfr-tunnel-darwin-arm64", req_url)
                        mock_chmod.assert_called_once()

    @patch("subprocess.run")
    @patch("ldm_core.handlers.share.UI")
    def test_verify_compatibility_hard_blocker(self, mock_ui, mock_run):
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"latest_version": "v1.3.0", "min_version": "v1.0.0"}'
        mock_run.return_value = mock_res

        # Local version is older than min_version -> Hard Blocker
        self.service._verify_compatibility(mock_bin, "0.9.0")

        mock_ui.die.assert_called_once_with(
            "Your Liferay Tunnel client is too old to connect to the server. Minimum required version is v1.0.0."
        )
        mock_ui.warning.assert_not_called()

    @patch("subprocess.run")
    @patch("ldm_core.handlers.share.UI")
    def test_verify_compatibility_soft_warning(self, mock_ui, mock_run):
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"latest_version": "v1.3.0", "min_version": "v1.0.0"}'
        mock_run.return_value = mock_res

        # Local version >= min_version but < latest_version -> Soft Warning
        self.service._verify_compatibility(mock_bin, "1.2.0")

        mock_ui.warning.assert_called_once_with(
            "A new version of Liferay Tunnel (v1.3.0) is available. You are running v1.2.0."
        )
        mock_ui.die.assert_not_called()

    @patch("subprocess.run")
    @patch("ldm_core.handlers.share.UI")
    def test_verify_compatibility_ok(self, mock_ui, mock_run):
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"latest_version": "v1.3.0", "min_version": "v1.0.0"}'
        mock_run.return_value = mock_res

        # Local version >= latest_version -> No Warning
        self.service._verify_compatibility(mock_bin, "1.3.0")

        mock_ui.warning.assert_not_called()
        mock_ui.die.assert_not_called()

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch.dict(os.environ, {"LFT_CLIENT_TOKEN": "env-token"})
    def test_get_auth_token_env(self, mock_home):
        # 1. From Env var
        token = self.service._get_auth_token()
        self.assertEqual(token, "env-token")

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch.dict(os.environ, {})
    def test_get_auth_token_file(self, mock_home):
        mock_home.return_value = Path("/fake/home")

        # 2. From file
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value="file-token\n"):
                token = self.service._get_auth_token()
                self.assertEqual(token, "file-token")

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch.dict(os.environ, {})
    def test_get_auth_token_config(self, mock_home):
        mock_home.return_value = Path("/fake/home")

        # 3. From global config
        with patch("pathlib.Path.exists", return_value=False):
            self.mock_manager.config.get_global_config = MagicMock(  # type: ignore[method-assign]
                return_value={"lfr_tunnel_token": "config-token"}
            )
            token = self.service._get_auth_token()
            self.assertEqual(token, "config-token")

    @patch("subprocess.run")
    def test_cmd_start(self, mock_run):
        # Mock ensures we have token and binary
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )
        self.service._get_auth_token = MagicMock(return_value="my-token")  # type: ignore[method-assign]

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "Tunnel started successfully."
        mock_run.return_value = mock_res

        self.service.cmd_start(subdomain="custom-sub", ports="9090")

        # Verify Popen/run invocation arguments
        mock_run.assert_called_once()
        cmd_args = mock_run.call_args[0][0]
        self.assertEqual(cmd_args[0], "/fake/bin/lfr-tunnel")
        self.assertIn("-background", cmd_args)
        self.assertIn("custom-sub", cmd_args)
        self.assertIn("9090", cmd_args)

        # Check token passed in environment
        env = mock_run.call_args[1]["env"]
        self.assertEqual(env["LFT_CLIENT_TOKEN"], "my-token")
        self.assertEqual(env["LFR_TUNNEL_TOKEN"], "my-token")

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker-compose"])
    @patch("subprocess.run")
    def test_cmd_start_docker(self, mock_run, mock_get_compose):
        self.service._get_auth_token = MagicMock(return_value="my-token")  # type: ignore[method-assign]
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        self.service.cmd_start(
            project_id="myproj",
            subdomain="custom-sub",
            ports="9090",
            provider="lfr-tunnel-docker",
        )

        # Verify sync_stack call
        self.mock_manager.runtime.sync_stack.assert_called_once()

        # Verify subprocess.run call to start container via docker compose
        mock_run.assert_called_once()
        cmd_args = mock_run.call_args[0][0]
        self.assertEqual(cmd_args, ["docker-compose", "up", "-d", "lfr-tunnel"])

    @patch("subprocess.run")
    def test_cmd_status(self, mock_run):
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "Tunnel is active."
        mock_run.return_value = mock_res

        self.service.cmd_status()
        mock_run.assert_called_once_with(
            ["/fake/bin/lfr-tunnel", "-status"],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("subprocess.run")
    def test_cmd_stop(self, mock_run):
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "Tunnel stopped."
        mock_run.return_value = mock_res

        self.service.cmd_stop()
        mock_run.assert_called_once_with(
            ["/fake/bin/lfr-tunnel", "-stop"],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker-compose"])
    @patch("subprocess.run")
    @patch("ldm_core.ui.UI.info")
    def test_cmd_status_docker_running(self, mock_info, mock_run, mock_get_compose):
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={"share_provider": "lfr-tunnel-docker"}
        )

        mock_res_ps = MagicMock()
        mock_res_ps.stdout = "Up 2 minutes\n"
        mock_res_logs = MagicMock()
        mock_res_logs.stdout = "some logs"

        # We need mock_run side effects for two runs: docker compose ps, then docker compose logs
        mock_run.side_effect = [mock_res_ps, mock_res_logs]

        self.service.cmd_status(project_id="myproj")

        # Verify docker compose ps args
        ps_args = mock_run.call_args_list[0][0][0]
        self.assertEqual(
            ps_args,
            ["docker-compose", "ps", "lfr-tunnel", "--format", "{{.Status}}"],
        )

        # Verify docker compose logs args
        logs_args = mock_run.call_args_list[1][0][0]
        self.assertEqual(
            logs_args, ["docker-compose", "logs", "--tail", "10", "lfr-tunnel"]
        )

        mock_info.assert_called_with("lfr-tunnel container is running: Up 2 minutes")

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker-compose"])
    @patch("subprocess.run")
    @patch("ldm_core.ui.UI.success")
    def test_cmd_stop_docker(self, mock_success, mock_run, mock_get_compose):
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={"share_provider": "lfr-tunnel-docker"}
        )

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        self.service.cmd_stop(project_id="myproj")

        mock_run.assert_called_once_with(
            ["docker-compose", "rm", "-fs", "lfr-tunnel"],
            cwd="/fake/myproj",
            capture_output=True,
            text=True,
            check=False,
        )
        mock_success.assert_called_with("Tunnel container stopped and removed.")
