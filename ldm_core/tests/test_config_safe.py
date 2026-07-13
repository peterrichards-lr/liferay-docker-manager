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

    @patch("sys.stdout.isatty")
    @patch("ldm_core.utils.get_actual_home")
    @patch("ldm_core.ui.UI.heading")
    @patch("builtins.print")
    def test_upgrade_banner_one_time(
        self, mock_print, mock_heading, mock_home, mock_isatty
    ):
        """Verify the upgrade banner displays once and writes the version back."""
        import os
        import sys

        mock_isatty.return_value = True
        orig_argv = sys.argv
        sys.argv = ["ldm"]
        orig_env = os.environ.copy()
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]
        try:
            mock_home.return_value = Path(self.test_dir)
            config_path = Path(self.test_dir) / ".ldmrc"

            from ldm_core.cli import check_and_display_upgrade_banner
            from ldm_core.constants import VERSION

            check_and_display_upgrade_banner()

            self.assertTrue(mock_heading.called)
            self.assertTrue(mock_print.called)

            from ldm_core.utils import load_global_config_safe

            saved_data = load_global_config_safe(config_path)
            self.assertEqual(saved_data.get("last_run_version"), VERSION)

            mock_heading.reset_mock()
            mock_print.reset_mock()

            check_and_display_upgrade_banner()

            self.assertFalse(mock_heading.called)
            self.assertFalse(mock_print.called)
        finally:
            sys.argv = orig_argv
            os.environ.clear()
            os.environ.update(orig_env)

    @patch("sys.stdout.isatty")
    @patch("ldm_core.utils.get_actual_home")
    @patch("ldm_core.ui.UI.heading")
    @patch("builtins.print")
    def test_upgrade_banner_non_tty(
        self, mock_print, mock_heading, mock_home, mock_isatty
    ):
        """Verify the upgrade banner does not display on non-TTY streams."""
        import os
        import sys

        mock_isatty.return_value = False
        orig_argv = sys.argv
        sys.argv = ["ldm"]
        orig_env = os.environ.copy()
        if "PYTEST_CURRENT_TEST" in os.environ:
            del os.environ["PYTEST_CURRENT_TEST"]
        try:
            mock_home.return_value = Path(self.test_dir)
            from ldm_core.cli import check_and_display_upgrade_banner

            check_and_display_upgrade_banner()
            self.assertFalse(mock_heading.called)
            self.assertFalse(mock_print.called)
        finally:
            sys.argv = orig_argv
            os.environ.clear()
            os.environ.update(orig_env)
