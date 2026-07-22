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
        # Mock poll health check to prevent real HTTP loops in base cmd_start tests
        self.service._poll_tunnel_health = MagicMock(return_value=(True, None))  # type: ignore[method-assign]
        # Mock resolve existing binary by default to keep unit tests isolated from host environment
        self.service._resolve_existing_binary = MagicMock(return_value=None)  # type: ignore[method-assign]

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
    @patch("ldm_core.handlers.share.download_file")
    @patch("subprocess.run")
    @patch("platform.system")
    @patch("platform.machine")
    def test_ensure_binary_download(
        self,
        mock_machine,
        mock_system,
        mock_run,
        mock_download,
        mock_home,
    ):
        mock_home.return_value = Path("/fake/home")
        mock_system.return_value = "Darwin"
        mock_machine.return_value = "arm64"

        # Mock installed version query (returns None -> trigger download, then returns "1.3.0" after download)
        mock_ver_res = MagicMock()
        mock_ver_res.stdout = "v1.3.0"
        mock_run.return_value = mock_ver_res
        mock_download.return_value = True

        # We need _get_installed_version to return None first, then "1.3.0"
        with patch.object(
            self.service,
            "_get_installed_version",
            side_effect=[None, "1.3.0"],
        ):
            # Mock file operations inside open()
            with patch("pathlib.Path.chmod") as mock_chmod:
                with patch("pathlib.Path.mkdir"):
                    with patch("pathlib.Path.stat") as mock_stat:
                        mock_stat.return_value.st_mode = 0o100644
                        bin_path = self.service._ensure_binary()

                    self.assertEqual(
                        bin_path,
                        Path("/fake/home/.ldm/bin/lfr-tunnel"),
                    )
                    # Should request the correct Go binary for darwin-arm64
                    mock_download.assert_called_once()
                    req_url = mock_download.call_args[0][0]
                    self.assertIn("lfr-tunnel-darwin-arm64", req_url)
                    self.assertEqual(
                        mock_download.call_args[0][1],
                        Path("/fake/home/.ldm/bin/lfr-tunnel"),
                    )
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
        self.service._verify_compatibility([str(mock_bin)], "0.9.0")

        mock_ui.die.assert_called_once_with(
            "Your Liferay Tunnel client is too old to connect to the server (Minimum required: v1.0.0). "
            "Please upgrade using 'lfr-tunnel -upgrade' or 'docker pull peterrichards/lfr-tunnel'."
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
        self.service._verify_compatibility([str(mock_bin)], "1.2.0")

        mock_ui.warning.assert_called_once_with(
            "An update is available for lfr-tunnel (Current: v1.2.0, Latest: v1.3.0)"
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
        self.service._verify_compatibility([str(mock_bin)], "1.3.0")

        mock_ui.warning.assert_not_called()
        mock_ui.die.assert_not_called()

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch.dict(os.environ, {"LFT_CLIENT_TOKEN": "env-token"})
    def test_get_auth_token_env(self, mock_home):
        # 1. From Env var
        token = self.service._get_auth_token()
        self.assertEqual(token, "env-token")

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch.dict(os.environ, {}, clear=True)
    def test_get_auth_token_file(self, mock_home):
        mock_home.return_value = Path("/fake/home")

        # 2. From file
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value="file-token\n"):
                token = self.service._get_auth_token()
                self.assertEqual(token, "file-token")

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch.dict(os.environ, {}, clear=True)
    def test_get_auth_token_config(self, mock_home):
        mock_home.return_value = Path("/fake/home")

        # 3. From global config
        with patch("pathlib.Path.exists", return_value=False):
            self.mock_manager.config.get_global_config = MagicMock(  # type: ignore[method-assign]
                return_value={"lfr_tunnel_token": "config-token"}
            )
            token = self.service._get_auth_token()
            self.assertEqual(token, "config-token")

    @patch("ldm_core.utils.get_keyring_token")
    @patch.dict(os.environ, {}, clear=True)
    def test_get_auth_token_keyring(self, mock_get_keyring):
        mock_get_keyring.return_value = "keyring-token"

        # 1. OS Keyring
        token = self.service._get_auth_token()
        self.assertEqual(token, "keyring-token")
        mock_get_keyring.assert_called_with(
            "liferay-docker-manager", "lfr_tunnel_token"
        )

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch("ldm_core.utils.get_keyring_token")
    @patch("ldm_core.utils.set_keyring_token")
    @patch("ldm_core.utils.save_global_config_safe")
    @patch("ldm_core.utils.safe_write_text")
    @patch("ldm_core.ui.UI.ask")
    @patch.dict(os.environ, {}, clear=True)
    def test_get_auth_token_interactive_prompt_keyring(
        self,
        mock_ask,
        mock_safe_write,
        mock_save_config,
        mock_set_keyring,
        mock_get_keyring,
        mock_home,
    ):
        mock_home.return_value = Path("/fake/home")
        mock_get_keyring.return_value = None
        mock_ask.return_value = "user-entered-token"
        self.mock_manager.non_interactive = False
        self.mock_manager.config.get_global_config = MagicMock(return_value={})  # type: ignore[method-assign]

        with patch("pathlib.Path.exists", return_value=False):
            token = self.service._get_auth_token()
            self.assertEqual(token, "user-entered-token")
            mock_set_keyring.assert_called_with(
                "liferay-docker-manager", "lfr_tunnel_token", "user-entered-token"
            )
            mock_save_config.assert_called_once()
            mock_safe_write.assert_called_with(
                Path("/fake/home/.lfr-tunnel/token"),
                "user-entered-token",
                mode=0o600,
            )

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
        self.mock_manager.runtime.cmd_run.assert_called_once()

        # Verify subprocess.run call to start container via docker compose
        self.assertTrue(mock_run.call_count >= 1)
        cmd_args = mock_run.call_args_list[-1][0][0]
        self.assertEqual(cmd_args, ["docker-compose", "up", "-d", "lfr-tunnel"])

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker-compose"])
    @patch("subprocess.run")
    def test_cmd_start_docker_custom_image(self, mock_run, mock_get_compose):
        self.service._get_auth_token = MagicMock(return_value="my-token")  # type: ignore[method-assign]
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.write_meta = MagicMock()  # type: ignore[method-assign]
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        self.service.cmd_start(
            project_id="myproj",
            subdomain="custom-sub",
            ports="9090",
            provider="lfr-tunnel-docker",
            image="custom/lfr-tunnel:latest",
        )

        # Verify metadata write contains custom image
        self.mock_manager.write_meta.assert_called_once()
        written_meta = self.mock_manager.write_meta.call_args[0][1]
        self.assertEqual(written_meta["share_image"], "custom/lfr-tunnel:latest")

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
    @patch("ldm_core.ui.UI.detail")
    def test_cmd_status_docker_running(self, mock_detail, mock_run, mock_get_compose):
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={"share_provider": "lfr-tunnel-docker"}
        )

        mock_res_ps = MagicMock()
        mock_res_ps.stdout = "Up 2 minutes\n"
        mock_res_json = MagicMock()
        mock_res_json.stdout = "not JSON"  # Fail json check so it falls back to logs
        mock_res_logs = MagicMock()
        mock_res_logs.stdout = "some logs"

        # We need mock_run side effects for three runs: docker compose ps, status-json, then docker compose logs
        mock_run.side_effect = [mock_res_ps, mock_res_json, mock_res_logs]

        self.service.cmd_status(project_id="myproj")

        # Verify docker compose ps args
        ps_args = mock_run.call_args_list[0][0][0]
        self.assertEqual(
            ps_args,
            ["docker-compose", "ps", "lfr-tunnel", "--format", "{{.Status}}"],
        )

        # Verify status-json args
        json_args = mock_run.call_args_list[1][0][0]
        self.assertEqual(
            json_args,
            [
                "docker",
                "exec",
                "myproj-lfr-tunnel",
                "./lfr-tunnel",
                "-status-json",
                "-subdomain",
                "myproj",
            ],
        )

        # Verify docker compose logs args
        logs_args = mock_run.call_args_list[2][0][0]
        self.assertEqual(
            logs_args, ["docker-compose", "logs", "--tail", "10", "lfr-tunnel"]
        )
        mock_detail.assert_called_with("lfr-tunnel container is running: Up 2 minutes")

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

    @patch("subprocess.run")
    @patch("requests.get")
    def test_poll_tunnel_health_native_success(self, mock_get, mock_run):
        # Un-mock _poll_tunnel_health for the check test
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )

        mock_run_res = MagicMock()
        mock_run_res.stdout = ""  # Return empty so it falls back to requests
        mock_run.return_value = mock_run_res

        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"status": "healthy"}
        mock_get.return_value = mock_res

        success, err = self.service._poll_tunnel_health("custom-sub", timeout=1)
        self.assertTrue(success)
        self.assertIsNone(err)

    @patch("subprocess.run")
    @patch("requests.get")
    def test_poll_tunnel_health_native_legacy_fallback(self, mock_get, mock_run):
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )

        mock_run_res = MagicMock()
        mock_run_res.stdout = ""  # Return empty so it falls back to requests
        mock_run.return_value = mock_run_res

        mock_res = MagicMock()
        mock_res.status_code = 404
        mock_get.return_value = mock_res

        success, err = self.service._poll_tunnel_health("custom-sub", timeout=1)
        self.assertTrue(success)
        self.assertIsNone(err)

    @patch("subprocess.run")
    def test_poll_tunnel_health_docker_success(self, mock_run):
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "healthy"
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        success, err = self.service._poll_tunnel_health(
            "custom-sub", container_name="myproj-lfr-tunnel", timeout=1
        )
        self.assertTrue(success)
        self.assertIsNone(err)

    def test_diagnose_tunnel_info_auth_failure(self):
        info = {"auth": {"valid": False, "error_message": "Invalid token"}}
        res = self.service._diagnose_tunnel_info(info, "sub")
        self.assertIn("Authentication Failed", res)
        self.assertIn("Invalid token", res)

    def test_diagnose_tunnel_info_conflict(self):
        info = {
            "auth": {"valid": True},
            "subdomain": {"conflict": True, "leased": False},
        }
        res = self.service._diagnose_tunnel_info(info, "sub")
        self.assertIn("Subdomain Conflict", res)

    def test_diagnose_tunnel_info_destination_offline(self):
        info = {
            "auth": {"valid": True},
            "subdomain": {"conflict": False, "leased": True},
            "destination": {"responsive": False, "port": 8080},
        }
        res = self.service._diagnose_tunnel_info(info, "sub")
        self.assertIn("Downstream Offline", res)

    @patch("subprocess.run")
    def test_cmd_inspector_success(self, mock_run):
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={"share_provider": "lfr-tunnel-docker"}
        )

        mock_inspect_res = MagicMock()
        mock_inspect_res.returncode = 0
        mock_inspect_res.stdout = "true"

        mock_rm_res = MagicMock()
        mock_stop_res = MagicMock()

        mock_run.side_effect = [
            mock_inspect_res,
            mock_rm_res,
            KeyboardInterrupt(),
            mock_stop_res,
        ]

        self.service.cmd_inspector("myproj")

        mock_run.assert_any_call(
            ["docker", "inspect", "-f", "{{.State.Running}}", "myproj-lfr-tunnel"],
            capture_output=True,
            text=True,
            check=False,
        )
        mock_run.assert_any_call(
            ["docker", "rm", "-f", "myproj-lfr-tunnel-inspector-proxy"],
            capture_output=True,
            check=False,
        )
        mock_run.assert_any_call(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                "myproj-lfr-tunnel-inspector-proxy",
                "--network",
                "liferay-net",
                "-p",
                "4040:4040",
                "alpine/socat",
                "tcp-listen:4040,fork,reuseaddr",
                "tcp-connect:myproj-lfr-tunnel:4040",
            ],
            check=True,
        )
        mock_run.assert_any_call(
            ["docker", "stop", "myproj-lfr-tunnel-inspector-proxy"],
            capture_output=True,
            check=False,
        )

    @patch("subprocess.run")
    def test_cmd_inspector_custom_port(self, mock_run):
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={"share_provider": "lfr-tunnel-docker"}
        )

        mock_inspect_res = MagicMock()
        mock_inspect_res.returncode = 0
        mock_inspect_res.stdout = "true"

        mock_rm_res = MagicMock()
        mock_stop_res = MagicMock()

        mock_run.side_effect = [
            mock_inspect_res,
            mock_rm_res,
            KeyboardInterrupt(),
            mock_stop_res,
        ]

        self.service.cmd_inspector("myproj", port=4045)

        mock_run.assert_any_call(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                "myproj-lfr-tunnel-inspector-proxy",
                "--network",
                "liferay-net",
                "-p",
                "4045:4040",
                "alpine/socat",
                "tcp-listen:4040,fork,reuseaddr",
                "tcp-connect:myproj-lfr-tunnel:4040",
            ],
            check=True,
        )

    def test_cmd_inspector_native_provider_error(self):
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={"share_provider": "lfr-tunnel"}
        )

        with self.assertRaises(SystemExit):
            self.service.cmd_inspector("myproj")

    @patch("subprocess.run")
    def test_cmd_inspector_not_running_error(self, mock_run):
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={"share_provider": "lfr-tunnel-docker"}
        )

        mock_inspect_res = MagicMock()
        mock_inspect_res.returncode = 1
        mock_inspect_res.stdout = "false"
        mock_run.return_value = mock_inspect_res

        with self.assertRaises(SystemExit):
            self.service.cmd_inspector("myproj")

    @patch("subprocess.run")
    def test_poll_tunnel_health_docker_logs_unauthorized(self, mock_run):
        # Restore actual _poll_tunnel_health method for this test
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )

        mock_wget = MagicMock()
        mock_wget.returncode = 1
        mock_wget.stderr = "Connection refused"

        mock_inspect = MagicMock()
        mock_inspect.returncode = 0
        mock_inspect.stdout = "false"

        mock_logs = MagicMock()
        mock_logs.returncode = 0
        mock_logs.stdout = (
            "[Error] Failed to register: gateway error (401): unauthorized"
        )
        mock_logs.stderr = ""

        mock_run.side_effect = [mock_wget, mock_inspect, mock_logs]

        success, err = self.service._poll_tunnel_health(
            "custom-sub", container_name="myproj-lfr-tunnel", timeout=0.1
        )
        self.assertFalse(success)
        self.assertIn("Authentication Failed", err)

    @patch("subprocess.run")
    def test_poll_tunnel_health_docker_logs_conflict(self, mock_run):
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )

        mock_wget = MagicMock()
        mock_wget.returncode = 1
        mock_wget.stderr = "Connection refused"

        mock_inspect = MagicMock()
        mock_inspect.returncode = 0
        mock_inspect.stdout = "false"

        mock_logs = MagicMock()
        mock_logs.returncode = 0
        mock_logs.stdout = "[Error] Failed to register: subdomain conflict"
        mock_logs.stderr = ""

        mock_run.side_effect = [mock_wget, mock_inspect, mock_logs]

        success, err = self.service._poll_tunnel_health(
            "custom-sub", container_name="myproj-lfr-tunnel", timeout=0.1
        )
        self.assertFalse(success)
        self.assertIn("Subdomain Conflict", err)

    @patch("subprocess.run")
    def test_poll_tunnel_health_docker_logs_running_but_unresponsive(self, mock_run):
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )

        mock_wget_healthz = MagicMock()
        mock_wget_healthz.returncode = 1
        mock_wget_healthz.stderr = "Connection refused"

        mock_inspect = MagicMock()
        mock_inspect.returncode = 0
        mock_inspect.stdout = "true"

        mock_wget_info = MagicMock()
        mock_wget_info.returncode = 1
        mock_wget_info.stderr = "Connection refused"

        mock_logs = MagicMock()
        mock_logs.returncode = 0
        mock_logs.stdout = "dial tcp: lookup tunnel.lfr-demo.online: no such host"
        mock_logs.stderr = ""

        mock_run.side_effect = [
            mock_wget_healthz,
            mock_inspect,
            mock_wget_info,
            mock_logs,
        ]

        success, err = self.service._poll_tunnel_health(
            "custom-sub", container_name="myproj-lfr-tunnel", timeout=0.1
        )
        self.assertFalse(success)
        self.assertIn(
            "Tunnel connection timeout. Container is running but not responsive", err
        )
        self.assertIn("dial tcp: lookup tunnel.lfr-demo.online: no such host", err)

    @patch("subprocess.run")
    def test_poll_tunnel_health_uses_busybox_args(self, mock_run):
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "healthy"
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        success, err = self.service._poll_tunnel_health(
            "custom-sub", container_name="myproj-lfr-tunnel", timeout=0.1
        )
        self.assertTrue(success)
        mock_run.assert_called_with(
            [
                "docker",
                "exec",
                "myproj-lfr-tunnel",
                "wget",
                "-qO-",
                "http://127.0.0.1:4040/api/healthz",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("subprocess.run")
    def test_poll_tunnel_health_native_status_json_success(self, mock_run):
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = (
            '{"running": true, "status": "healthy", "connection_state": "connected"}'
        )
        mock_run.return_value = mock_res

        success, err = self.service._poll_tunnel_health("custom-sub", timeout=1)
        self.assertTrue(success)
        self.assertIsNone(err)
        mock_run.assert_called_with(
            [
                "/fake/bin/lfr-tunnel",
                "-status-json",
                "-subdomain",
                "custom-sub",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("subprocess.run")
    def test_poll_tunnel_health_docker_status_json_success(self, mock_run):
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = (
            '{"running": true, "status": "healthy", "connection_state": "connected"}'
        )
        mock_run.return_value = mock_res

        success, err = self.service._poll_tunnel_health(
            "custom-sub", container_name="myproj-lfr-tunnel", timeout=1
        )
        self.assertTrue(success)
        self.assertIsNone(err)
        mock_run.assert_called_with(
            [
                "docker",
                "exec",
                "myproj-lfr-tunnel",
                "./lfr-tunnel",
                "-status-json",
                "-subdomain",
                "custom-sub",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("subprocess.run")
    @patch("ldm_core.ui.UI.heading")
    @patch("ldm_core.ui.UI.raw")
    def test_cmd_status_native_status_json_success(
        self, mock_raw, mock_heading, mock_run
    ):
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={
                "share_provider": "lfr-tunnel",
                "share_subdomain": "custom-sub",
            }
        )

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"running": true, "status": "healthy", "connection_state": "connected", "public_urls": ["https://custom-sub.lfr-demo.online"], "inspector_port": 4041}'
        mock_run.return_value = mock_res

        self.service.cmd_status(project_id="myproj")

        mock_run.assert_called_with(
            ["/fake/bin/lfr-tunnel", "-status-json", "-subdomain", "custom-sub"],
            capture_output=True,
            text=True,
            check=False,
        )
        mock_heading.assert_called_once_with("Liferay Tunnel Status")
        mock_raw.assert_any_call("  ● \x1b[0;37mSubdomain: \x1b[0;36mcustom-sub\x1b[0m")
        mock_raw.assert_any_call("  ● \x1b[0;37mStatus: \x1b[0;32mhealthy\x1b[0m")

    @patch("ldm_core.utils.get_compose_cmd", return_value=["docker-compose"])
    @patch("subprocess.run")
    @patch("ldm_core.ui.UI.heading")
    @patch("ldm_core.ui.UI.raw")
    def test_cmd_status_docker_status_json_success(
        self, mock_raw, mock_heading, mock_run, mock_get_compose
    ):
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={
                "share_provider": "lfr-tunnel-docker",
                "share_subdomain": "custom-sub",
                "tunnel_container_name": "myproj-lfr-tunnel",
            }
        )

        mock_res_ps = MagicMock()
        mock_res_ps.stdout = "Up 2 minutes\n"
        mock_res_json = MagicMock()
        mock_res_json.stdout = '{"running": true, "status": "healthy", "connection_state": "connected", "public_urls": ["https://custom-sub.lfr-demo.online"]}'
        mock_run.side_effect = [mock_res_ps, mock_res_json]

        self.service.cmd_status(project_id="myproj")

        mock_heading.assert_called_once_with("Liferay Tunnel Container Status")
        mock_raw.assert_any_call(
            "  ● \x1b[0;37mContainer Name: \x1b[0;36mmyproj-lfr-tunnel\x1b[0m"
        )
        mock_raw.assert_any_call("  ● \x1b[0;37mStatus: \x1b[0;32mhealthy\x1b[0m")

    @patch("subprocess.run")
    def test_cmd_stop_with_subdomain(self, mock_run):
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={
                "share_provider": "lfr-tunnel",
                "share_subdomain": "custom-sub",
            }
        )

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        self.service.cmd_stop(project_id="myproj")

        mock_run.assert_called_once_with(
            ["/fake/bin/lfr-tunnel", "-stop", "-subdomain", "custom-sub"],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("subprocess.run")
    def test_cmd_start_with_custom_host_name(self, mock_run):
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )
        self.service._get_installed_version = MagicMock(return_value="v1.2.3")  # type: ignore[method-assign]
        self.service._verify_compatibility = MagicMock()  # type: ignore[method-assign]
        self.service._get_auth_token = MagicMock(return_value="my-token")  # type: ignore[method-assign]
        self.service._poll_tunnel_health = MagicMock(return_value=(True, None))  # type: ignore[method-assign]

        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/myproj")
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={
                "share_provider": "lfr-tunnel",
                "host_name": "custom.domain.local",
            }
        )

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        self.service.cmd_start(project_id="myproj")

        run_args = mock_run.call_args[0][0]
        self.assertIn("-target-host", run_args)
        self.assertIn("custom.domain.local", run_args)

    @patch("subprocess.run")
    def test_resolve_public_tunnel_url_status_json(self, mock_run):
        self.service._get_binary_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )

        with patch("os.path.exists", return_value=True):
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = (
                '{"public_urls": ["https://my-subdomain.custom.server.com"]}'
            )
            mock_run.return_value = mock_res

            url = self.service.resolve_public_tunnel_url(
                "my-subdomain", project_id="myproj"
            )
            self.assertEqual(url, "https://my-subdomain.custom.server.com")
            mock_run.assert_called_with(
                ["/fake/bin/lfr-tunnel", "-status-json", "-subdomain", "my-subdomain"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )

    @patch.dict(os.environ, {"LDM_LFR_TUNNEL_BIN": "/env/bin/lfr-tunnel"})
    def test_resolve_existing_binary_env_var(self):
        with patch.object(self.service, "_get_installed_version", return_value="1.0.0"):
            res = ShareService._resolve_existing_binary(self.service)
            self.assertEqual(res, Path("/env/bin/lfr-tunnel"))

    def test_resolve_existing_binary_config(self):
        self.mock_manager.config.get_global_config = MagicMock(  # type: ignore[method-assign]
            return_value={"lfr_tunnel_bin": "/config/bin/lfr-tunnel"}
        )
        with patch.object(self.service, "_get_installed_version", return_value="1.0.0"):
            res = ShareService._resolve_existing_binary(self.service)
            self.assertEqual(res, Path("/config/bin/lfr-tunnel"))

    @patch("shutil.which")
    def test_resolve_existing_binary_path(self, mock_which):
        mock_which.return_value = "/sys/path/bin/lfr-tunnel"
        with patch.object(self.service, "_get_installed_version", return_value="1.0.0"):
            res = ShareService._resolve_existing_binary(self.service)
            self.assertEqual(res, Path("/sys/path/bin/lfr-tunnel"))

    @patch("shutil.which")
    @patch("ldm_core.handlers.share.get_actual_home")
    def test_resolve_existing_binary_fallback(self, mock_home, mock_which):
        mock_which.return_value = None
        mock_home.return_value = Path("/fake/home")
        with patch("pathlib.Path.exists", return_value=True):
            with patch.object(
                self.service, "_get_installed_version", return_value="1.0.0"
            ):
                res = ShareService._resolve_existing_binary(self.service)
                self.assertEqual(res, Path("/fake/home/.ldm/bin/lfr-tunnel"))

    @patch("ldm_core.handlers.share.UI")
    def test_ensure_binary_non_interactive_no_flag_fails(self, mock_ui):
        mock_ui.die.side_effect = SystemExit("Terminated")
        self.service._get_binary_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )
        self.service._get_installed_version = MagicMock(return_value=None)  # type: ignore[method-assign]
        self.mock_manager.non_interactive = True
        self.mock_manager.args.auto_install_lfr_tunnel = False

        with self.assertRaises(SystemExit):
            self.service._ensure_binary()
        mock_ui.die.assert_called_once()

    @patch("ldm_core.handlers.share.run_command")
    @patch("ldm_core.handlers.share.UI")
    def test_ensure_binary_custom_install_cmd(self, mock_ui, mock_run_cmd):
        self.service._get_binary_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )
        self.service._get_installed_version = MagicMock(side_effect=[None, "1.0.0"])  # type: ignore[method-assign]
        self.mock_manager.non_interactive = True
        self.mock_manager.args.auto_install_lfr_tunnel = True
        self.mock_manager.config.get_global_config = MagicMock(  # type: ignore[method-assign]
            return_value={"lfr_tunnel_install_cmd": "install.sh"}
        )

        self.service._resolve_existing_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/resolved/lfr-tunnel")
        )

        mock_run_cmd.return_value = "success"

        res = self.service._ensure_binary()
        mock_run_cmd.assert_called_once_with(["install.sh"], check=False)
        self.assertEqual(res, Path("/resolved/lfr-tunnel"))

    @patch("ldm_core.handlers.share.run_command")
    @patch("ldm_core.handlers.share.UI")
    def test_ensure_binary_custom_install_cmd_failure(self, mock_ui, mock_run_cmd):
        self.service._get_binary_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )
        self.service._get_installed_version = MagicMock(return_value=None)  # type: ignore[method-assign]
        self.mock_manager.non_interactive = True
        self.mock_manager.args.auto_install_lfr_tunnel = True
        self.mock_manager.config.get_global_config = MagicMock(  # type: ignore[method-assign]
            return_value={"lfr_tunnel_install_cmd": "install.sh"}
        )

        mock_ui.die.side_effect = SystemExit("Terminated")
        mock_run_cmd.return_value = None

        with self.assertRaises(SystemExit):
            self.service._ensure_binary()
        mock_ui.die.assert_called_once_with("Custom installation command failed.")

    @patch("ldm_core.handlers.share.run_command")
    @patch("ldm_core.handlers.share.UI")
    def test_ensure_binary_custom_cmd_safety(self, mock_ui, mock_run_cmd):
        self.service._get_binary_path = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )
        self.service._get_installed_version = MagicMock(side_effect=[None, "1.0.0"])  # type: ignore[method-assign]
        self.mock_manager.non_interactive = True
        self.mock_manager.args.auto_install_lfr_tunnel = True
        self.mock_manager.config.get_global_config = MagicMock(  # type: ignore[method-assign]
            return_value={"lfr_tunnel_install_cmd": "echo pwned; id"}
        )
        self.service._resolve_existing_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/resolved/lfr-tunnel")
        )

        mock_run_cmd.return_value = "success"

        res = self.service._ensure_binary()
        mock_run_cmd.assert_called_once_with(["echo", "pwned;", "id"], check=False)
        self.assertEqual(res, Path("/resolved/lfr-tunnel"))

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("subprocess.run")
    @patch("requests.get")
    def test_poll_tunnel_health_native_log_extraction_reservation_failure(
        self, mock_get, mock_run, mock_read, mock_exists, mock_home
    ):
        # Restore actual _poll_tunnel_health method for this test
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )

        mock_home.return_value = Path("/fake/home")
        mock_exists.return_value = True

        # mock requests to fail (crashed daemon)
        mock_get.side_effect = Exception("Connection refused")

        # mock subprocess.run for status-json to fail
        status_res = MagicMock()
        status_res.stdout = ""
        mock_run.return_value = status_res

        # mock log file reading
        mock_read.return_value = (
            "[Error] Failed to register: gateway error (403): Custom subdomains must be reserved in the portal prior to connecting.\n"
            "[Client] Subdomain reservation or limit issue detected.\n"
            "[Client] Please visit the User Portal to resolve it:\n"
            "         👉 https://portal.lfr-demo.se (Cmd/Ctrl+Click to open)"
        )

        success, err = self.service._poll_tunnel_health("custom-sub", timeout=0.1)
        self.assertFalse(success)
        self.assertIn("Gateway Registration Failed:", err)
        self.assertIn("Custom subdomains must be reserved", err)
        self.assertIn("👉 https://portal.lfr-demo.se", err)

    @patch("subprocess.run")
    def test_poll_tunnel_health_docker_logs_multiline_reservation_failure(
        self, mock_run
    ):
        # Restore actual _poll_tunnel_health method for this test
        self.service._poll_tunnel_health = ShareService._poll_tunnel_health.__get__(  # type: ignore[method-assign]
            self.service, ShareService
        )

        mock_wget = MagicMock()
        mock_wget.returncode = 1
        mock_wget.stderr = "Connection refused"

        mock_inspect = MagicMock()
        mock_inspect.returncode = 0
        mock_inspect.stdout = "false"

        mock_logs = MagicMock()
        mock_logs.returncode = 0
        mock_logs.stdout = (
            "[Error] Failed to register: gateway error (403): Custom subdomains must be reserved in the portal prior to connecting.\n"
            "[Client] Subdomain reservation or limit issue detected.\n"
            "[Client] Please visit the User Portal to resolve it:\n"
            "         👉 https://portal.lfr-demo.se (Cmd/Ctrl+Click to open)"
        )
        mock_logs.stderr = ""

        mock_run.side_effect = [mock_wget, mock_inspect, mock_logs]

        success, err = self.service._poll_tunnel_health(
            "custom-sub", container_name="myproj-lfr-tunnel", timeout=0.1
        )
        self.assertFalse(success)
        self.assertIn("Gateway Registration Failed:", err)
        self.assertIn("Custom subdomains must be reserved", err)
        self.assertIn("👉 https://portal.lfr-demo.se", err)

    @patch("ldm_core.handlers.share.get_actual_home")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("subprocess.run")
    @patch("ldm_core.handlers.share.UI")
    def test_cmd_start_native_error_handling_prints_logs(
        self, mock_ui, mock_run, mock_read, mock_exists, mock_home
    ):
        mock_home.return_value = Path("/fake/home")
        mock_exists.return_value = True
        mock_read.return_value = (
            "Client Error: Subdomain limit reached.\n👉 https://portal.lfr-demo.se"
        )

        # Mock dependencies in cmd_start
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/bin/lfr-tunnel")
        )
        self.service._get_installed_version = MagicMock(return_value="1.0.0")  # type: ignore[method-assign]
        self.service._verify_compatibility = MagicMock()  # type: ignore[method-assign]
        self.service._get_auth_token = MagicMock(return_value="mock-token")  # type: ignore[method-assign]

        # Process starts but exits with 1 (immediate failure)
        mock_run_res = MagicMock()
        mock_run_res.returncode = 1
        mock_run_res.stderr = "Subprocess immediate crash"
        mock_run.return_value = mock_run_res

        self.service.cmd_start(project_id="demo", subdomain="custom-sub")

        # Verify that UI.error was called for exit failure and logs printed
        mock_ui.error.assert_any_call("Failed to start tunnel (Exit 1)")
        mock_ui.error.assert_any_call("Tunnel Log Output:")
