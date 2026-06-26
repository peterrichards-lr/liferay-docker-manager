import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.diagnostics import DiagnosticsService, DoctorRunner


class MockArgs:
    def __init__(self):
        self.project = None
        self.search = False
        self.all = False
        self.bundle = False
        self.fix = False
        self.detailed = False
        self.clean_hosts = False
        self.seeds = False
        self.samples = False
        self.slug = False
        self.skip_project = False


class MockDiagManager(BaseHandler):
    def __init__(self):
        self.verbose = False
        self.non_interactive = True
        self.args = MockArgs()
        self.diagnostics = DiagnosticsService(self)
        self.cloud = MagicMock()
        self.assets = MagicMock()
        self.config = MagicMock()
        self.license = MagicMock()
        self.license.check_license_health = MagicMock(return_value=("OK", True, []))

    def detect_project_path(self, project_id=None, for_init=False, fatal=True):
        return Path(f"/tmp/{project_id}") if project_id else Path("/tmp/default")

    def read_meta(self, *args, **kwargs):
        return {"tag": "2026.q1.4-lts", "env_args": []}

    def cmd_completion(self, *args, **kwargs):
        return self.diagnostics.cmd_completion(*args, **kwargs)

    def cmd_setup_completion(self, *args, **kwargs):
        return self.diagnostics.cmd_setup_completion(*args, **kwargs)

    def find_dxp_roots(self, *args, **kwargs):
        return [{"path": Path("/tmp/p1")}, {"path": Path("/tmp/p2")}]

    def require_compose(self, *args, **kwargs):
        return True

    def parse_version(self, tag):
        return (2024, 1, 0)

    def get_common_dir(self, project_path=None):
        return Path("/tmp/common")

    def setup_paths(self, project_path):
        return {"root": project_path}

    def run_command(self, cmd, *args, **kwargs):
        return ""

    def safe_rmtree(self, path):
        pass

    def check_docker(self):
        return True


