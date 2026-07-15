import os
import unittest
from pathlib import Path

from ldm_core.utils import _DRY_RUN_VFS, reset_dry_run_vfs, safe_write_text


class TestDryRunVfsLeak(unittest.TestCase):
    def setUp(self):
        # Ensure we are starting from a clean state and LDM_DRY_RUN is set
        reset_dry_run_vfs()
        self.old_dry_run = os.environ.get("LDM_DRY_RUN")
        os.environ["LDM_DRY_RUN"] = "true"

    def tearDown(self):
        if self.old_dry_run is not None:
            os.environ["LDM_DRY_RUN"] = self.old_dry_run
        else:
            os.environ.pop("LDM_DRY_RUN", None)
        reset_dry_run_vfs()

    def test_vfs_write_in_dry_run(self):
        # GIVEN a dry run file write
        test_path = Path("/tmp/fake_vfs_test_path_1234.txt")
        safe_write_text(test_path, "test_vfs_data")

        # THEN the internal _DRY_RUN_VFS should contain it
        self.assertIn(str(test_path.resolve()), _DRY_RUN_VFS)
        self.assertEqual(_DRY_RUN_VFS[str(test_path.resolve())], "test_vfs_data")

    def test_vfs_state_is_isolated_and_empty_here(self):
        # GIVEN that the previous test ran
        test_path = Path("/tmp/fake_vfs_test_path_1234.txt")

        # THEN the current test should see a completely clean _DRY_RUN_VFS
        # because the clear_dry_run_vfs autouse fixture runs between tests.
        self.assertNotIn(str(test_path.resolve()), _DRY_RUN_VFS)
        self.assertEqual(len(_DRY_RUN_VFS), 0)
