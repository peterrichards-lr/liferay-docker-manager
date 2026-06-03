import unittest

from ldm_core.ui import UI


class TestUI(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(UI.format_duration(45), "45s")
        self.assertEqual(UI.format_duration(60), "1m 0s")
        self.assertEqual(UI.format_duration(90), "1m 30s")
        self.assertEqual(UI.format_duration(3600), "1h 0m 0s")
        self.assertEqual(UI.format_duration(3661), "1h 1m 1s")
        self.assertEqual(UI.format_duration(0), "0s")

    def test_redact_kv_patterns(self):
        self.assertEqual(
            UI.redact("MYSQL_PASSWORD=secret"), "MYSQL_PASSWORD=[REDACTED]"
        )
        self.assertEqual(
            UI.redact("LIFERAY_AUTH_TOKEN=abc123def"), "LIFERAY_AUTH_TOKEN=[REDACTED]"
        )
        self.assertEqual(
            UI.redact("MY_SECRET_KEY=verysecret"), "MY_SECRET_KEY=[REDACTED]"
        )
        self.assertEqual(UI.redact("USER_KEY=12345"), "USER_KEY=[REDACTED]")

    def test_redact_cli_flags(self):
        self.assertEqual(
            UI.redact("mysql -uroot -pPASSWORD123"), "mysql -uroot -p[REDACTED]"
        )
        self.assertEqual(
            UI.redact("ldm --password=secret run"), "ldm --password=[REDACTED] run"
        )

    def test_redact_no_match(self):
        # Normal text should remain untouched
        self.assertEqual(UI.redact("Starting Liferay..."), "Starting Liferay...")
        # Key without = shouldn't match (usually)
        self.assertEqual(UI.redact("This is my PASSWORD"), "This is my PASSWORD")
        # Empty or None
        self.assertEqual(UI.redact(""), "")
        self.assertEqual(UI.redact(None), None)

    def test_format_size(self):
        self.assertEqual(UI.format_size(512), "512.0 B")
        self.assertEqual(UI.format_size(1024), "1.0 KB")
        self.assertEqual(UI.format_size(1024 * 1024), "1.0 MB")
        self.assertEqual(UI.format_size(1024 * 1024 * 1024), "1.0 GB")

    def test_print_unicode_fallback(self):
        class MockASCIIStream:
            def __init__(self):
                self.encoding = "ascii"
                self.written = []

            def write(self, s):
                self.written.append(s)

            def flush(self):
                pass

        stream = MockASCIIStream()
        # "❌" cannot be encoded in ASCII, so it should trigger fallback and be replaced with "[X]"
        UI._print("❌ Test message", file=stream)

        # Check if the fallback replacement happened
        combined = "".join(stream.written)
        self.assertIn("[X]", combined)
        self.assertIn("Test message", combined)
        self.assertNotIn("❌", combined)


if __name__ == "__main__":
    unittest.main()
