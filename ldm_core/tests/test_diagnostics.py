import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.diagnostics import DiagnosticsHandler
from ldm_core.constants import VERSION


class MockDiagManager(DiagnosticsHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

    def find_dxp_roots(self, *args, **kwargs):
        # Default to empty for base doctor check
        return getattr(self, "_mock_roots", [])

    def read_meta(self, *args, **kwargs):
        return {"host_name": "test.local", "ssl": "true", "ssl_port": 443}

    def setup_paths(self, root_path):
        return {"root": Path(root_path)}

    def get_common_dir(self, *args, **kwargs):
        # We need this to exist to avoid 'Missing' warning (which triggers exit 1)
        return Path("/tmp")

    def require_compose(self, *args, **kwargs):
        return True

    def check_docker(self, *args, **kwargs):
        return True

    def _is_cloud_authenticated(self, *args, **kwargs):
        return True, "test-user"

    def _check_container_health_logs(self, *args, **kwargs):
        return None, True

    def validate_properties_file(self, *args, **kwargs):
        # Fallback to real implementation for property validation tests
        if hasattr(self, "_use_real_validate"):
            return super().validate_properties_file(*args, **kwargs)
        return "OK", True, []

    def check_license_health(self, *args, **kwargs):
        return "OK", True, []

    def check_dns_health(self, *args, **kwargs):
        return "OK", True, []

    def validate_project_dns(self, *args, **kwargs):
        return True, []

    def check_mkcert(self, *args, **kwargs):
        return "Installed (Root CA Trusted)", True, "/home/user/.local/share/mkcert"

    def _check_openssl(self, *args, **kwargs):
        return "OpenSSL 3.0.0", True

    def _check_lcp_cli(self, *args, **kwargs):
        return "Logged In", True

    def _check_docker_creds(self, *args, **kwargs):
        return "OK (osxkeychain)", True

    def _check_docker_resources(self, *args, **kwargs):
        return [("Docker CPUs", "4 Cores", True), ("Docker Memory", "8.0 GB", True)]


class TestDiagnostics(unittest.TestCase):
    def setUp(self):
        self.manager = MockDiagManager()
        self.test_file = Path("/tmp/test.properties")

    def tearDown(self):
        if self.test_file.exists():
            self.test_file.unlink()

    @patch("ldm_core.handlers.diagnostics.run_command")
    def test_cmd_status_running(self, mock_run):
        # Set up mock projects
        self.manager._mock_roots = [
            {"path": Path("/tmp/proj1"), "version": "2025.q1.0"}
        ]

        # Mock responses for:
        # 1. proxy existence check (liferay-proxy-global)
        # 2. proxy inspect
        # 3. search existence check (liferay-search-global)
        # 4. search inspect
        # 5. socket bridge existence check (docker-socket-proxy)
        # 6. project container filter check
        mock_run.side_effect = [
            "id-proxy",
            "running traefik:latest",
            "id-search",
            "running search:latest",
            None,  # No bridge
            "proj_container_id",
            "running",  # project container status
        ]

        with patch("builtins.print") as mock_print:
            try:
                self.manager.cmd_status()
            except SystemExit:
                pass

            # Verify it printed something about global infrastructure
            self.assertTrue(
                any("Search (ES)" in str(call) for call in mock_print.call_args_list)
            )

    def test_validate_lcp_json_valid(self):
        lcp_file = Path("/tmp/LCP.json")
        lcp_file.write_text('{"id": "test-ext", "ports": [{"targetPort": 8080}]}')
        try:
            status, ok, errors = self.manager.validate_lcp_json(lcp_file)
            self.assertTrue(ok)
            self.assertEqual(status, "Valid Structure")
        finally:
            if lcp_file.exists():
                lcp_file.unlink()

    @patch("subprocess.run")
    def test_check_liferay_health_logs(self, mock_run):
        # 1. Success case
        mock_run.return_value = MagicMock(
            stdout="Liferay(TM) Portal 7.4 started in 120s", stderr="", returncode=0
        )
        status, ok = self.manager._check_liferay_health_logs("test-container")
        self.assertEqual(status, "Ready")
        self.assertTrue(ok)

        # 2. Critical Error case (ES Version)
        mock_run.return_value = MagicMock(
            stdout="ERROR: Elasticsearch node does not meet the minimum version requirement of 8.19",
            stderr="",
            returncode=0,
        )
        status, ok = self.manager._check_liferay_health_logs("test-container")
        self.assertTrue("Critical" in status)
        self.assertFalse(ok)

        # 3. Starting case
        mock_run.return_value = MagicMock(
            stdout="Starting Liferay Digital Experience Platform...",
            stderr="",
            returncode=0,
        )
        status, ok = self.manager._check_liferay_health_logs("test-container")
        self.assertEqual(status, "Starting...")
        self.assertTrue(ok)

        # 4. Warning case
        mock_run.return_value = MagicMock(
            stdout="WARN: Some slow background task", stderr="", returncode=0
        )
        status, ok = self.manager._check_liferay_health_logs("test-container")
        self.assertTrue("Warning" in status)
        self.assertEqual(ok, "warn")

    @patch("ldm_core.handlers.diagnostics.verify_executable_checksum")
    @patch("ldm_core.handlers.diagnostics.check_for_updates")
    @patch("ldm_core.handlers.diagnostics.platform.platform")
    @patch("shutil.which")
    def test_cmd_doctor_basic(
        self, mock_which, mock_platform, mock_update, mock_verify
    ):
        mock_verify.return_value = ("Verified", True, VERSION)
        mock_update.return_value = (VERSION, None)
        mock_platform.return_value = "Test-OS"
        mock_which.return_value = "/usr/bin/mock"
        self.manager.args.skip_project = True

        # Use a nested patch for subprocess.run to be absolutely sure
        with patch("ldm_core.handlers.diagnostics.subprocess.run") as mock_subproc:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "27.0.0"
            mock_subproc.return_value = mock_res

            with (
                patch("builtins.print"),
                patch.object(Path, "exists", return_value=True),
                patch.object(Path, "read_text", return_value=""),
                patch.object(Path, "home", return_value=Path("/tmp/home")),
                patch("ldm_core.handlers.diagnostics.run_command", return_value="200"),
                patch(
                    "ldm_core.utils.get_compose_cmd", return_value=["docker", "compose"]
                ),
            ):
                try:
                    self.manager.cmd_doctor()
                except SystemExit as e:
                    self.assertEqual(e.code, 0)

    def test_validate_properties_valid(self):
        self.manager._use_real_validate = True
        self.test_file.write_text("key1=value1\nkey2=value2")
        status, ok, errors = self.manager.validate_properties_file(self.test_file)
        self.assertTrue(ok)
        self.assertEqual(status, "Valid Structure")
        self.assertEqual(len(errors), 0)

    def test_validate_properties_duplicates(self):
        self.manager._use_real_validate = True
        self.test_file.write_text("key1=value1\nkey1=value2\nkey2=v3")
        status, ok, errors = self.manager.validate_properties_file(self.test_file)
        self.assertEqual(ok, "warn")
        self.assertTrue(any("Duplicate key 'key1'" in e for e in errors))

    def test_validate_properties_broken_continuation(self):
        self.manager._use_real_validate = True
        # Line ends in backslash but next line is empty
        self.test_file.write_text("key1=value1\\\n\nkey2=value2")
        status, ok, errors = self.manager.validate_properties_file(self.test_file)
        self.assertEqual(ok, "warn")
        self.assertTrue(any("Broken continuation" in e for e in errors))

    def test_validate_properties_orphaned_line(self):
        self.manager._use_real_validate = True
        self.test_file.write_text("key1=value1\norphaned line here")
        status, ok, errors = self.manager.validate_properties_file(self.test_file)
        self.assertEqual(ok, "warn")
        self.assertTrue(any("Orphaned line" in e for e in errors))

    def test_validate_lcp_json_missing_id(self):
        lcp_file = Path("/tmp/LCP.json")
        lcp_file.write_text('{"ports": [{"targetPort": 8080}]}')
        try:
            status, ok, errors = self.manager.validate_lcp_json(lcp_file)
            self.assertEqual(ok, "warn")
            self.assertTrue(any("Missing mandatory 'id'" in e for e in errors))
        finally:
            if lcp_file.exists():
                lcp_file.unlink()

    def test_validate_lcp_json_invalid_port(self):
        lcp_file = Path("/tmp/LCP.json")
        lcp_file.write_text('{"id": "test", "ports": [{"noPort": 1}]}')
        try:
            status, ok, errors = self.manager.validate_lcp_json(lcp_file)
            self.assertEqual(ok, "warn")
            self.assertTrue(any("missing 'targetPort'" in e for e in errors))
        finally:
            if lcp_file.exists():
                lcp_file.unlink()


if __name__ == "__main__":
    unittest.main()


class TestDiagnosticsCompletion(unittest.TestCase):
    def setUp(self):
        self.manager = DiagnosticsHandler()
        self.test_home = Path("/tmp/home-test")
        self.test_home.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil

        if self.test_home.exists():
            shutil.rmtree(self.test_home)

    @patch("ldm_core.handlers.diagnostics.get_actual_home")
    def test_is_completion_enabled_detects_marker(self, mock_home):
        mock_home.return_value = self.test_home

        with patch.dict(
            "ldm_core.handlers.diagnostics.os.environ", {"SHELL": "/bin/zsh"}
        ):
            zshrc = self.test_home / ".zshrc"

            # Case 1: File doesn't exist
            # print(f"DEBUG: Shell={os.environ.get('SHELL')}, Home={self.test_home}, exists={zshrc.exists()}")
            self.assertFalse(self.manager.is_completion_enabled())

            # Case 2: File exists but no marker
            zshrc.write_text("# No completion here")
            self.assertFalse(self.manager.is_completion_enabled())

            # Case 3: Marker present (new style)
            zshrc.write_text('eval "$(ldm completion zsh)"')
            self.assertTrue(self.manager.is_completion_enabled())

            # Case 4: Marker present (legacy style)
            zshrc.write_text('eval "$(register-python-argcomplete ldm)"')
            self.assertTrue(self.manager.is_completion_enabled())
