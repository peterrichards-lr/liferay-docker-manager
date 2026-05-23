import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.snapshot import SnapshotService


class MockSnapshotManager(BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True
        self.snapshot = SnapshotService(self)
        self.composer = MagicMock()
        self.composer.is_using_named_volumes.return_value = False

    def run_command(self, *args, **kwargs):
        return ""


class TestSnapshotService(unittest.TestCase):
    def setUp(self):
        self.manager = MockSnapshotManager()
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_snapshots_empty(self, mock_detect):
        mock_detect.return_value = self.test_dir

        with patch("ldm_core.ui.UI.info") as mock_info:
            backups = self.manager.snapshot.cmd_snapshots()
            self.assertEqual(backups, [])
            mock_info.assert_called_with("No snapshots found.")

    def test_dehydrate_hydration_hooks(self):
        # Test that dehydration/hydration are triggered when is_using_named_volumes is True
        paths = {
            "root": self.test_dir,
            "data": self.test_dir / "data",
            "state": self.test_dir / "state",
        }
        self.manager.composer.is_using_named_volumes.return_value = True

        with patch.object(self.manager.snapshot, "_sync_volume") as mock_sync:
            # 1. Test Dehydration
            self.manager.snapshot._dehydrate_named_volumes(paths)
            self.assertEqual(mock_sync.call_count, 2)
            mock_sync.assert_any_call(paths["data"], ANY, direction="from_volume")

            # 2. Test Hydration
            mock_sync.reset_mock()
            # Create host dirs to trigger hydration
            paths["data"].mkdir()
            paths["state"].mkdir()
            self.manager.snapshot._hydrate_named_volumes(paths)
            self.assertEqual(mock_sync.call_count, 2)
            mock_sync.assert_any_call(paths["data"], ANY, direction="to_volume")

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_snapshot_abort_no_project(self, mock_detect):
        mock_detect.return_value = None
        self.assertIsNone(self.manager.snapshot.cmd_snapshot())

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_restore_abort_no_project(self, mock_detect):
        mock_detect.return_value = None
        self.assertIsNone(self.manager.snapshot.cmd_restore())

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.verify_runtime_environment")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    @patch("ldm_core.utils.reclaim_volume_permissions")
    def test_cmd_snapshot_basic(
        self, mock_reclaim, mock_run, mock_verify, mock_paths, mock_meta, mock_detect
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
            self.manager.snapshot.cmd_snapshot("proj")

        snap_dirs = [d for d in (self.test_dir / "snapshots").iterdir() if d.is_dir()]
        self.assertTrue(len(snap_dirs) >= 1)

    def test_get_dir_size_empty(self):
        with patch("pathlib.Path.rglob", return_value=[]):
            size = self.manager.snapshot._get_dir_size(Path("/tmp"))
            self.assertEqual(size, "0.0 B")

    def test_get_dir_size_kb(self):
        mock_file = MagicMock()
        mock_file.is_file.return_value = True
        mock_file.stat.return_value.st_size = 1024
        with patch("pathlib.Path.rglob", return_value=[mock_file]):
            size = self.manager.snapshot._get_dir_size(Path("/tmp"))
            self.assertEqual(size, "1.0 KB")

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    def test_cmd_restore_integrity_success(self, mock_paths, mock_detect):
        mock_detect.return_value = self.test_dir
        (self.test_dir / "snapshots").mkdir(exist_ok=True)

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "state": self.test_dir / "osgi" / "state",
        }

        snap_dir = self.test_dir / "snapshots" / "20260512_120000"
        snap_dir.mkdir(parents=True)
        (snap_dir / "files.tar.gz").touch()
        (snap_dir / "files.tar.gz.sha256").write_text("match-sha")
        (snap_dir / "meta").touch()

        # Set latest flag
        self.manager.args.latest = True
        self.manager.args.verify = True
        self.manager.args.list = False
        self.manager.args.backup_dir = None

        with (
            patch("ldm_core.utils.calculate_sha256", return_value="match-sha"),
            patch.object(self.manager.snapshot, "_extract_snapshot_archive"),
            patch("ldm_core.ui.UI.success") as mock_success,
            patch("ldm_core.handlers.base.BaseHandler.read_meta", return_value={}),
        ):
            self.manager.snapshot.cmd_restore("test")
            mock_success.assert_any_call("Snapshot integrity verified.")

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    def test_cmd_restore_integrity_failure(self, mock_paths, mock_detect):
        mock_detect.return_value = self.test_dir
        (self.test_dir / "snapshots").mkdir(exist_ok=True)

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "state": self.test_dir / "osgi" / "state",
        }

        snap_dir = self.test_dir / "snapshots" / "20260512_120000"
        snap_dir.mkdir(parents=True)
        (snap_dir / "files.tar.gz").touch()
        (snap_dir / "files.tar.gz.sha256").write_text("wrong-sha")
        (snap_dir / "meta").touch()

        self.manager.args.latest = True
        self.manager.args.verify = True
        self.manager.args.list = False

        with (
            patch("ldm_core.utils.calculate_sha256", return_value="actual-sha"),
            patch("ldm_core.ui.UI.die", side_effect=SystemExit) as mock_die,
            patch("ldm_core.handlers.base.BaseHandler.read_meta", return_value={}),
        ):
            with self.assertRaises(SystemExit):
                self.manager.snapshot.cmd_restore("test")
            self.assertTrue(mock_die.called)
