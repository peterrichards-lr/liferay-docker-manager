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

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.utils.calculate_sha256")
    def test_cmd_snapshot_with_mysql(
        self, mock_sha, mock_paths, mock_meta, mock_detect
    ):
        mock_detect.return_value = Path("/tmp/mysql-proj")
        mock_paths.return_value = {
            "root": Path("/tmp/mysql-proj"),
            "backups": Path("/tmp/mysql-proj/snapshots"),
            "state": Path("/tmp/mysql-proj/osgi/state"),
            "data": Path("/tmp/mysql-proj/data"),
            "deploy": Path("/tmp/mysql-proj/deploy"),
            "files": Path("/tmp/mysql-proj/files"),
            "logs": Path("/tmp/mysql-proj/logs"),
            "configs": Path("/tmp/mysql-proj/osgi/configs"),
            "modules": Path("/tmp/mysql-proj/osgi/modules"),
            "compose": Path("/tmp/mysql-proj/docker-compose.yml"),
            "portal_log4j": Path("/tmp/mysql-proj/osgi/log4j"),
            "common": Path("/tmp/common"),
        }
        mock_meta.return_value = {
            "db_type": "mysql",
            "container_name": "mysql-proj",
            "use_shared_search": "false",
        }
        mock_sha.return_value = "dummy-sha"

        with (
            patch("tarfile.open"),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.read_text", return_value=""),
            patch("pathlib.Path.write_text") as mock_write,
            patch("ldm_core.handlers.base.BaseHandler.write_meta"),
            patch.object(self.manager, "verify_runtime_environment"),
            patch.object(self.manager, "run_command") as mock_run,
        ):
            # Sequence for run_command:
            # 1. docker ps (db check) -> returns running-id
            # 2. docker exec (mysqldump) -> returns SQL content
            mock_run.side_effect = ["running-db-id", "DUMP-SQL-CONTENT"]

            self.manager.cmd_snapshot("mysql-proj")

            # Verify that mysqldump was called
            found_dump = False
            for call in mock_run.call_args_list:
                cmd = call[0][0]
                if "mysqldump" in cmd:
                    found_dump = True
                    break
            self.assertTrue(
                found_dump,
                f"mysqldump was not called. Calls: {mock_run.call_args_list}",
            )

            # Verify SQL content was written
            mock_write.assert_any_call("DUMP-SQL-CONTENT")


if __name__ == "__main__":
    unittest.main()
