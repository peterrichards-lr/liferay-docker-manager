import unittest
import unittest.mock

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

    @unittest.mock.patch("ldm_core.ui.sys.platform", "darwin")
    @unittest.mock.patch("ldm_core.ui.sys.stdout")
    @unittest.mock.patch("builtins.input", return_value="yes")
    def test_ask_unix_interactive(self, mock_input, mock_stdout):
        UI.NON_INTERACTIVE = False
        res = UI.ask("Continue?", default="Y")
        self.assertEqual(res, "yes")
        mock_stdout.write.assert_called()
        written = "".join([call.args[0] for call in mock_stdout.write.call_args_list])
        self.assertIn("❓", written)
        self.assertIn("Continue?", written)
        mock_input.assert_called_once_with()

    @unittest.mock.patch("ldm_core.ui.sys.platform", "win32")
    @unittest.mock.patch("builtins.input", return_value="no")
    def test_ask_windows_interactive(self, mock_input):
        UI.NON_INTERACTIVE = False
        res = UI.ask("Continue?", default="Y")
        self.assertEqual(res, "no")
        mock_input.assert_called_once()
        called_prompt = mock_input.call_args[0][0]
        self.assertNotIn("❓", called_prompt)
        self.assertIn("[?]", called_prompt)
        self.assertIn("Continue?", called_prompt)

    def test_ask_non_interactive(self):
        UI.NON_INTERACTIVE = True
        try:
            res = UI.ask("Continue?", default="Y")
            self.assertEqual(res, "Y")
        finally:
            UI.NON_INTERACTIVE = False

    @unittest.mock.patch("ldm_core.ui.sys.platform", "win32")
    @unittest.mock.patch("builtins.input", side_effect=KeyboardInterrupt)
    def test_ask_windows_abort(self, mock_input):
        UI.NON_INTERACTIVE = False
        with self.assertRaises(SystemExit) as cm:
            UI.ask("Continue?")
        self.assertEqual(cm.exception.code, 130)

    def test_no_color_strips_color(self):
        class MockStream:
            def __init__(self):
                self.written = []

            def write(self, s):
                self.written.append(s)

            def flush(self):
                pass

        UI.NO_COLOR = True
        try:
            stream = MockStream()
            UI._print("Test", color=UI.CYAN, file=stream)
            combined = "".join(stream.written)
            self.assertNotIn("\033", combined)
            self.assertIn("Test", combined)
        finally:
            UI.NO_COLOR = False

    def test_no_unicode_forces_ascii_replacements(self):
        class MockStream:
            def __init__(self):
                self.written = []

            def write(self, s):
                self.written.append(s)

            def flush(self):
                pass

        UI.NO_UNICODE = True
        try:
            stream = MockStream()
            UI._print("❌ Error ✅ OK", file=stream)
            combined = "".join(stream.written)
            self.assertIn("[X]", combined)
            self.assertIn("[OK]", combined)
            self.assertNotIn("❌", combined)
            self.assertNotIn("✅", combined)
        finally:
            UI.NO_UNICODE = False

    @unittest.mock.patch("ldm_core.ui.UI.ask")
    def test_confirm_yes_default(self, mock_ask):
        # Default True / "Y"
        mock_ask.return_value = "Y/n"
        self.assertTrue(UI.confirm("Proceed?", default=True))
        mock_ask.assert_called_with("Proceed?", "Y/n")

        mock_ask.return_value = "y"
        self.assertTrue(UI.confirm("Proceed?", default=True))

        mock_ask.return_value = "no"
        self.assertFalse(UI.confirm("Proceed?", default=True))

    @unittest.mock.patch("ldm_core.ui.UI.ask")
    def test_confirm_no_default(self, mock_ask):
        # Default False / "N"
        mock_ask.return_value = "y/N"
        self.assertFalse(UI.confirm("Proceed?", default=False))
        mock_ask.assert_called_with("Proceed?", "y/N")

        mock_ask.return_value = "yes"
        self.assertTrue(UI.confirm("Proceed?", default=False))

        mock_ask.return_value = "n"
        self.assertFalse(UI.confirm("Proceed?", default=False))

    @unittest.mock.patch("ldm_core.utils.get_actual_home")
    def test_trace_log_initialization(self, mock_home):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            UI.init_trace_log(["cmd", "arg1"])
            self.assertIsNotNone(UI._trace_handle)
            assert UI.TRACE_LOG_PATH is not None
            self.assertTrue(UI.TRACE_LOG_PATH.exists())
            assert UI._trace_handle is not None
            UI._trace_handle.close()
            UI._trace_handle = None

            content = UI.TRACE_LOG_PATH.read_text(encoding="utf-8")
            self.assertIn("--- LDM Trace Log Started at", content)
            self.assertIn("Command: cmd arg1", content)

    @unittest.mock.patch("ldm_core.utils.get_actual_home")
    def test_trace_log_writing(self, mock_home):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_home.return_value = Path(tmpdir)
            UI.init_trace_log(["cmd"])
            UI.trace("Test Trace Message")
            UI._print("Test Print Message", color=UI.CYAN)
            assert UI._trace_handle is not None
            UI._trace_handle.close()
            UI._trace_handle = None

            assert UI.TRACE_LOG_PATH is not None
            content = UI.TRACE_LOG_PATH.read_text(encoding="utf-8")
            self.assertIn("Test Trace Message", content)
            self.assertIn("Test Print Message", content)
            self.assertNotIn("\033", content)


if __name__ == "__main__":
    unittest.main()
