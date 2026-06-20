import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ldm_core.cli import get_parser
from ldm_core.utils import (
    _DRY_RUN_VFS,
    read_meta,
    run_command,
    safe_mkdir,
    safe_write_text,
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


if __name__ == "__main__":
    unittest.main()
