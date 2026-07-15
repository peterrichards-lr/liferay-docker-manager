import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, MagicMock, mock_open, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.snapshot import SnapshotService


class MockSnapshotManager(BaseHandler):
    def __init__(self):
        from argparse import Namespace

        self.args = Namespace(
            database_mode=None,
            search_mode=None,
            ssl=None,
            lean=False,
            tunnel_managed_cors=False,
        )
        self.verbose = False
        self.non_interactive = True
        self.snapshot = SnapshotService(self)
        self.composer = MagicMock()
        self.composer.is_using_named_volumes.return_value = False
        self.runtime = MagicMock()
        self.defaults = MagicMock()
        self.defaults.get = MagicMock(return_value="isolated")

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

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("builtins.print")
    def test_cmd_snapshots_with_elements(self, mock_print, mock_detect):
        mock_detect.return_value = self.test_dir

        # Setup backup dirs
        backups_dir = self.test_dir / "snapshots"
        backups_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot 1: All resources included
        snap1 = backups_dir / "2026-06-26T12-00-00Z"
        snap1.mkdir()

        # Snapshot 2: No resources, missing metadata keys (should assume false)
        snap2 = backups_dir / "2026-06-26T11-00-00Z"
        snap2.mkdir()

        def mock_read_meta_side_effect(path):
            if "2026-06-26T12-00-00Z" in str(path):
                return {
                    "name": "Full Backup",
                    "includes_database": "true",
                    "includes_volume_assets": "true",
                    "includes_client_extensions": "true",
                    "includes_osgi_modules": "true",
                }
            return {
                "name": "Empty Backup",
            }

        with patch.object(
            MockSnapshotManager, "read_meta", side_effect=mock_read_meta_side_effect
        ):
            backups = self.manager.snapshot.cmd_snapshots()
            self.assertEqual(len(backups), 2)

        printed_args = [call[0][0] for call in mock_print.call_args_list]

        # Check first printed snapshot (Full Backup) has [DB,VOL,CX,MOD]
        self.assertTrue(
            any(
                "Full Backup" in line and "[DB,VOL,CX,MOD]" in line
                for line in printed_args
            )
        )
        # Check second printed snapshot (Empty Backup) does not print any resource tags
        self.assertTrue(
            any(
                "Empty Backup" in line
                and not any(p in line for p in ["DB", "VOL", "CX", "MOD"])
                for line in printed_args
            )
        )

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

        self.manager.args.delete = None
        self.manager.args.keep_last = None
        self.manager.args.older_than = None

        with (
            patch("tarfile.open"),
            patch("ldm_core.handlers.base.BaseHandler.write_meta") as mock_write,
            patch("ldm_core.utils.calculate_sha256", return_value="dummy-sha"),
        ):
            self.manager.snapshot.cmd_snapshot("proj")
            mock_write.assert_called_once()
            written_meta = mock_write.call_args[0][1]
            self.assertEqual(written_meta["includes_database"], "false")
            self.assertEqual(written_meta["includes_volume_assets"], "false")
            self.assertEqual(written_meta["includes_client_extensions"], "false")
            self.assertEqual(written_meta["includes_osgi_modules"], "false")

        snap_dirs = [d for d in (self.test_dir / "snapshots").iterdir() if d.is_dir()]
        self.assertTrue(len(snap_dirs) >= 1)

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.verify_runtime_environment")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    @patch("ldm_core.utils.reclaim_volume_permissions")
    def test_cmd_snapshot_host_name_ssl_overrides(
        self, mock_reclaim, mock_run, mock_verify, mock_paths, mock_meta, mock_detect
    ):
        mock_detect.return_value = self.test_dir
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
        mock_meta.return_value = {"tag": "2026.q1.4", "db_type": "postgresql"}

        # Simulate host_name and ssl CLI overrides
        self.manager.args.host_name = "custom.domain"
        self.manager.args.ssl = True
        self.manager.args.delete = None
        self.manager.args.keep_last = None
        self.manager.args.older_than = None

        try:
            with (
                patch("tarfile.open"),
                patch("ldm_core.handlers.base.BaseHandler.write_meta") as mock_write,
                patch("ldm_core.utils.calculate_sha256", return_value="dummy-sha"),
            ):
                self.manager.snapshot.cmd_snapshot("proj")
                mock_write.assert_called_once()
                written_meta = mock_write.call_args[0][1]
                self.assertEqual(written_meta["host_name"], "custom.domain")
                self.assertEqual(written_meta["ssl"], "true")
        finally:
            self.manager.args.host_name = None
            self.manager.args.ssl = None

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

    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    @patch("time.sleep")
    def test_wipe_db_postgres_retries(self, mock_sleep, mock_sub_run, mock_file_open):
        import subprocess

        mock_err = subprocess.CalledProcessError(
            1, ["cmd"], stderr=b"starting up database"
        )
        mock_success_res = MagicMock()
        mock_success_res.returncode = 0

        mock_sub_run.side_effect = [
            mock_success_res,  # baseline dump
            mock_err,  # wipe retry 1
            mock_err,  # wipe retry 2
            mock_err,  # wipe retry 3
            mock_success_res,  # wipe retry 4
            mock_success_res,  # import
        ]

        self.manager.snapshot._execute_orchestrated_db_restore(
            "db-container", "postgresql", "sql-file", {}, {"host_name": "localhost"}
        )
        self.assertEqual(mock_sub_run.call_count, 6)
        self.assertEqual(mock_sleep.call_count, 3)

    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    @patch("time.sleep")
    def test_wipe_db_postgres_non_fatal_sql_error(
        self, mock_sleep, mock_sub_run, mock_file_open
    ):
        import subprocess

        mock_err = subprocess.CalledProcessError(
            1, ["cmd"], stderr=b"relation public.some_table already exists"
        )
        mock_success_res = MagicMock()
        mock_success_res.returncode = 0

        mock_sub_run.side_effect = [
            mock_success_res,  # baseline dump
            mock_err,  # wipe (non-fatal error)
            mock_success_res,  # import
        ]

        self.manager.snapshot._execute_orchestrated_db_restore(
            "db-container", "postgresql", "sql-file", {}, {"host_name": "localhost"}
        )
        self.assertEqual(mock_sub_run.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 0)

    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    @patch("platform.system", return_value="Darwin")
    def test_execute_orchestrated_db_restore_success(
        self, mock_system, mock_sub_run, mock_file_open
    ):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_sub_run.return_value = mock_res

        self.manager.snapshot._execute_orchestrated_db_restore(
            "db-container", "postgresql", "sql-file", {}, {"host_name": "my-local-host"}
        )

        import_call = mock_sub_run.call_args_list[-1]
        self.assertEqual(
            import_call.args[0],
            [
                "docker",
                "exec",
                "-i",
                "db-container",
                "psql",
                "-U",
                "lportal",
                "-d",
                "lportal",
                "-v",
                "ON_ERROR_STOP=1",
            ],
        )
        self.assertIsNotNone(import_call.kwargs.get("stdin"))
        self.assertIsNone(import_call.kwargs.get("shell"))

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    @patch("time.sleep")
    def test_execute_orchestrated_db_restore_failure_retries(
        self, mock_sleep, mock_sub_run, mock_file_open, mock_stat, mock_exists
    ):
        import subprocess

        mock_exists.return_value = True
        mock_stat_val = MagicMock()
        mock_stat_val.st_size = 100
        mock_stat.return_value = mock_stat_val

        mock_success = MagicMock()
        mock_success.returncode = 0
        mock_err = subprocess.CalledProcessError(1, ["cmd"], stderr=b"broken pipe")

        mock_sub_run.side_effect = [
            mock_success,  # baseline dump
            mock_success,  # wipe 1
            mock_err,  # import 1
            mock_success,  # wipe 2
            mock_err,  # import 2
            mock_success,  # wipe 3
            mock_err,  # import 3
            mock_success,  # wipe rollback
            mock_success,  # import rollback
        ]

        with self.assertRaises(SystemExit):
            self.manager.snapshot._execute_orchestrated_db_restore(
                "db-container", "postgresql", "sql-file", {}, {}
            )
        self.assertEqual(mock_sub_run.call_count, 9)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    def test_cmd_restore_smart_store_detection_simple(self, mock_paths, mock_detect):
        mock_detect.return_value = self.test_dir
        (self.test_dir / "snapshots").mkdir(exist_ok=True)

        data_dir = self.test_dir / "data"
        data_dir.mkdir(exist_ok=True)

        doclib = data_dir / "document_library"
        doclib.mkdir()
        comp_dir = doclib / "20116"
        comp_dir.mkdir()
        folder_dir = comp_dir / "12345"
        folder_dir.mkdir()

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "state": self.test_dir / "osgi" / "state",
            "data": data_dir,
        }

        snap_dir = self.test_dir / "snapshots" / "20260512_120000"
        snap_dir.mkdir(parents=True)
        (snap_dir / "volume.tgz").touch()
        (snap_dir / "meta").touch()

        self.manager.args.latest = True
        self.manager.args.verify = True
        self.manager.args.list = False
        self.manager.args.backup_dir = None

        with (
            patch.object(self.manager.snapshot, "_hydrate_named_volumes"),
            patch("ldm_core.handlers.base.BaseHandler.read_meta", return_value={}),
            patch("ldm_core.handlers.base.BaseHandler.write_meta") as mock_write_meta,
        ):
            self.manager.snapshot.cmd_restore("test")
            mock_write_meta.assert_called_with(
                self.test_dir,
                {
                    "dl_store_impl": "com.liferay.portal.store.file.system.FileSystemStore"
                },
            )

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    def test_cmd_restore_smart_store_detection_advanced(self, mock_paths, mock_detect):
        mock_detect.return_value = self.test_dir
        (self.test_dir / "snapshots").mkdir(exist_ok=True)

        data_dir = self.test_dir / "data"
        data_dir.mkdir(exist_ok=True)

        doclib = data_dir / "document_library"
        doclib.mkdir()
        comp_dir = doclib / "20116"
        comp_dir.mkdir()
        folder_dir = comp_dir / "12345"
        folder_dir.mkdir()
        grandkid = folder_dir / "67890"
        grandkid.mkdir()

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "state": self.test_dir / "osgi" / "state",
            "data": data_dir,
        }

        snap_dir = self.test_dir / "snapshots" / "20260512_120000"
        snap_dir.mkdir(parents=True)
        (snap_dir / "volume.tgz").touch()
        (snap_dir / "meta").touch()

        self.manager.args.latest = True
        self.manager.args.verify = True
        self.manager.args.list = False
        self.manager.args.backup_dir = None

        with (
            patch.object(self.manager.snapshot, "_hydrate_named_volumes"),
            patch("ldm_core.handlers.base.BaseHandler.read_meta", return_value={}),
            patch("ldm_core.handlers.base.BaseHandler.write_meta") as mock_write_meta,
        ):
            self.manager.snapshot.cmd_restore("test")
            mock_write_meta.assert_called_with(
                self.test_dir,
                {
                    "dl_store_impl": "com.liferay.portal.store.file.system.AdvancedFileSystemStore"
                },
            )

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.snapshot.SnapshotService._list_backups")
    @patch("ldm_core.handlers.snapshot.SnapshotService.cmd_snapshot")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.write_meta")
    @patch("ldm_core.utils.calculate_sha256")
    @patch("tarfile.open")
    def test_cmd_package_success(
        self,
        mock_tar_open,
        mock_calc_sha,
        mock_write_meta,
        mock_read_meta,
        mock_cmd_snapshot,
        mock_list_backups,
        mock_paths,
        mock_detect,
    ):
        mock_detect.return_value = self.test_dir
        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
        }

        # Mock snapshots list
        snap_dir = self.test_dir / "snapshots" / "20260512_120000"
        mock_list_backups.return_value = [{"path": snap_dir}]

        mock_read_meta.return_value = {
            "tag": "2026.q1.4-lts",
            "db_type": "postgresql",
        }
        mock_calc_sha.return_value = "dummy-sha-value"

        # Ensure directory structures mock behaves nicely
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "meta").touch()

        self.manager.args.non_interactive = True

        # Call command
        self.manager.snapshot.cmd_package(
            project_id="test",
            output_dir=str(self.test_dir),
            repo="my-owner/my-repo",
            use_latest=True,
        )

        mock_write_meta.assert_called_with(
            snap_dir,
            {
                "tag": "2026.q1.4-lts",
                "db_type": "postgresql",
                "github_repository": "my-owner/my-repo",
            },
        )

        # Verify package artifact created
        proj_name = self.test_dir.name
        sha_file = self.test_dir / f"{proj_name}.ldmp.sha256"
        self.assertTrue(sha_file.exists())
        self.assertEqual(
            sha_file.read_text().strip(), f"dummy-sha-value  {proj_name}.ldmp"
        )

    @patch("builtins.open", new_callable=mock_open)
    @patch("subprocess.run")
    @patch("platform.system", return_value="Darwin")
    def test_execute_orchestrated_db_restore_space_in_container_name(
        self, mock_system, mock_sub_run, mock_file_open
    ):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_sub_run.return_value = mock_res

        self.manager.snapshot._execute_orchestrated_db_restore(
            "zukunft digital-db",
            "postgresql",
            "sql-file",
            {},
            {"host_name": "my-local-host"},
        )

        import_call = mock_sub_run.call_args_list[-1]
        self.assertEqual(
            import_call.args[0],
            [
                "docker",
                "exec",
                "-i",
                "zukunft digital-db",
                "psql",
                "-U",
                "lportal",
                "-d",
                "lportal",
                "-v",
                "ON_ERROR_STOP=1",
            ],
        )

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    def test_cmd_restore_hypersonic_success(self, mock_paths, mock_detect):
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
            patch(
                "ldm_core.handlers.base.BaseHandler.read_meta",
                return_value={"db_type": "hypersonic"},
            ),
            patch.object(self.manager.runtime, "cmd_stop") as mock_stop,
            patch.object(
                self.manager.snapshot, "_execute_orchestrated_db_restore"
            ) as mock_db_restore,
        ):
            self.manager.snapshot.cmd_restore("test")
            mock_success.assert_any_call(
                "  + Hypersonic database restored successfully (file-based)."
            )
            # Verify we bypassed stopping Liferay and executing DB restore since it is file-based
            mock_stop.assert_not_called()
            mock_db_restore.assert_not_called()

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.snapshot.SnapshotService._list_backups")
    @patch("ldm_core.handlers.snapshot.SnapshotService.cmd_snapshot")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.write_meta")
    @patch("ldm_core.utils.calculate_sha256")
    @patch("tarfile.open")
    def test_cmd_package_snapshot_specific(
        self,
        mock_tar_open,
        mock_calc_sha,
        mock_write_meta,
        mock_read_meta,
        mock_cmd_snapshot,
        mock_list_backups,
        mock_paths,
        mock_detect,
    ):
        mock_detect.return_value = self.test_dir
        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
        }

        # Mock snapshots list
        snap_dir = self.test_dir / "snapshots" / "my-custom-snapshot"
        mock_list_backups.return_value = [
            {"name": "my-custom-snapshot", "path": snap_dir}
        ]

        mock_read_meta.return_value = {
            "tag": "2026.q1.4-lts",
            "db_type": "postgresql",
        }
        mock_calc_sha.return_value = "dummy-sha-value"

        # Ensure directory structures mock behaves nicely
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "meta").touch()

        self.manager.args.non_interactive = True

        # Call command targeting the specific snapshot name
        self.manager.snapshot.cmd_package(
            project_id="test",
            output_dir=str(self.test_dir),
            repo="my-owner/my-repo",
            snapshot="my-custom-snapshot",
        )

        mock_write_meta.assert_called_with(
            snap_dir,
            {
                "tag": "2026.q1.4-lts",
                "db_type": "postgresql",
                "github_repository": "my-owner/my-repo",
            },
        )
        # Check that we did not run a new snapshot command
        mock_cmd_snapshot.assert_not_called()

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.snapshot.SnapshotService._list_backups")
    @patch("ldm_core.ui.UI.die", side_effect=SystemExit)
    def test_cmd_package_snapshot_missing(
        self,
        mock_die,
        mock_list_backups,
        mock_paths,
        mock_detect,
    ):
        mock_detect.return_value = self.test_dir
        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
        }

        # Mock empty snapshots list
        mock_list_backups.return_value = []

        with self.assertRaises(SystemExit):
            self.manager.snapshot.cmd_package(
                project_id="test",
                output_dir=str(self.test_dir),
                snapshot="missing-snapshot",
            )
        mock_die.assert_called_once_with(
            f"Snapshot 'missing-snapshot' not found for project '{self.test_dir.name}'."
        )

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.snapshot.SnapshotService._list_backups")
    @patch("ldm_core.handlers.snapshot.SnapshotService.cmd_snapshot")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.write_meta")
    @patch("ldm_core.utils.calculate_sha256")
    @patch("tarfile.open")
    def test_cmd_package_creates_output_dir(
        self,
        mock_tar_open,
        mock_calc_sha,
        mock_write_meta,
        mock_read_meta,
        mock_cmd_snapshot,
        mock_list_backups,
        mock_paths,
        mock_detect,
    ):
        mock_detect.return_value = self.test_dir
        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
        }

        snap_dir = self.test_dir / "snapshots" / "20260512_120000"
        mock_list_backups.return_value = [{"path": snap_dir}]

        mock_read_meta.return_value = {
            "tag": "2026.q1.4-lts",
            "db_type": "postgresql",
        }
        mock_calc_sha.return_value = "dummy-sha-value"

        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "meta").touch()

        self.manager.args.non_interactive = True

        # Output directory does not exist yet
        non_existent_output = self.test_dir / "new_dist_dir"
        self.assertFalse(non_existent_output.exists())

        # Call command
        self.manager.snapshot.cmd_package(
            project_id="test",
            output_dir=str(non_existent_output),
            repo="my-owner/my-repo",
            use_latest=True,
        )

        # Verify that output directory was created dynamically
        self.assertTrue(non_existent_output.exists())
        proj_name = self.test_dir.name
        self.assertTrue((non_existent_output / f"{proj_name}.ldmp.sha256").exists())

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.verify_runtime_environment")
    @patch("ldm_core.utils.reclaim_volume_permissions")
    def test_cmd_snapshot_component_lists(
        self, mock_reclaim, mock_verify, mock_paths, mock_detect
    ):
        mock_detect.return_value = self.test_dir

        # Setup mock project files & directories
        (self.test_dir / "snapshots").mkdir(exist_ok=True)
        (self.test_dir / "osgi" / "state").mkdir(parents=True, exist_ok=True)
        (self.test_dir / "docker-compose.yml").touch()

        ce_dir = self.test_dir / "client-extensions"
        ce_dir.mkdir()
        (ce_dir / "test-ce.zip").touch()

        deploy_dir = self.test_dir / "deploy"
        deploy_dir.mkdir()
        (deploy_dir / "test-mod.jar").touch()

        modules_dir = self.test_dir / "modules"
        modules_dir.mkdir()
        (modules_dir / "another-mod.war").touch()

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "state": self.test_dir / "osgi" / "state",
            "ce_dir": ce_dir,
            "deploy": deploy_dir,
            "modules": modules_dir,
        }

        self.manager.args.delete = None
        self.manager.args.keep_last = None
        self.manager.args.older_than = None

        with (
            patch("tarfile.open"),
            patch.object(
                self.manager, "run_command", return_value="liferay\ndb\ntunnel\n"
            ),
            patch("ldm_core.handlers.base.BaseHandler.write_meta") as mock_write_meta,
            patch("ldm_core.utils.calculate_sha256", return_value="dummy-sha"),
        ):
            self.manager.snapshot.cmd_snapshot("proj")
            mock_write_meta.assert_called_once()
            written_meta = mock_write_meta.call_args[0][1]

            # Assert standard inclusion flags
            self.assertEqual(written_meta["includes_client_extensions"], "true")
            self.assertEqual(written_meta["includes_osgi_modules"], "true")

            # Assert lists are correctly extracted and sorted
            self.assertEqual(written_meta["client_extensions"], "test-ce.zip")
            self.assertEqual(
                written_meta["osgi_modules"], "another-mod.war,test-mod.jar"
            )
            self.assertEqual(written_meta["active_services"], "db,liferay,tunnel")

    @patch("ldm_core.handlers.runtime.UI.die", side_effect=SystemExit(1))
    @patch("shutil.disk_usage")
    def test_extract_snapshot_low_disk_space_fails(self, mock_disk_usage, mock_die):
        # 100 bytes free space
        from collections import namedtuple

        Usage = namedtuple("Usage", "total used free")
        mock_disk_usage.return_value = Usage(1000, 900, 100)

        paths = {
            "root": Path(self.test_dir),
        }
        # Create a dummy tar archive of size 500 bytes
        archive = Path(self.test_dir) / "dummy.tgz"
        archive.write_bytes(b"0" * 500)

        with self.assertRaises(SystemExit):
            self.manager.snapshot._extract_snapshot_archive(archive, paths)

        mock_die.assert_called_once()
        self.assertIn("Insufficient disk space", mock_die.call_args[0][0])

    @patch("ldm_core.handlers.runtime.UI.die", side_effect=SystemExit(1))
    @patch("shutil.disk_usage")
    @patch("tarfile.open")
    def test_extract_snapshot_sufficient_disk_space_succeeds(
        self, mock_tar_open, mock_disk_usage, mock_die
    ):
        # ample free space
        from collections import namedtuple

        Usage = namedtuple("Usage", "total used free")
        mock_disk_usage.return_value = Usage(10**10, 10**9, 10**9)

        paths = {
            "root": Path(self.test_dir),
        }
        archive = Path(self.test_dir) / "dummy.tgz"
        archive.write_bytes(b"0" * 50)

        self.manager.snapshot._extract_snapshot_archive(archive, paths)
        mock_die.assert_not_called()
        mock_tar_open.assert_called_once_with(archive, "r:gz")

    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_restore_volume_hash_match_skips(self, mock_detect, mock_paths):
        mock_detect.return_value = self.test_dir
        (self.test_dir / "snapshots").mkdir(exist_ok=True)

        target_data = self.test_dir / "data"
        target_data.mkdir(parents=True, exist_ok=True)
        # Create a mock file in data to show it's not empty
        (target_data / "some_file.txt").touch()

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "data": target_data,
        }

        snap_dir = self.test_dir / "snapshots" / "20260512_120000"
        snap_dir.mkdir(parents=True, exist_ok=True)

        volume_tgz = snap_dir / "volume.tgz"
        volume_tgz.write_bytes(b"dummy tgz content")

        # Calculate expected hash
        from ldm_core.utils import calculate_sha256

        expected_hash = calculate_sha256(volume_tgz)

        # Write matching hash file in target data
        (target_data / ".ldm_volume.sha256").write_text(expected_hash)

        # Set latest flag
        self.manager.args.latest = True
        self.manager.args.verify = True
        self.manager.args.list = False
        self.manager.args.backup_dir = None

        with (
            patch.object(self.manager, "run_command") as mock_run,
            patch.object(self.manager.runtime, "cmd_reset"),
            patch.object(self.manager.runtime, "cmd_run"),
            patch(
                "ldm_core.handlers.base.BaseHandler.read_meta",
                return_value={"container_name": "test-c"},
            ),
            patch("ldm_core.ui.UI.info") as mock_info,
        ):
            # Run restore
            self.manager.snapshot.cmd_restore("test")

            # Since the hash matches and folder contains files, it should skip extraction
            # Check that run_command was not called with tar -xzf
            tar_calls = [
                call for call in mock_run.call_args_list if "tar" in call[0][0]
            ]
            self.assertEqual(len(tar_calls), 0)
            mock_info.assert_any_call(
                "  + Volume archive unchanged (hash matched). Skipping extraction."
            )

    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_restore_volume_hash_mismatch_extracts(self, mock_detect, mock_paths):
        mock_detect.return_value = self.test_dir
        (self.test_dir / "snapshots").mkdir(exist_ok=True)

        target_data = self.test_dir / "data"
        target_data.mkdir(parents=True, exist_ok=True)
        (target_data / "some_file.txt").touch()

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "data": target_data,
        }

        snap_dir = self.test_dir / "snapshots" / "20260512_120000"
        snap_dir.mkdir(parents=True, exist_ok=True)

        volume_tgz = snap_dir / "volume.tgz"
        volume_tgz.write_bytes(b"dummy tgz content")

        # Write mismatching hash file in target data
        hash_file = target_data / ".ldm_volume.sha256"
        hash_file.write_text("mismatch-hash")

        # Set latest flag
        self.manager.args.latest = True
        self.manager.args.verify = True
        self.manager.args.list = False
        self.manager.args.backup_dir = None

        with (
            patch.object(self.manager, "run_command") as mock_run,
            patch.object(self.manager.runtime, "cmd_reset"),
            patch.object(self.manager.runtime, "cmd_run"),
            patch(
                "ldm_core.handlers.base.BaseHandler.read_meta",
                return_value={"container_name": "test-c"},
            ),
            patch("ldm_core.ui.UI.info"),
        ):
            # Run restore
            self.manager.snapshot.cmd_restore("test")

            # Since the hash mismatches, it should run extraction
            tar_calls = [
                call for call in mock_run.call_args_list if "tar" in call[0][0]
            ]
            self.assertEqual(len(tar_calls), 1)

            # It should also write the correct new hash to hash_file
            from ldm_core.utils import calculate_sha256

            expected_hash = calculate_sha256(volume_tgz)
            self.assertEqual(hash_file.read_text().strip(), expected_hash)

    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    def test_cmd_restore_version_tag_rollback(self, mock_detect, mock_paths):
        mock_detect.return_value = self.test_dir
        (self.test_dir / "snapshots").mkdir(exist_ok=True)

        data_dir = self.test_dir / "data"
        data_dir.mkdir(exist_ok=True)

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "state": self.test_dir / "osgi" / "state",
            "data": data_dir,
        }

        snap_dir = self.test_dir / "snapshots" / "20260512_120000"
        snap_dir.mkdir(parents=True)
        (snap_dir / "volume.tgz").touch()
        (snap_dir / "meta").touch()

        self.manager.args.latest = True
        self.manager.args.verify = True
        self.manager.args.list = False
        self.manager.args.backup_dir = None

        # Set up current metadata tag and different tag in snapshot metadata
        current_meta = {"tag": "dxp-2026.q2.1"}
        snapshot_meta = {"tag": "dxp-2024.q4.1"}

        def mock_read_meta(path):
            if str(path).endswith("meta") and "20260512_120000" in str(path):
                return snapshot_meta
            return current_meta

        with (
            patch.object(self.manager.snapshot, "_hydrate_named_volumes"),
            patch.object(MockSnapshotManager, "read_meta", side_effect=mock_read_meta),
            patch.object(MockSnapshotManager, "write_meta") as mock_write_meta,
        ):
            self.manager.snapshot.cmd_restore("test")

            # Verify that cmd_run was triggered on self.manager.runtime to rebuild config
            self.manager.runtime.cmd_run.assert_called_with(
                project_id=self.test_dir.name,
                no_up=True,
                show_summary=False,
                is_restore=True,
                paths=mock_paths.return_value,
                project_meta=ANY,
            )

            # Verify write_meta updated tag in project_meta to the snapshot tag
            called_meta = mock_write_meta.call_args[0][1]
            self.assertEqual(called_meta["tag"], "dxp-2024.q4.1")
            self.assertEqual(called_meta["last_run_liferay_version"], "dxp-2024.q4.1")

    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.verify_runtime_environment")
    @patch("ldm_core.utils.reclaim_volume_permissions")
    def test_cmd_snapshot_includes_ldm_directory(
        self, mock_reclaim, mock_verify, mock_paths, mock_detect
    ):
        mock_detect.return_value = self.test_dir
        (self.test_dir / "snapshots").mkdir(exist_ok=True)
        ldm_dir = self.test_dir / ".ldm"
        ldm_dir.mkdir()
        (ldm_dir / "fragment-overrides.json").touch()

        mock_paths.return_value = {
            "root": self.test_dir,
            "backups": self.test_dir / "snapshots",
            "state": self.test_dir / "osgi" / "state",
        }

        self.manager.args.delete = None
        self.manager.args.keep_last = None
        self.manager.args.older_than = None

        with (
            patch("tarfile.open") as mock_tar_open,
            patch.object(self.manager, "run_command", return_value="liferay\n"),
            patch("ldm_core.handlers.base.BaseHandler.write_meta"),
        ):
            mock_tar = mock_tar_open.return_value.__enter__.return_value
            self.manager.snapshot.cmd_snapshot("proj")

            # Assert .ldm was added to the tarball
            mock_tar.add.assert_any_call(ldm_dir, arcname=".ldm")

    def test_sync_volume_success(self):
        """Verify _sync_volume returns True when run_command succeeds."""
        with patch.object(
            self.manager, "run_command", return_value="success output"
        ) as mock_run:
            with tempfile.TemporaryDirectory() as tmp_dir:
                res = self.manager.snapshot._sync_volume(tmp_dir, "my-vol", "to_volume")
                self.assertTrue(res)
                mock_run.assert_called()

    def test_sync_volume_failure(self):
        """Verify _sync_volume returns False and logs warning when run_command returns None (failure)."""
        with patch.object(self.manager, "run_command", return_value=None):
            with tempfile.TemporaryDirectory() as tmp_dir:
                with patch("ldm_core.ui.UI.warning") as mock_warn:
                    res = self.manager.snapshot._sync_volume(
                        tmp_dir, "my-vol", "to_volume"
                    )
                    self.assertFalse(res)
                    mock_warn.assert_called_with(
                        "Failed to sync volume my-vol: Command execution returned error status."
                    )

    @patch("ldm_core.handlers.base.BaseHandler.write_meta")
    @patch("ldm_core.utils.calculate_sha256", return_value="dummy-sha")
    @patch("ldm_core.handlers.base.BaseHandler.verify_runtime_environment")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.utils.reclaim_volume_permissions")
    @patch("tarfile.open")
    def test_cmd_snapshot_db_dump_streaming(
        self,
        mock_tar,
        mock_reclaim,
        mock_detect,
        mock_read_meta,
        mock_paths,
        mock_verify,
        mock_sha,
        mock_write_meta,
    ):
        """Verify cmd_snapshot database dump executes streaming directly to file descriptor."""
        mock_detect.return_value = self.test_dir
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

        # Database type is postgresql, container is active
        mock_read_meta.return_value = {
            "db_type": "postgresql",
            "db_container_name": "proj-db",
        }

        # Mock run_command to simulate:
        # 1. docker ps for db container check -> returns "active"
        # 2. pg_dump execution -> writes mock sql output
        def mock_run_cmd(cmd, *args, **kwargs):
            if "docker" in cmd and "ps" in cmd:
                return "active"
            if "pg_dump" in cmd:
                stdout_file = kwargs.get("stdout_file")
                if stdout_file:
                    stdout_file.write(b"mock sql output")
                return ""
            return ""

        with patch.object(
            self.manager, "run_command", side_effect=mock_run_cmd
        ) as mock_run:
            self.manager.snapshot.cmd_snapshot("proj")

            pg_dump_call = None
            for call in mock_run.call_args_list:
                call_cmd = call[0][0]
                if "pg_dump" in call_cmd:
                    pg_dump_call = call
                    break

            self.assertIsNotNone(pg_dump_call)
            assert pg_dump_call is not None
            self.assertIn("stdout_file", pg_dump_call[1])
            self.assertIsNotNone(pg_dump_call[1]["stdout_file"])

    @patch("ldm_core.handlers.base.BaseHandler.write_meta")
    @patch("ldm_core.utils.calculate_sha256", return_value="dummy-sha")
    @patch("ldm_core.handlers.base.BaseHandler.verify_runtime_environment")
    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.base.BaseHandler.detect_project_path")
    @patch("ldm_core.utils.reclaim_volume_permissions")
    @patch("tarfile.open")
    def test_cmd_snapshot_db_dump_failure(
        self,
        mock_tar,
        mock_reclaim,
        mock_detect,
        mock_read_meta,
        mock_paths,
        mock_verify,
        mock_sha,
        mock_write_meta,
    ):
        """Verify cmd_snapshot calls UI.die on database dump execution failure."""
        mock_detect.return_value = self.test_dir
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

        mock_read_meta.return_value = {
            "db_type": "postgresql",
            "db_container_name": "proj-db",
        }

        # Simulate docker ps ok, but pg_dump failing/throwing exception
        def mock_run_cmd(cmd, *args, **kwargs):
            if "docker" in cmd and "ps" in cmd:
                return "active"
            if "pg_dump" in cmd:
                raise RuntimeError("Mock pg_dump failure")
            return ""

        with patch.object(self.manager, "run_command", side_effect=mock_run_cmd):
            with patch("ldm_core.ui.UI.die") as mock_die:
                self.manager.snapshot.cmd_snapshot("proj")
                mock_die.assert_called_once()
                self.assertEqual(mock_die.call_args[1].get("exit_code"), 3)

    @patch("time.time")
    @patch("time.sleep")
    def test_wait_for_search_restore_success(self, mock_sleep, mock_time):
        mock_time.side_effect = [100.0, 101.0]
        with patch.object(self.manager, "run_command", return_value='"stage":"DONE"'):
            res = self.manager.snapshot._wait_for_search_restore(
                "snap", "proj", timeout=10
            )
            self.assertTrue(res)

    @patch("time.time")
    @patch("time.sleep")
    def test_wait_for_search_restore_timeout(self, mock_sleep, mock_time):
        mock_time.side_effect = [100.0, 101.0, 102.0]
        with patch.object(self.manager, "run_command", return_value='"stage":"INDEX"'):
            res = self.manager.snapshot._wait_for_search_restore(
                "snap", "proj", timeout=1
            )
            self.assertFalse(res)