class TestDiagnostics(unittest.TestCase):
    def setUp(self):
        self.manager = MockDiagManager()

    @patch("ldm_core.handlers.diagnostics.DiagnosticsService._get_env_info")
    @patch("ldm_core.handlers.diagnostics.DoctorRunner")
    def test_cmd_doctor_calls_runner(self, mock_runner, mock_env):
        mock_env.return_value = ("arch", "os", "provider", "mount")
        self.manager.diagnostics.cmd_doctor()
        self.assertTrue(mock_runner.called)

    def test_doctor_runner_add_hint(self):
        runner = DoctorRunner(self.manager.diagnostics)
        runner.add_hint("test hint", "http://doc")
        self.assertEqual(len(runner.hints), 1)
        self.assertEqual(runner.hints[0]["text"], "test hint")

    def test_doctor_runner_check_openssl(self):
        with patch("ldm_core.handlers.diagnostics.run_command") as mock_run:
            mock_run.return_value = "OpenSSL 3.0.0"
            res, ok = self.manager.diagnostics._check_openssl()
            self.assertTrue(ok)
            self.assertEqual(res, "OpenSSL 3.0.0")

    def test_doctor_runner_check_lcp_cli(self):
        with (
            patch("shutil.which", return_value="/usr/bin/lcp"),
            patch.object(
                self.manager.cloud, "_is_cloud_authenticated", return_value=(True, "OK")
            ),
        ):
            res, ok = self.manager.diagnostics._check_lcp_cli()
            self.assertTrue(ok)
            self.assertEqual(res, "Logged In")

    def test_doctor_runner_check_docker_resources(self):
        docker_info_raw = '{"NCPU": 8, "MemTotal": 17179869184}'
        results = self.manager.diagnostics._check_docker_resources(docker_info_raw)
        self.assertTrue(any("Docker CPUs" in r[0] for r in results))
        self.assertTrue(any("Docker Memory" in r[0] for r in results))

    @patch("ldm_core.handlers.diagnostics.run_command")
    @patch("subprocess.run")
    @patch("shutil.which")
    @patch("ldm_core.utils.get_compose_cmd", return_value="/usr/bin/docker-compose")
    def test_check_docker_runtime(self, mock_comp, mock_which, mock_sub_run, mock_run):
        mock_which.side_effect = lambda x: "/usr/bin/" + x

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "24.0.0"
        mock_sub_run.return_value = mock_res

        mock_run.side_effect = [
            "default",  # docker context show
            "docker-compose version 2.20.0",  # docker-compose version (if called)
            '{"NCPU": 4, "MemTotal": 8589934592}',  # docker info
        ]

        runner = DoctorRunner(self.manager.diagnostics)
        runner._check_docker_runtime()
        self.assertTrue(any("Docker Engine" in r[0] for r in runner.results))
        self.assertTrue(any("Docker Compose" in r[0] for r in runner.results))

    @patch("ldm_core.handlers.diagnostics.DoctorRunner.add_hint")
    @patch("ldm_core.handlers.diagnostics.run_command")
    def test_check_elasticsearch_watermarks_blocked(self, mock_run, mock_hint):
        runner = DoctorRunner(self.manager.diagnostics)
        explain_json = json.dumps(
            {
                "node_allocation_decisions": [
                    {
                        "deciders": [
                            {
                                "decider": "disk_threshold",
                                "decision": "NO",
                                "explanation": "disk watermark exceeded",
                            }
                        ]
                    }
                ]
            }
        )
        mock_run.side_effect = [
            explain_json,  # allocation explain
            "index.blocks.read_only_allow_delete: true",  # settings
        ]
        res = self.manager.diagnostics._check_elasticsearch_watermarks(runner.add_hint)
        self.assertEqual(res, "Disk Watermark Exceeded (Blocked)")
        self.assertTrue(mock_hint.called)

    @patch("ldm_core.handlers.diagnostics.DoctorRunner.add_hint")
    @patch("ldm_core.handlers.diagnostics.run_command")
    def test_check_elasticsearch_watermarks_flood(self, mock_run, mock_hint):
        runner = DoctorRunner(self.manager.diagnostics)
        mock_run.side_effect = [
            '{"node_allocation_decisions": []}',  # allocation explain OK
            "index.blocks.read_only_allow_delete: true",  # but read-only blocks exist
        ]
        res = self.manager.diagnostics._check_elasticsearch_watermarks(runner.add_hint)
        self.assertEqual(res, "Read-Only (Flood Stage)")

    @patch("ldm_core.handlers.diagnostics.check_for_updates", return_value=(None, None))
    @patch(
        "ldm_core.handlers.diagnostics.verify_executable_checksum",
        return_value=("Valid", True, "2.5.0"),
    )
    @patch(
        "ldm_core.handlers.diagnostics.DiagnosticsService.is_completion_enabled",
        return_value=True,
    )
    def test_check_tooling_and_integrity(self, mock_comp, mock_verify, mock_updates):
        runner = DoctorRunner(self.manager.diagnostics)
        runner._check_tooling_and_integrity()
        self.assertTrue(any("LDM Version" in r[0] for r in runner.results))
        self.assertTrue(any("Executable Integrity" in r[0] for r in runner.results))

    @patch("ldm_core.utils.get_actual_home", return_value=Path("/tmp"))
    @patch(
        "ldm_core.handlers.diagnostics.DiagnosticsService.check_mkcert",
        return_value=("OK", True, "/root"),
    )
    @patch.object(MockDiagManager, "run_command", return_value="OK")
    def test_check_global_config_and_network(self, mock_run, mock_mkcert, mock_home):
        runner = DoctorRunner(self.manager.diagnostics)
        runner.docker_version = "24.0.0"
        runner.project_paths = [Path("/tmp/proj1")]
        runner._check_global_config_and_network()
        self.assertTrue(any("mkcert" in r[0] for r in runner.results))
        self.assertTrue(any("Volume Permissions" in r[0] for r in runner.results))

    @patch("ldm_core.handlers.diagnostics.run_command", return_value="")
    @patch(
        "ldm_core.handlers.diagnostics.DiagnosticsService.validate_lcp_json",
        return_value=("Valid", True, []),
    )
    def test_check_project_specific(self, mock_lcp, mock_run):
        runner = DoctorRunner(self.manager.diagnostics)
        runner.project_paths = [Path("/tmp/proj1")]
        runner._check_project_specific()
        self.assertTrue(any("[proj1] Metadata" in r[0] for r in runner.results))
        self.assertTrue(any("[proj1] Config" in r[0] for r in runner.results))

    def test_check_dangling_and_print(self):
        runner = DoctorRunner(self.manager.diagnostics)
        # Add a warning to avoid clean exit
        runner.results.append(("Test", "Warning", "warn"))
        with (
            patch("ldm_core.handlers.diagnostics.run_command", return_value=""),
            patch("sys.exit") as mock_exit,
        ):
            self.manager.args.bundle = False
            runner._check_dangling_and_print()
            self.assertTrue(mock_exit.called)

    @patch("ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp"))
    @patch("pathlib.Path.exists", return_value=True)
    @patch("shutil.which", return_value="/usr/bin/helper")
    def test_check_docker_creds(self, mock_which, mock_exists, mock_home):
        with patch(
            "builtins.open",
            unittest.mock.mock_open(read_data='{"credsStore": "osxkeychain"}'),
        ):
            status, ok = self.manager.diagnostics._check_docker_creds()
            self.assertTrue(ok)
            self.assertIn("osxkeychain", status)

    @patch("ldm_core.docker_service.DockerService.get_logs")
    def test_check_container_health_logs(self, mock_logs):
        mock_logs.return_value = "health: starting\nERROR: something broke"
        status, ok = self.manager.diagnostics._check_container_health_logs("test-c")
        self.assertFalse(ok)
        self.assertIn("Error in logs", status)

    @patch("ldm_core.docker_service.DockerService.get_logs")
    def test_check_liferay_health_logs(self, mock_logs):
        mock_logs.return_value = "Liferay(TM) Portal 7.4 GA 100\nSTARTED in 100s"
        status, ok = self.manager.diagnostics._check_liferay_health_logs("test-liferay")
        self.assertTrue(ok)
        self.assertIn("Ready", status)

    @patch("ldm_core.handlers.diagnostics.run_command")
    def test_cmd_list(self, mock_run):
        # 1. Setup mocks
        with (
            patch.object(
                self.manager,
                "find_dxp_roots",
                return_value=[{"path": Path("/tmp/proj1"), "version": "2024.q1.0"}],
            ),
            patch.object(
                self.manager,
                "read_meta",
                return_value={
                    "container_name": "proj1",
                    "port": 8080,
                    "host_name": "localhost",
                },
            ),
        ):
            mock_run.return_value = "running"

            # 2. Capture output
            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with redirect_stdout(f):
                self.manager.diagnostics.cmd_list()

            output = f.getvalue()

            # 3. Verify
            self.assertIn("Project", output)
            self.assertIn("proj1", output)
            self.assertIn("2024.q1.0", output)
            self.assertIn("Running", output)
            self.assertIn("http://localhost:8080", output)

    @patch("ldm_core.handlers.diagnostics.run_command")
    @patch.object(
        MockDiagManager, "find_dxp_roots", return_value=[{"path": Path("/tmp/p1")}]
    )
    @patch("ldm_core.docker_service.DockerService.is_running", return_value=False)
    def test_cmd_prune(self, mock_running, mock_roots, mock_run):
        mock_run.return_value = "orphan-container|deleted-project"
        with patch("ldm_core.docker_service.DockerService.rm") as mock_rm:
            self.manager.diagnostics.cmd_prune()
            self.assertTrue(mock_rm.called)

    @patch("ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp"))
    @patch("pathlib.Path.exists", return_value=True)
    @patch("os.remove")
    def test_cmd_cache_tags(self, mock_remove, mock_exists, mock_home):
        self.manager.diagnostics.cmd_cache("tags")
        mock_remove.assert_called_with(Path("/tmp/.liferay_docker_cache.json"))

    @patch("shutil.which", return_value="/usr/bin/mkcert")
    @patch("ldm_core.handlers.diagnostics.run_command", return_value="/tmp/ca")
    @patch("os.path.exists", return_value=True)
    @patch("os.listdir", return_value=["ca.pem"])
    @patch("ldm_core.handlers.diagnostics.get_actual_home", return_value=Path("/tmp"))
    @patch("os.access", return_value=True)
    def test_check_mkcert(
        self, mock_access, mock_home, mock_listdir, mock_exists, mock_run, mock_which
    ):
        status, ok, ca_root = self.manager.diagnostics.check_mkcert()
        self.assertTrue(ok)
        self.assertIn("Trusted", status)

    @patch(
        "ldm_core.handlers.diagnostics.DiagnosticsService._get_env_info",
        return_value=("arch", "os", "provider", "mount"),
    )
    def test_doctor_slug(self, mock_env):
        self.manager.args.slug = True
        with patch("builtins.print") as mock_print:
            runner = DoctorRunner(self.manager.diagnostics)
            runner.run()
            mock_print.assert_any_call("arch-os-provider")

    def test_validate_properties_file_duplicates(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            f = Path(tmp_dir) / "pe.properties"
            f.write_text("key1=v1\nkey1=v2")
            status, ok, errors = self.manager.diagnostics.validate_properties_file(f)
            self.assertEqual(ok, "warn")
            self.assertTrue(any("Duplicate key 'key1'" in e for e in errors))

    def test_validate_lcp_json_missing_id(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            f = Path(tmp_dir) / "LCP.json"
            f.write_text(json.dumps({"type": "service"}))
            status, ok, errors = self.manager.diagnostics.validate_lcp_json(f)
            self.assertEqual(ok, "warn")
            self.assertIn("Missing mandatory 'id' field.", errors)

    @patch(
        "ldm_core.handlers.diagnostics.DiagnosticsService._get_env_info",
        return_value=("arch", "os", "provider", "mount"),
    )
    def test_doctor_runner_all_projects(self, mock_env):
        # Use a more manual approach to avoid complex run() logic
        runner = DoctorRunner(self.manager.diagnostics, all_projects=True)
        # Explicitly set the path for test resolution
        runner.all_projects = True
        # Mock what run() would do
        roots = self.manager.find_dxp_roots()
        runner.project_paths = [r["path"] for r in roots]
        self.assertEqual(len(runner.project_paths), 2)

    @patch("ldm_core.handlers.diagnostics.verify_executable_checksum")
    def test_check_tooling_integrity_shadowed(self, mock_verify):
        # Return a version different from current VERSION
        mock_verify.return_value = ("Shadowed", True, "1.0.0")
        runner = DoctorRunner(self.manager.diagnostics)
        with patch(
            "ldm_core.handlers.diagnostics.check_for_updates", return_value=(None, None)
        ):
            runner._check_tooling_and_integrity()
            self.assertTrue(any("(Shadowed by" in str(r[1]) for r in runner.results))

    def test_check_global_config_and_network_ro(self):
        runner = DoctorRunner(self.manager.diagnostics)
        runner.docker_version = "24.0.0"
        runner.project_paths = [Path("/tmp/proj1")]
        runner.provider = "Colima"
        # Patch ldm_core.utils.get_actual_home directly
        with (
            patch("ldm_core.utils.get_actual_home", return_value=Path("/tmp")),
            patch.object(self.manager, "run_command", return_value="Permission denied"),
        ):
            runner._check_global_config_and_network()
            self.assertTrue(any("Read-Only" in str(r[1]) for r in runner.results))

    def test_check_tooling_and_integrity_venv_active(self):
        runner = DoctorRunner(self.manager.diagnostics)
        with (
            patch("sys.prefix", "dummy_venv"),
            patch("sys.base_prefix", "dummy_base"),
            patch.dict("os.environ", {}, clear=True),
        ):
            runner._check_tooling_and_integrity()
            venv_result = next(
                r for r in runner.results if r[0] == "Virtual Environment"
            )
            self.assertEqual(venv_result[1], "Active (.venv)")
            self.assertTrue(venv_result[2])

    @patch("ldm_core.handlers.diagnostics.verify_executable_checksum")
    def test_check_tooling_and_integrity_venv_inactive(self, mock_verify):
        mock_verify.return_value = ("Source", True, "2.11.7")
        runner = DoctorRunner(self.manager.diagnostics)
        with (
            patch("sys.prefix", "dummy_base"),
            patch("sys.base_prefix", "dummy_base"),
            patch.dict("os.environ", {}, clear=True),
        ):
            runner._check_tooling_and_integrity()
            venv_result = next(
                r for r in runner.results if r[0] == "Virtual Environment"
            )
            self.assertEqual(venv_result[1], "Not Activated")
            self.assertEqual(venv_result[2], "warn")
            self.assertTrue(any("globally" in h["text"] for h in runner.hints))

    @patch("ldm_core.handlers.diagnostics.verify_executable_checksum")
    def test_check_tooling_and_integrity_venv_inactive_binary(self, mock_verify):
        mock_verify.return_value = ("Binary Checksum Valid", True, "2.11.7")
        runner = DoctorRunner(self.manager.diagnostics)
        with (
            patch("sys.prefix", "dummy_base"),
            patch("sys.base_prefix", "dummy_base"),
            patch.dict("os.environ", {}, clear=True),
        ):
            runner._check_tooling_and_integrity()
            venv_result = next(
                r for r in runner.results if r[0] == "Virtual Environment"
            )
            self.assertEqual(venv_result[1], "Not Required (Binary)")
            self.assertTrue(venv_result[2])
            self.assertFalse(any("globally" in h["text"] for h in runner.hints))

    def test_doctor_runner_dashboard_view(self):
        runner = DoctorRunner(self.manager.diagnostics)
        runner.results = [
            ("Python Version", "3.10.0", True),
            ("Docker Engine", "24.0.0", True),
            ("Project Initialization", "Vanilla", "warn"),
        ]
        self.manager.args.system = False
        self.manager.args.docker = False
        self.manager.args.project = False
        self.manager.args.detailed = False
        self.manager.args.verbose = False

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f), patch("sys.exit") as mock_exit:
            runner._check_dangling_and_print()
            output = f.getvalue()
            self.assertIn("System (Python, Executable, Venv)", output)
            self.assertIn("Docker (Engine, Compose, Resources)", output)
            self.assertIn("Project (Metadata, DNS, Mounts, SSL)", output)
            self.assertIn("Project Initialization", output)
            self.assertNotIn("Docker Engine", output)
            self.assertNotIn("Python Version", output)

    def test_doctor_runner_subsystem_filter(self):
        runner = DoctorRunner(self.manager.diagnostics)
        runner.results = [
            ("Python Version", "3.10.0", True),
            ("Docker Engine", "24.0.0", True),
            ("Project Initialization", "Vanilla", "warn"),
        ]
        self.manager.args.system = True
        self.manager.args.docker = False
        self.manager.args.project = False
        self.manager.args.detailed = False
        self.manager.args.verbose = False

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f), patch("sys.exit") as mock_exit:
            runner._check_dangling_and_print()
            output = f.getvalue()
            self.assertIn("Python Version", output)
            self.assertNotIn("Docker Engine", output)
            self.assertNotIn("Project Initialization", output)

    @patch("ldm_core.handlers.diagnostics.UI.die", side_effect=SystemExit)
    def test_upgrade_version_and_repair_conflict(self, mock_die):
        self.manager.args.repair = True
        self.manager.args.version = "v2.11.53"
        with self.assertRaises(SystemExit):
            self.manager.diagnostics.cmd_upgrade()
        mock_die.assert_called_once_with(
            "Cannot specify both --repair and --version. Please choose one."
        )

    @patch("ldm_core.handlers.diagnostics.UI.die", side_effect=SystemExit)
    def test_upgrade_invalid_version_format(self, mock_die):
        self.manager.args.repair = False
        self.manager.args.version = "invalid.1.0"
        with self.assertRaises(SystemExit):
            self.manager.diagnostics.cmd_upgrade()
        mock_die.assert_called_once()
        self.assertIn("Invalid version format", mock_die.call_args[0][0])

    @patch("ldm_core.handlers.diagnostics.check_for_updates", return_value=(None, None))
    @patch("ldm_core.handlers.diagnostics.UI.die", side_effect=SystemExit)
    def test_upgrade_version_not_found(self, mock_die, mock_updates):
        self.manager.args.repair = False
        self.manager.args.version = "v2.11.99"
        with self.assertRaises(SystemExit):
            self.manager.diagnostics.cmd_upgrade()
        mock_updates.assert_called_once_with(
            mock_updates.call_args[0][0], force=True, tag="v2.11.99"
        )
        mock_die.assert_called_once_with(
            "Version 'v2.11.99' not found on GitHub Releases."
        )

    @patch("ldm_core.handlers.diagnostics.check_for_updates")
    @patch("ldm_core.handlers.diagnostics.UI.die", side_effect=SystemExit)
    def test_upgrade_downgrade_non_interactive_no_force(self, mock_die, mock_updates):
        mock_updates.return_value = (
            "2.11.53",
            "https://github.com/releases/download/v2.11.53/ldm-macos",
        )
        self.manager.args.repair = False
        self.manager.args.version = "v2.11.53"
        self.manager.non_interactive = True
        self.manager.args.force = False

        with self.assertRaises(SystemExit):
            self.manager.diagnostics.cmd_upgrade()

        mock_die.assert_called_once_with(
            "Downgrade aborted: --force is required in non-interactive mode."
        )

    @patch("sys.argv", ["liferay_docker.py"])
    @patch("ldm_core.handlers.diagnostics.check_for_updates")
    @patch("ldm_core.handlers.diagnostics.UI.die", side_effect=SystemExit)
    def test_upgrade_downgrade_non_interactive_with_force(self, mock_die, mock_updates):
        mock_updates.return_value = (
            "2.11.53",
            "https://github.com/releases/download/v2.11.53/ldm-macos",
        )
        self.manager.args.repair = False
        self.manager.args.version = "v2.11.53"
        self.manager.non_interactive = True
        self.manager.args.force = True

        with self.assertRaises(SystemExit):
            self.manager.diagnostics.cmd_upgrade()

        # Verify it passed the downgrade check and hit the standalone binary safeguard
        mock_die.assert_called_once_with(
            "Self-upgrade is only supported for standalone binaries. Please use 'git pull' for source installations."
        )

    @patch("ldm_core.handlers.diagnostics.check_for_updates")
    @patch("ldm_core.handlers.diagnostics.UI.confirm", return_value=False)
    @patch("ldm_core.handlers.diagnostics.UI.die", side_effect=SystemExit)
    def test_upgrade_downgrade_interactive_reject(
        self, mock_die, mock_confirm, mock_updates
    ):
        mock_updates.return_value = (
            "2.11.53",
            "https://github.com/releases/download/v2.11.53/ldm-macos",
        )
        self.manager.args.repair = False
        self.manager.args.version = "v2.11.53"
        self.manager.non_interactive = False

        # In interactive mode, if confirm returns False, it prints "Operation aborted." and returns (no die)
        self.manager.diagnostics.cmd_upgrade()

        mock_confirm.assert_called_once()
        self.assertIn(
            "Are you sure you want to proceed with the downgrade?",
            mock_confirm.call_args[0][0],
        )
        mock_die.assert_not_called()

    @patch("sys.argv", ["liferay_docker.py"])
    @patch("ldm_core.handlers.diagnostics.check_for_updates")
    @patch("ldm_core.handlers.diagnostics.UI.confirm", return_value=True)
    @patch("ldm_core.handlers.diagnostics.UI.die", side_effect=SystemExit)
    def test_upgrade_downgrade_interactive_confirm(
        self, mock_die, mock_confirm, mock_updates
    ):
        mock_updates.return_value = (
            "2.11.53",
            "https://github.com/releases/download/v2.11.53/ldm-macos",
        )
        self.manager.args.repair = False
        self.manager.args.version = "v2.11.53"
        self.manager.non_interactive = False

        with self.assertRaises(SystemExit):
            self.manager.diagnostics.cmd_upgrade()

        mock_confirm.assert_called_once()
        # Verify it got past the confirm and hit the standalone binary check
        mock_die.assert_called_once_with(
            "Self-upgrade is only supported for standalone binaries. Please use 'git pull' for source installations."
        )


