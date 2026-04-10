import unittest
from pathlib import Path
from ldm_core.handlers.diagnostics import DiagnosticsHandler


class MockManager(DiagnosticsHandler):
    def __init__(self):
        self.verbose = False


class TestDiagnostics(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.test_file = Path("/tmp/test.properties")

    def tearDown(self):
        if self.test_file.exists():
            self.test_file.unlink()

    def test_validate_properties_valid(self):
        self.test_file.write_text("key1=value1\nkey2=value2")
        status, ok, errors = self.manager.validate_properties_file(self.test_file)
        self.assertTrue(ok)
        self.assertEqual(status, "Valid Structure")
        self.assertEqual(len(errors), 0)

    def test_validate_properties_duplicates(self):
        self.test_file.write_text("key1=value1\nkey1=value2\nkey2=v3")
        status, ok, errors = self.manager.validate_properties_file(self.test_file)
        self.assertEqual(ok, "warn")
        self.assertTrue(any("Duplicate key 'key1'" in e for e in errors))

    def test_validate_properties_broken_continuation(self):
        # Line ends in backslash but next line is empty
        self.test_file.write_text("key1=value1\\\n\nkey2=value2")
        status, ok, errors = self.manager.validate_properties_file(self.test_file)
        self.assertEqual(ok, "warn")
        self.assertTrue(any("Broken continuation" in e for e in errors))

    def test_validate_properties_orphaned_line(self):
        self.test_file.write_text("key1=value1\norphaned line here")
        status, ok, errors = self.manager.validate_properties_file(self.test_file)
        self.assertEqual(ok, "warn")
        self.assertTrue(any("Orphaned line" in e for e in errors))

    def test_validate_lcp_json_valid(self):
        lcp_file = Path("/tmp/LCP.json")
        lcp_file.write_text('{"id": "test-ext", "ports": [{"targetPort": 8080}]}')
        try:
            status, ok, errors = self.manager.validate_lcp_json(lcp_file)
            self.assertTrue(ok)
            self.assertEqual(status, "Valid Structure")
        finally:
            if lcp_file.exists():
                lcp_file.unlink()

    def test_validate_lcp_json_missing_id(self):
        lcp_file = Path("/tmp/LCP.json")
        lcp_file.write_text('{"ports": [{"targetPort": 8080}]}')
        try:
            status, ok, errors = self.manager.validate_lcp_json(lcp_file)
            self.assertEqual(ok, "warn")
            self.assertTrue(any("Missing mandatory 'id'" in e for e in errors))
        finally:
            if lcp_file.exists():
                lcp_file.unlink()

    def test_validate_lcp_json_invalid_port(self):
        lcp_file = Path("/tmp/LCP.json")
        lcp_file.write_text('{"id": "test", "ports": [{"noPort": 1}]}')
        try:
            status, ok, errors = self.manager.validate_lcp_json(lcp_file)
            self.assertEqual(ok, "warn")
            self.assertTrue(any("missing 'targetPort'" in e for e in errors))
        finally:
            if lcp_file.exists():
                lcp_file.unlink()


if __name__ == "__main__":
    unittest.main()
