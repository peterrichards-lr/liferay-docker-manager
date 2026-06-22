import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ldm_core.cli import get_parser
from ldm_core.utils import (
    _DRY_RUN_VFS,
    read_meta,
    reclaim_volume_permissions,
    run_command,
    safe_copy,
    safe_extract,
    safe_mkdir,
    safe_move,
    safe_rmtree,
    safe_write_text,
    write_meta,
)


class TestDryRun(unittest.TestCase):
    def setUp(self):
        self.original_env = os.environ.get("LDM_DRY_RUN")
        os.environ["LDM_DRY_RUN"] = "true"
        _DRY_RUN_VFS.clear()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        if self.original_env is None:
            os.environ.pop("LDM_DRY_RUN", None)
        else:
            os.environ["LDM_DRY_RUN"] = self.original_env
        _DRY_RUN_VFS.clear()
        shutil.rmtree(self.tmp_dir)

    def test_cli_parser_dry_run_flag(self):
        parser, _ = get_parser()
        # Global dry-run flag before command
        args = parser.parse_args(["--dry-run", "run", "demo"])
        self.assertTrue(args.dry_run)

        # Global dry-run flag after command
        args = parser.parse_args(["run", "demo", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_run_command_mock_returns(self):
        # Verify run_command is intercepted and does not run actual docker process
        res = run_command(["docker", "info", "--format", "{{.MemTotal}}"])
        self.assertEqual(res, "17179869184")

        res_json = run_command(["docker", "info", "--format", "{{json .}}"])
        self.assertEqual(res_json, '{"MemTotal": 17179869184}')

        res_context = run_command(["docker", "context", "show"])
        self.assertEqual(res_context, "default")

        res_status = run_command(
            ["docker", "inspect", "-f", "{{.State.Status}}", "demo-container"]
        )
        self.assertEqual(res_status, "running")

    def test_safe_write_text_and_read_meta_vfs(self):
        meta_file = Path(self.tmp_dir) / "meta"
        meta_content = "container_name=demo-liferay\ntag=latest\ndb_type=postgresql\n"

        # Write text under dry-run (should write to VFS, not disk)
        safe_write_text(meta_file, meta_content)
        self.assertFalse(meta_file.exists())
        self.assertIn(str(meta_file.resolve()), _DRY_RUN_VFS)

        # Read meta (should read from VFS successfully)
        meta = read_meta(meta_file)
        self.assertEqual(meta.get("container_name"), "demo-liferay")
        self.assertEqual(meta.get("tag"), "latest")
        self.assertEqual(meta.get("db_type"), "postgresql")

    def test_safe_mkdir_bypass(self):
        target_dir = Path(self.tmp_dir) / "sub_dir"
        safe_mkdir(target_dir)
        self.assertFalse(target_dir.exists())

    def test_safe_copy_move_extract_rmtree_dry_run(self):
        src = Path(self.tmp_dir) / "src.txt"
        dst = Path(self.tmp_dir) / "dst.txt"

        # Test copy
        safe_copy(src, dst)
        self.assertFalse(dst.exists())

        # Test move
        safe_move(src, dst)
        self.assertFalse(dst.exists())

        # Test extract
        safe_extract(None, dst)
        self.assertFalse(dst.exists())

        # Test rmtree
        sub = Path(self.tmp_dir) / "sub"
        sub.mkdir()
        safe_rmtree(sub)
        self.assertTrue(sub.exists())
        sub.rmdir()

    def test_write_meta_dry_run(self):
        meta_path = Path(self.tmp_dir) / "project-meta"
        meta = {"project_name": "dry-run-demo", "tag": "2026.q1.4-lts"}

        write_meta(meta_path, meta)
        self.assertFalse(meta_path.exists())

        resolved_path = str(meta_path.resolve())
        self.assertIn(resolved_path, _DRY_RUN_VFS)
        content = _DRY_RUN_VFS[resolved_path]
        self.assertIn("project_name=dry-run-demo", content)
        self.assertIn("tag=2026.q1.4-lts", content)

    def test_reclaim_volume_permissions_dry_run(self):
        self.assertTrue(reclaim_volume_permissions("/some/path"))

    def test_handlers_early_exit_dry_run(self):
        from unittest.mock import MagicMock

        from ldm_core.handlers.snapshot import SnapshotService
        from ldm_core.handlers.workspace import WorkspaceService

        manager = MagicMock()
        manager.detect_project_path.return_value = Path(self.tmp_dir) / "demo-proj"
        manager.args.project = "demo-proj"
        manager.args.project_flag = None

        # Workspace import dry run
        ws = WorkspaceService(manager)
        proj = ws.cmd_import("https://github.com/peterrichards-lr/test-repo.git")
        self.assertEqual(proj, "demo-proj")

        # Snapshot & Restore dry run
        snap = SnapshotService(manager)
        snap.cmd_snapshot("demo-proj")
        snap.cmd_restore("demo-proj")

        # Verify no real operations occurred on manager or filesystem
        self.assertFalse((Path(self.tmp_dir) / "demo-proj").exists())


if __name__ == "__main__":
    unittest.main()