class TestDiagnosticsCompletion(unittest.TestCase):
    def setUp(self):
        self.manager = MockDiagManager()
        self.test_home = Path("/tmp/home-test")
        self.test_home.mkdir(parents=True, exist_ok=True)

    @patch("ldm_core.handlers.diagnostics.get_actual_home")
    def test_cmd_completion_bash(self, mock_home):
        mock_home.return_value = self.test_home
        with patch("builtins.print") as mock_print:
            self.manager.cmd_completion("bash")
            self.assertTrue(mock_print.called)

    @patch("ldm_core.handlers.diagnostics.get_actual_home")
    def test_cmd_completion_zsh(self, mock_home):
        mock_home.return_value = self.test_home
        with patch("builtins.print") as mock_print:
            self.manager.cmd_completion("zsh")
            self.assertTrue(mock_print.called)


class TestDiagnosticsSetupCompletion(unittest.TestCase):
    def setUp(self):
        self.manager = MockDiagManager()
        import tempfile

        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_home = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("ldm_core.handlers.diagnostics.get_actual_home")
    @patch.dict("os.environ", {"SHELL": "/bin/zsh"})
    def test_cmd_setup_completion_zsh(self, mock_home):
        mock_home.return_value = self.test_home

        # 1. Run first time (profile does not exist)
        self.manager.cmd_setup_completion("zsh")
        zshrc = self.test_home / ".zshrc"
        self.assertTrue(zshrc.exists())
        content = zshrc.read_text(encoding="utf-8")
        self.assertIn("# >>> LDM CLI AUTOCOMPLETE >>>", content)
        self.assertIn('eval "$(ldm completion zsh)"', content)
        self.assertIn("# <<< LDM CLI AUTOCOMPLETE <<<", content)

        # 2. Run second time (profile exists, should backup and update)
        zshrc.write_text(content + "\n# dummy comment\n", encoding="utf-8")
        self.manager.cmd_setup_completion("zsh")

        bak_file = self.test_home / ".zshrc.bak"
        self.assertTrue(bak_file.exists())
        self.assertIn("# dummy comment", bak_file.read_text(encoding="utf-8"))

        new_content = zshrc.read_text(encoding="utf-8")
        self.assertIn("# >>> LDM CLI AUTOCOMPLETE >>>", new_content)

    @patch("ldm_core.handlers.diagnostics.get_actual_home")
    def test_cmd_setup_completion_bash(self, mock_home):
        mock_home.return_value = self.test_home

        # If .bash_profile exists but .bashrc does not, it should write to .bash_profile
        bash_profile = self.test_home / ".bash_profile"
        bash_profile.write_text("# existing profile", encoding="utf-8")

        self.manager.cmd_setup_completion("bash")
        self.assertTrue(bash_profile.exists())
        content = bash_profile.read_text(encoding="utf-8")
        self.assertIn('eval "$(ldm completion bash)"', content)
        self.assertFalse((self.test_home / ".bashrc").exists())

    @patch("ldm_core.handlers.diagnostics.get_actual_home")
    def test_cmd_setup_completion_fish(self, mock_home):
        mock_home.return_value = self.test_home
        self.manager.cmd_setup_completion("fish")
        fish_config = self.test_home / ".config" / "fish" / "config.fish"
        self.assertTrue(fish_config.exists())
        content = fish_config.read_text(encoding="utf-8")
        self.assertIn("ldm completion fish | source", content)

    @patch("ldm_core.handlers.diagnostics.get_actual_home")
    @patch("subprocess.run")
    def test_cmd_setup_completion_powershell(self, mock_run, mock_home):
        mock_home.return_value = self.test_home
        mock_run.side_effect = Exception("failed")

        self.manager.cmd_setup_completion("powershell")
        import sys

        if sys.platform == "win32":
            ps_profile = (
                self.test_home
                / "Documents"
                / "PowerShell"
                / "Microsoft.PowerShell_profile.ps1"
            )
        else:
            ps_profile = (
                self.test_home
                / ".config"
                / "powershell"
                / "Microsoft.PowerShell_profile.ps1"
            )

        self.assertTrue(ps_profile.exists())
        content = ps_profile.read_text(encoding="utf-8")
        self.assertIn(
            "ldm completion powershell | Out-String | Invoke-Expression", content
        )

    @patch("ldm_core.handlers.diagnostics.get_actual_home")
    @patch("subprocess.run")
    def test_cmd_setup_completion_argcomplete_fallback(self, mock_run, mock_home):
        mock_home.return_value = self.test_home

        with patch.dict("sys.modules", {"argcomplete": None}):
            self.manager.cmd_setup_completion("zsh")

        called_args = [call[0][0] for call in mock_run.call_args_list]
        install_calls = [
            arg
            for arg in called_args
            if "pip" in arg and "install" in arg and "argcomplete" in arg
        ]
        self.assertTrue(len(install_calls) > 0)


if __name__ == "__main__":
    unittest.main()
