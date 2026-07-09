import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.utils import FileLock, load_global_config_safe, save_global_config_safe


class TestConfigSafe(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config_path = Path(self.test_dir) / ".ldmrc"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.test_dir)

    def test_load_non_existent(self):
        """Verify load_global_config_safe returns empty dict for non-existent files."""
        res = load_global_config_safe(self.config_path)
        self.assertEqual(res, {})

    def test_load_save_success(self):
        """Verify load and save roundtrip works successfully."""
        data = {"defaults": {"tag": "2026.q1.7-lts"}, "ngrok_authtoken": "test-token"}
        res_save = save_global_config_safe(self.config_path, data)
        self.assertTrue(res_save)
        self.assertTrue(self.config_path.exists())

        res_load = load_global_config_safe(self.config_path)
        self.assertEqual(res_load, data)

    def test_load_malformed_json_warning(self):
        """Verify malformed JSON triggers detailed warning diagnostics."""
        # Write trailing comma malformed JSON
        self.config_path.write_text('{"defaults": {"tag": "2026"},}', encoding="utf-8")

        with patch("ldm_core.ui.UI.warning") as mock_warn:
            res = load_global_config_safe(self.config_path)
            self.assertEqual(res, {})
            mock_warn.assert_called()
            # Verify details are logged in warning
            warn_msg = mock_warn.call_args[0][0]
            self.assertIn("contains invalid JSON syntax", warn_msg)
            self.assertIn("line 1", warn_msg)

    def test_save_lock_acquisition_failure(self):
        """Verify save fails gracefully when lock is held by another process/object."""
        lock_file = self.config_path.with_suffix(self.config_path.suffix + ".lock")
        # Acquire lock manually first
        another_lock = FileLock(lock_file)
        another_lock.acquire()

        with patch("ldm_core.ui.UI.warning") as mock_warn:
            data = {"tag": "val"}
            res = save_global_config_safe(self.config_path, data)
            self.assertFalse(res)
            mock_warn.assert_called()
            self.assertIn(
                "Failed to write configuration file", mock_warn.call_args[0][0]
            )

        another_lock.release()
