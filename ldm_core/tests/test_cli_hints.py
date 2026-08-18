import io
import sys
import unittest
from unittest.mock import patch

from ldm_core.ui import UI


class TestCLIHints(unittest.TestCase):
    def setUp(self):
        UI.QUIET_MODE = False
        UI.NO_COLOR = True
        UI.NO_UNICODE = True

    def test_ui_hint_output(self):
        captured_output = io.StringIO()
        with patch.object(sys, "stdout", captured_output):
            UI.hint("Run 'ldm status' for environment status.")
        output = captured_output.getvalue()
        self.assertIn("Next step: Run 'ldm status' for environment status.", output)

    def test_ui_hint_quiet_mode_suppression(self):
        UI.QUIET_MODE = True
        captured_output = io.StringIO()
        with patch.object(sys, "stdout", captured_output):
            UI.hint("This hint should be suppressed.")
        output = captured_output.getvalue()
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
