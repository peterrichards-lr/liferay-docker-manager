import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.cloud import CloudService


class MockArgs:
    def __init__(self):
        self.list_envs = False
        self.list_backups = False
        self.sync_env = False
        self.download = False
        self.restore = False
        self.env_id = None
        self.project = None
        self.service = "liferay"
        self.follow = False
        self.db = None
        self.no_move = False


class MockManager:
    def __init__(self):
        self.args = MockArgs()
        self.non_interactive = False

        from ldm_core.defaults import DefaultsManager

        self.defaults = DefaultsManager()
        self.assets = MagicMock()

    def detect_project_path(self, *args, **kwargs):
        return Path("/tmp/proj")

    def read_meta(self, *args, **kwargs):
        return {}

    def write_meta(self, *args, **kwargs):
        pass

    def setup_paths(self, *args, **kwargs):
        return {"root": Path("/tmp/proj"), "data": Path("/tmp/proj/data")}

    def cmd_restore(self, *args, **kwargs):
        pass


class TestCloudService(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.cloud = CloudService(self.manager)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_is_cloud_authenticated_true(self, mock_run, mock_which):
        mock_which.return_value = "/usr/local/bin/lcp"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "some-token"
        mock_run.return_value = mock_res

        is_auth, reason = self.cloud._is_cloud_authenticated()
        self.assertTrue(is_auth)
        self.assertEqual(reason, "Authenticated")

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_is_cloud_authenticated_false(self, mock_run, mock_which):
        mock_which.return_value = "/usr/local/bin/lcp"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "No token available"
        mock_run.return_value = mock_res

        is_auth, reason = self.cloud._is_cloud_authenticated()
        self.assertFalse(is_auth)
        self.assertEqual(reason, "Not authenticated")

    @patch("ldm_core.handlers.cloud.CloudService._is_cloud_authenticated")
    def test_ensure_cloud_auth_already_auth(self, mock_auth):
        mock_auth.return_value = (True, "Authenticated")
        self.assertTrue(self.cloud.ensure_cloud_auth())

    @patch("ldm_core.ui.UI.confirm", return_value=True)
    @patch("ldm_core.ui.UI.die")
    @patch("ldm_core.handlers.cloud.CloudService._is_cloud_authenticated")
    def test_ensure_cloud_auth_die_if_not_installed(
        self, mock_auth, mock_die, mock_confirm
    ):
        mock_auth.return_value = (False, "LCP CLI not installed")
        mock_die.side_effect = SystemExit
        with self.assertRaises(SystemExit):
            self.cloud.ensure_cloud_auth()
        mock_die.assert_any_call(
            "Liferay Cloud CLI (lcp) is not installed. Install it to use cloud features.",
            exit_code=2,
        )

    @patch("shutil.which")
    def test_run_lcp_cmd_cli_not_found(self, mock_which):
        mock_which.return_value = None
        with patch("ldm_core.ui.UI.die") as mock_die:
            self.cloud._run_lcp_cmd(["test"])
            mock_die.assert_called_with("LCP CLI not found.")

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_run_lcp_cmd_success(self, mock_run, mock_which):
        mock_which.return_value = "/usr/local/bin/lcp"
        mock_res = MagicMock()
        mock_res.stdout = '{"status": "ok"}'
        mock_run.return_value = mock_res

        # Note: _run_lcp_cmd currently hardcodes capture_json = False internally
        res = self.cloud._run_lcp_cmd(["backup", "list"], capture_json=False)
        self.assertEqual(res, mock_res.stdout)

    @patch("shutil.which")
    @patch("subprocess.Popen")
    @patch("ldm_core.ui.UI.die")
    @patch("ldm_core.ui.UI.error")
    def test_run_lcp_cmd_expired_token(
        self, mock_error, mock_die, mock_popen, mock_which
    ):
        mock_which.return_value = "/usr/local/bin/lcp"

        # Mock Popen to simulate a failed interactive prompt
        mock_process = MagicMock()
        mock_process.stdout.readline.side_effect = [
            'You need to log in on liferay.cloud before using "lcp backup".\n',
            "",
        ]
        mock_process.wait.return_value = 1
        mock_popen.return_value = mock_process

        # We must pass spinner=True to trigger the Popen stream branch
        spinner_mock = MagicMock()
        res = self.cloud._run_lcp_cmd(["backup", "list"], spinner=spinner_mock)

        # Verify LDM intercepted it and ran UI.die with exit code 2
        self.assertIsNone(res)
        mock_error.assert_any_call("Your Liferay Cloud session has expired.")
        mock_die.assert_called_with(
            "Please run 'lcp login' to re-authenticate.", exit_code=2
        )

    @patch("ldm_core.handlers.cloud.CloudService._run_lcp_cmd")
    def test_get_cloud_liferay_version(self, mock_run):
        mock_run.return_value = [
            {"id": "liferay", "image": "liferay/dxp:2026.q1.4-lts"}
        ]
        version = self.cloud._get_cloud_liferay_version("my-project", "uat")
        self.assertEqual(version, "2026.q1.4-lts")

    @patch("ldm_core.handlers.cloud.CloudService.ensure_cloud_auth")
    @patch("ldm_core.handlers.cloud.CloudService._run_lcp_cmd")
    def test_cmd_cloud_fetch_list_envs(self, mock_run, mock_auth):
        self.manager.args.list_envs = True
        self.manager.args.env_id = None
        mock_run.return_value = "env1, env2"

        with patch("builtins.print") as mock_print:
            self.cloud.cmd_cloud_fetch("proj1")
            mock_print.assert_called_with("env1, env2")

    @patch("ldm_core.handlers.cloud.CloudService.ensure_cloud_auth")
    def test_cmd_cloud_fetch_sync_env(self, mock_auth):
        self.manager.args.list_envs = False
        self.manager.args.sync_env = True
        self.manager.args.env_id = "uat"

        mock_root = Path("/tmp/proj1")
        mock_lcp_json = {
            "env": {"GLOBAL_VAR": "global"},
            "environments": {
                "uat": {"env": {"LIFERAY_DEBUG": "true", "CUSTOM_VAR": "hello"}}
            },
        }

        with (
            patch.object(self.manager, "detect_project_path", return_value=mock_root),
            patch.object(self.manager, "read_meta", return_value={}),
            patch.object(self.manager, "write_meta") as mock_write,
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", return_value=json.dumps(mock_lcp_json)),
        ):
            self.cloud.cmd_cloud_fetch("proj1")

            self.assertTrue(mock_write.called)
            meta = mock_write.call_args[0][1]
            custom_env = json.loads(meta["custom_env"])
            self.assertEqual(custom_env["LIFERAY_DEBUG"], "true")
            self.assertEqual(custom_env["GLOBAL_VAR"], "global")

    @patch("ldm_core.handlers.cloud.CloudService.ensure_cloud_auth")
    @patch("ldm_core.handlers.cloud.CloudService._run_lcp_cmd")
    @patch("ldm_core.handlers.cloud.CloudService._verify_cloud_backup_checksums")
    @patch("pathlib.Path.mkdir")
    def test_cmd_cloud_fetch_download(
        self, mock_mkdir, mock_verify, mock_run, mock_auth
    ):
        self.manager.args.list_envs = False
        self.manager.args.sync_env = False
        self.manager.args.download = True
        self.manager.args.env_id = "uat"

        # Mock backup list response
        mock_run.side_effect = [
            [{"id": "backup123", "created": "today"}],  # backup list
            "Downloaded",  # backup download output
        ]

        mock_root = Path("/tmp/proj1")
        mock_db = (
            mock_root
            / "snapshots"
            / "cloud_uat_backup123"
            / "nested"
            / "database"
            / "dump.gz"
        )
        mock_vol = (
            mock_root
            / "snapshots"
            / "cloud_uat_backup123"
            / "nested"
            / "doclib"
            / "uuid"
        )

        with (
            patch.object(self.manager, "detect_project_path", return_value=mock_root),
            patch.object(self.manager, "read_meta", return_value={}),
            patch.object(self.manager, "setup_paths", return_value={"root": mock_root}),
            patch("pathlib.Path.iterdir", return_value=[mock_db.parent.parent]),
            patch("pathlib.Path.glob", side_effect=[[mock_db], [mock_vol]]),
            patch("shutil.move"),
            patch("shutil.rmtree"),
        ):
            self.cloud.cmd_cloud_fetch("proj1")
            self.assertTrue(mock_verify.called)

    def test_verify_cloud_backup_checksums_success(self):
        import hashlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir)
            db_file = backup_dir / "database.gz"
            db_file.write_bytes(b"DATA")
            expected_md5 = hashlib.md5(b"DATA").hexdigest()

            meta = {"database": {"checksum": expected_md5}}
            with patch("ldm_core.ui.UI.info") as mock_info:
                self.cloud._verify_cloud_backup_checksums(backup_dir, meta)
                # Verify OK was logged
                self.assertTrue(any("OK" in str(c) for c in mock_info.call_args_list))

    def test_detect_db_type_postgresql(self):
        import gzip
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir)
            db_gz = backup_dir / "database.gz"
            with gzip.open(db_gz, "wt") as f:
                f.write("-- PostgreSQL database dump\n...")

            self.assertEqual(self.cloud._detect_db_type(backup_dir), "postgresql")

    def test_detect_db_type_mysql(self):
        import gzip
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir)
            db_gz = backup_dir / "database.gz"
            with gzip.open(db_gz, "wt") as f:
                f.write("-- MySQL dump\n...")

            self.assertEqual(self.cloud._detect_db_type(backup_dir), "mysql")

    def test_detect_db_type_unknown(self):
        import gzip
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir)
            db_gz = backup_dir / "database.gz"
            with gzip.open(db_gz, "wt") as f:
                f.write("Some other content")

            self.assertIsNone(self.cloud._detect_db_type(backup_dir))

    @patch("ldm_core.ui.UI.info")
    def test_resolve_hydrate_db_type_auto_detect(self, mock_info):
        import gzip
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir)
            db_gz = backup_dir / "database.gz"
            with gzip.open(db_gz, "wt") as f:
                f.write("-- PostgreSQL database dump\n...")

            self.manager.args.db = None
            res = self.cloud._resolve_hydrate_db_type(backup_dir)
            self.assertEqual(res, "postgresql")
            self.assertTrue(
                any("postgresql" in str(c) for c in mock_info.call_args_list)
            )

    @patch("ldm_core.ui.UI.die")
    def test_resolve_hydrate_db_type_mismatch(self, mock_die):
        import gzip
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir)
            db_gz = backup_dir / "database.gz"
            with gzip.open(db_gz, "wt") as f:
                f.write("-- PostgreSQL database dump\n...")

            self.manager.args.db = "mysql"
            self.cloud._resolve_hydrate_db_type(backup_dir)
            mock_die.assert_called_once()
            args = mock_die.call_args[0][0]
            self.assertIn("Database type mismatch", args)

    @patch("ldm_core.ui.UI.ask_choices")
    def test_resolve_hydrate_db_type_prompt(self, mock_ask):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir)
            # No database.gz so detection fails
            self.manager.args.db = None
            mock_ask.return_value = "mysql"

            res = self.cloud._resolve_hydrate_db_type(backup_dir)
            self.assertEqual(res, "mysql")
            mock_ask.assert_called_once_with(
                "Database type for hydration",
                ["postgresql", "mysql"],
                default="postgresql",
            )

    @patch("ldm_core.ui.UI.die")
    def test_resolve_hydrate_db_type_non_interactive_die(self, mock_die):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir)
            self.manager.args.db = None
            self.manager.non_interactive = True

            self.cloud._resolve_hydrate_db_type(backup_dir)
            mock_die.assert_called_once()
            self.assertIn("Could not determine database type", mock_die.call_args[0][0])

    @patch("ldm_core.ui.UI.ask_choices")
    @patch(
        "ldm_core.handlers.cloud.CloudService._resolve_hydrate_db_type",
        return_value="postgresql",
    )
    @patch("ldm_core.handlers.cloud.CloudService.hydrate_cloud_backup")
    @patch("ldm_core.handlers.cloud.PROJECT_META_FILE", "meta_fake")
    def test_cmd_hydrate_existing_project(self, mock_hydrate, mock_db_type, mock_ask):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir).resolve()
            (backup_dir / "database.gz").write_text("db")
            (backup_dir / "volume.tgz").write_text("vol")

            project_dir = Path(tmp_dir) / "my_project"
            project_dir.mkdir()
            # Create metadata file to mark it as existing
            (project_dir / "meta_fake").write_text("key=value")

            with patch.object(
                self.manager, "detect_project_path", return_value=project_dir
            ):
                with patch.object(
                    self.cloud.manager.assets, "prompt_for_tag"
                ) as mock_prompt_tag:
                    self.cloud.cmd_hydrate(str(backup_dir), project_id="my_project")

                    # prompt_for_tag should NOT have been called
                    mock_prompt_tag.assert_not_called()
                    # hydrate_cloud_backup should be called with tag_for_seed=None
                    mock_hydrate.assert_called_once_with(
                        "my_project", backup_dir, tag_for_seed=None, no_run=None
                    )

    @patch("ldm_core.ui.UI.ask_choices")
    @patch(
        "ldm_core.handlers.cloud.CloudService._resolve_hydrate_db_type",
        return_value="postgresql",
    )
    @patch("ldm_core.handlers.cloud.CloudService.hydrate_cloud_backup")
    @patch("ldm_core.handlers.cloud.PROJECT_META_FILE", "meta_fake")
    def test_cmd_hydrate_new_project(self, mock_hydrate, mock_db_type, mock_ask):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir).resolve()
            (backup_dir / "database.gz").write_text("db")
            (backup_dir / "volume.tgz").write_text("vol")

            project_dir = Path(tmp_dir) / "new_project"
            # Do NOT create project_dir / "meta_fake" (so it's a new project)
            project_dir.mkdir()

            with patch.object(
                self.manager, "detect_project_path", return_value=project_dir
            ):
                with patch.object(
                    self.cloud.manager.assets, "prompt_for_tag", return_value="7.4"
                ) as mock_prompt_tag:
                    self.cloud.cmd_hydrate(str(backup_dir), project_id="new_project")

                    # prompt_for_tag should have been called
                    mock_prompt_tag.assert_called_once()
                    # hydrate_cloud_backup should be called with tag_for_seed="7.4"
                    mock_hydrate.assert_called_once_with(
                        "new_project", backup_dir, tag_for_seed="7.4", no_run=None
                    )


