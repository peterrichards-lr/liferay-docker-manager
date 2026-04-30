import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.snapshot import SnapshotHandler


class MockSnapshotManager(SnapshotHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True


class TestSnapshotHandler(unittest.TestCase):
    def setUp(self):
        self.manager = MockSnapshotManager()
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_snapshots_empty(self, mock_detect):
        mock_detect.return_value = self.test_dir

        with patch("ldm_core.ui.UI.info") as mock_info:
            backups = self.manager.cmd_snapshots()
            self.assertEqual(backups, [])
            mock_info.assert_called_with("No snapshots found.")

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_snapshot_abort_no_project(self, mock_detect):
        mock_detect.return_value = None
        self.assertIsNone(self.manager.cmd_snapshot())

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_restore_abort_no_project(self, mock_detect):
        mock_detect.return_value = None
        self.assertIsNone(self.manager.cmd_restore())

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.verify_runtime_environment")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    def test_cmd_snapshot_basic(
        self, mock_run, mock_verify, mock_paths, mock_meta, mock_detect
    ):
        mock_detect.return_value = self.test_dir

        # Ensure all required project subdirs exist in temp dir
        for d in [
            "snapshots",
            "data",
            "deploy",
            "files",
            "logs",
            "osgi/configs",
            "osgi/modules",
            "osgi/state",
        ]:
            (self.test_dir / d).mkdir(parents=True, exist_ok=True)
        (self.test_dir / "docker-compose.yml").touch()

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "state": self.test_dir / "osgi" / "state",
            "data": self.test_dir / "data",
            "deploy": self.test_dir / "deploy",
            "files": self.test_dir / "files",
            "logs": self.test_dir / "logs",
            "configs": self.test_dir / "osgi" / "configs",
            "modules": self.test_dir / "osgi" / "modules",
            "compose": self.test_dir / "docker-compose.yml",
        }
        mock_meta.return_value = {"use_shared_search": "false"}

        with (
            patch("tarfile.open"),
            patch("ldm_core.handlers.base.BaseHandler.write_meta"),
            patch("ldm_core.utils.calculate_sha256", return_value="dummy-sha"),
        ):
            self.manager.cmd_snapshot("proj")

        snap_dirs = [d for d in (self.test_dir / "snapshots").iterdir() if d.is_dir()]
        self.assertTrue(len(snap_dirs) >= 1)

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    def test_cmd_snapshot_with_mysql(self, mock_paths, mock_meta, mock_detect):
        mock_detect.return_value = self.test_dir

        # Setup dirs
        for d in [
            "snapshots",
            "data",
            "deploy",
            "files",
            "logs",
            "osgi/configs",
            "osgi/modules",
            "osgi/state",
            "osgi/log4j",
        ]:
            (self.test_dir / d).mkdir(parents=True, exist_ok=True)
        (self.test_dir / "docker-compose.yml").touch()

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "state": self.test_dir / "osgi" / "state",
            "data": self.test_dir / "data",
            "deploy": self.test_dir / "deploy",
            "files": self.test_dir / "files",
            "logs": self.test_dir / "logs",
            "configs": self.test_dir / "osgi" / "configs",
            "modules": self.test_dir / "osgi" / "modules",
            "compose": self.test_dir / "docker-compose.yml",
            "portal_log4j": self.test_dir / "osgi" / "log4j",
            "common": self.test_dir / "common",
        }
        mock_meta.return_value = {
            "db_type": "mysql",
            "container_name": "mysql-proj",
            "use_shared_search": "false",
        }

        # Mocking builtins.open to avoid FileNotFoundError when writing the dump
        with (
            patch("tarfile.open"),
            patch("ldm_core.handlers.base.BaseHandler.write_meta"),
            patch("ldm_core.utils.calculate_sha256", return_value="dummy-sha"),
            patch.object(self.manager, "verify_runtime_environment"),
            patch.object(self.manager, "run_command", return_value="DUMP-CONTENT"),
            patch("builtins.open", MagicMock()),
        ):
            self.manager.cmd_snapshot("mysql-proj")

            # Check for snapshot dir (which we can still check because mkdir isn't mocked)
            snap_dirs = [
                d for d in (self.test_dir / "snapshots").iterdir() if d.is_dir()
            ]
            self.assertTrue(len(snap_dirs) >= 1)

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    @patch.object(SnapshotHandler, "_extract_snapshot_archive")
    def test_cmd_restore_latest(
        self, mock_extract, mock_run, mock_paths, mock_meta, mock_detect
    ):
        # 1. Setup real filesystem state in temp dir
        root = self.test_dir
        snapshots_dir = root / "snapshots"
        snapshots_dir.mkdir()

        # Create two snapshots
        snap1 = snapshots_dir / "20260428_100000"
        snap2 = snapshots_dir / "20260428_110000"  # Newer

        for s in [snap1, snap2]:
            s.mkdir()
            (s / "files.tar.gz").touch()  # LDM marker

        mock_detect.return_value = root
        mock_paths.return_value = {
            "root": root,
            "backups": snapshots_dir,
            "state": root / "osgi" / "state",
            "data": root / "data",
            "deploy": root / "deploy",
            "files": root / "files",
            "modules": root / "osgi" / "modules",
            "configs": root / "osgi" / "configs",
        }
        mock_meta.return_value = {"container_name": "test-proj"}

        self.manager.args.latest = True
        self.manager.args.list = False
        self.manager.args.index = None
        self.manager.args.backup_dir = None

        with (
            patch("tarfile.open"),
            patch.object(self.manager, "verify_runtime_environment"),
            patch.object(
                self.manager, "read_meta", return_value={"container_name": "test-proj"}
            ),
            patch.object(
                SnapshotHandler, "_restore_from_cloud_layout", return_value=False
            ),
            # Mock verify_snapshot_integrity
            patch.object(
                SnapshotHandler,
                "verify_snapshot_integrity",
                return_value=True,
                create=True,
            ),
        ):
            self.manager.cmd_restore("proj")

        # Verify that the newest snapshot (snap2) was chosen
        mock_extract.assert_called_once()
        actual_choice = mock_extract.call_args[0][0]
        # Verify it chose snap2 by checking the path string contains the newer timestamp
        self.assertIn("20260428_110000", str(actual_choice))


if __name__ == "__main__":
    unittest.main()
