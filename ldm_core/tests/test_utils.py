import unittest
from ldm_core.utils import version_to_tuple, verify_executable_checksum


class TestUtils(unittest.TestCase):
    def test_verify_executable_checksum(self):
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


if __name__ == "__main__":
    unittest.main()