class TestRunLcpCmdTimeout(unittest.TestCase):
    """Tests for subprocess timeout guards in _run_lcp_cmd (Issue #467)."""

    def setUp(self):
        self.cloud = CloudService()

    @patch("shutil.which")
    @patch("subprocess.run")
    @patch("ldm_core.ui.UI.error")
    def test_subprocess_run_timeout_returns_none(
        self, mock_error, mock_run, mock_which
    ):
        """subprocess.TimeoutExpired from the non-spinner path must be caught and return None."""
        import subprocess

        mock_which.return_value = "/usr/local/bin/lcp"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["lcp"], timeout=300)

        result = self.cloud._run_lcp_cmd(["backup", "list"], timeout=300)

        self.assertIsNone(result)
        # A user-friendly timeout message must be logged
        self.assertTrue(
            any("timed out" in str(c) for c in mock_error.call_args_list),
            "Expected a timeout error message to be logged",
        )

    @patch("shutil.which")
    @patch("subprocess.Popen")
    @patch("ldm_core.ui.UI.error")
    def test_spinner_path_timeout_kills_process(
        self, mock_error, mock_popen, mock_which
    ):
        """When the timer fires during the Popen spinner path, the process is killed and None returned."""
        import threading

        mock_which.return_value = "/usr/local/bin/lcp"

        mock_process = MagicMock()
        # Simulate the process hanging: readline blocks until kill() ends the pipe
        kill_event = threading.Event()

        def _blocking_readline():
            # Block until kill() is simulated (event set by test thread)
            kill_event.wait(timeout=5)
            return ""  # Empty string signals EOF

        mock_process.stdout.readline.side_effect = _blocking_readline
        mock_process.wait.return_value = -9  # Killed
        mock_popen.return_value = mock_process

        spinner_mock = MagicMock()

        # Use a very short timeout so the test doesn't stall
        def _run():
            return self.cloud._run_lcp_cmd(
                ["backup", "list"], spinner=spinner_mock, timeout=0.1
            )

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run)
            # Allow the timer to fire
            kill_event.set()
            result = future.result(timeout=5)

        self.assertIsNone(result)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_custom_timeout_is_passed_to_subprocess_run(self, mock_run, mock_which):
        """The timeout kwarg must be forwarded to subprocess.run."""
        mock_which.return_value = "/usr/local/bin/lcp"
        mock_res = MagicMock()
        mock_res.stdout = "some output"
        mock_run.return_value = mock_res

        self.cloud._run_lcp_cmd(["backup", "list"], timeout=60)

        call_kwargs = mock_run.call_args.kwargs
        self.assertEqual(call_kwargs.get("timeout"), 60)


if __name__ == "__main__":
    unittest.main()
