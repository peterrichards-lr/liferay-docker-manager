import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.defaults import CONVENTION_DEFAULTS, DefaultsManager


class TestDefaultsManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

        # Override paths for testing
        self.global_path = self.root / "global.json"
        self.user_path = self.root / "user.json"

        # Mock get_actual_home to avoid polluting actual user dir
        with patch("ldm_core.defaults.get_actual_home", return_value=self.root):
            self.manager = DefaultsManager()
            self.manager.global_path = self.global_path
            self.manager.user_path = self.user_path
            # Re-init dicts since paths changed
            self.manager.global_defaults = {}
            self.manager.user_defaults = {}

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_get_convention_defaults(self):
        # Should return convention default if not overridden
        self.assertEqual(self.manager.get("db_type"), CONVENTION_DEFAULTS["db_type"])

    def test_set_user_default(self):
        self.manager.set_user_default("db_type", "mysql")
        self.assertEqual(self.manager.get("db_type"), "mysql")
        self.assertTrue(self.user_path.exists())
        self.assertIn("mysql", self.user_path.read_text())

    def test_set_global_default(self):
        self.manager.set_global_default("port", "9090")
        self.assertEqual(self.manager.get("port"), "9090")
        self.assertTrue(self.global_path.exists())

    def test_cascading_priority(self):
        self.manager.set_global_default("port", "9090")
        self.manager.set_user_default("port", "8081")
        # User default should override global
        self.assertEqual(self.manager.get("port"), "8081")

    def test_remove_user_default(self):
        self.manager.set_user_default("tag", "2024.q1.4-lts")
        self.assertEqual(self.manager.get("tag"), "2024.q1.4-lts")
        self.manager.remove_user_default("tag")
        self.assertEqual(self.manager.get("tag"), CONVENTION_DEFAULTS["tag"])


if __name__ == "__main__":
    unittest.main()
