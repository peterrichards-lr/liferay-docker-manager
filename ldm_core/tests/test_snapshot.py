import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from ldm_core.handlers.snapshot import SnapshotHandler


class MockSnapshotManager(SnapshotHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True


class TestSnapshotHandler(unittest.TestCase):
    def setUp(self):
        self.manager = MockSnapshotManager()

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_snapshots_empty(self, mock_detect):
        mock_detect.return_value = Path("/tmp/project")

        with patch.object(Path, "exists", return_value=False):
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
    @patch("ldm_core.utils.calculate_sha256")
    def test_cmd_snapshot_basic(
        self, mock_sha, mock_run, mock_verify, mock_paths, mock_meta, mock_detect
    ):
        mock_detect.return_value = Path("/tmp/proj")
        mock_paths.return_value = {
            "root": Path("/tmp/proj"),
            "backups": Path("/tmp/proj/snapshots"),
            "state": Path("/tmp/proj/osgi/state"),
            "data": Path("/tmp/proj/data"),
            "deploy": Path("/tmp/proj/deploy"),
            "files": Path("/tmp/proj/files"),
            "logs": Path("/tmp/proj/logs"),
            "configs": Path("/tmp/proj/osgi/configs"),
            "modules": Path("/tmp/proj/osgi/modules"),
            "compose": Path("/tmp/proj/docker-compose.yml"),
        }
        mock_meta.return_value = {"use_shared_search": "false"}
        mock_sha.return_value = "dummy-sha"

        with patch("tarfile.open"):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("pathlib.Path.mkdir"):
                    with patch("pathlib.Path.read_text", return_value=""):
                        with patch("pathlib.Path.write_text"):
                            with patch("ldm_core.handlers.base.BaseHandler.write_meta"):
                                self.manager.cmd_snapshot("proj")

        # Verify that SHA calculation was called, proving the core logic executed successfully
        self.assertTrue(mock_sha.called)


if __name__ == "__main__":
    unittest.main()
