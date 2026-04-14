import unittest
from unittest.mock import patch
from pathlib import Path
from ldm_core.utils import version_to_tuple, verify_executable_checksum


class TestUtils(unittest.TestCase):
    @patch("sys.argv", ["ldm.py"])
    @patch("sys.frozen", False, create=True)
    def test_verify_executable_checksum_source(self):
        # When running as source (pytest), it should return "Source", True, VERSION
        status, ok, version = verify_executable_checksum("1.6.11")
        self.assertEqual(status, "Source")
        self.assertTrue(ok)
        self.assertEqual(version, "1.6.11")

    def test_version_to_tuple(self):
        self.assertEqual(version_to_tuple("1.5.4"), (1, 5, 4))
        self.assertEqual(version_to_tuple("v1.5.4"), (1, 5, 4))
        self.assertEqual(version_to_tuple("1.5"), (1, 5, 0))
        self.assertEqual(version_to_tuple("2"), (2, 0, 0))
        self.assertEqual(version_to_tuple(""), (0, 0, 0))
        self.assertEqual(version_to_tuple(None), (0, 0, 0))
        self.assertEqual(version_to_tuple("invalid"), (0, 0, 0))

    def test_version_comparison(self):
        self.assertTrue(version_to_tuple("1.5.5") > version_to_tuple("1.5.4"))
        self.assertTrue(version_to_tuple("1.6.0") > version_to_tuple("1.5.9"))
        self.assertTrue(version_to_tuple("2.0.0") > version_to_tuple("1.9.9"))
        self.assertFalse(version_to_tuple("1.5.4") > version_to_tuple("1.5.4"))
        self.assertFalse(version_to_tuple("1.4.9") > version_to_tuple("1.5.0"))

    def test_sanitize_id(self):
        from ldm_core.utils import sanitize_id

        self.assertEqual(sanitize_id("my-project"), "my-project")
        self.assertEqual(sanitize_id("project_123"), "project_123")
        self.assertEqual(sanitize_id("my project!"), "myproject")
        self.assertEqual(sanitize_id("path/to/../../etc/passwd"), "pathtoetcpasswd")
        self.assertEqual(sanitize_id("user; drop table users"), "userdroptableusers")
        self.assertEqual(sanitize_id(""), "")
        self.assertEqual(sanitize_id(None), None)

    @patch("ldm_core.utils.platform.system")
    @patch("ldm_core.utils.os.environ.get")
    def test_get_actual_home_case_insensitive(self, mock_env, mock_system):
        from ldm_core.utils import get_actual_home

        # Mock macOS with capitalized "Darwin"
        mock_system.return_value = "Darwin"
        mock_env.return_value = "tester"

        with patch.object(Path, "exists", return_value=True):
            home = get_actual_home()
            self.assertEqual(str(home), "/Users/tester")


if __name__ == "__main__":
    unittest.main()
