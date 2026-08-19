import io
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.workspace.guide import cmd_guide


class TestGuideCLI(unittest.TestCase):
    def setUp(self):
        self.manager = MagicMock()
        self.manager.args = MagicMock()

    def test_cmd_guide_non_interactive(self):
        """Verify cmd_guide outputs all 5 topics without prompting when non-interactive is True."""
        self.manager.args.non_interactive = True
        captured_output = io.StringIO()

        with patch("sys.stdout", captured_output):
            cmd_guide(self.manager)

        output = captured_output.getvalue()
        self.assertIn("LDM Developer Onboarding & Interactive Guide", output)
        self.assertIn("Quickstart Workflow", output)
        self.assertIn("LDM Conventions & Defaults", output)
        self.assertIn("Customizing Defaults", output)
        self.assertIn("Data Management & Snapshots", output)
        self.assertIn("Compute & Sharing", output)

    @patch("builtins.input", side_effect=["1", "Q"])
    def test_cmd_guide_interactive_navigation(self, mock_input):
        """Verify interactive menu choice selection and clean exit on 'Q'."""
        self.manager.args.non_interactive = False
        captured_output = io.StringIO()

        with (
            patch("sys.stdout", captured_output),
            patch("sys.stdin.isatty", return_value=True),
        ):
            cmd_guide(self.manager)

        output = captured_output.getvalue()
        self.assertIn("Quickstart Workflow", output)
        self.assertIn("Exiting LDM Onboarding Guide", output)


if __name__ == "__main__":
    unittest.main()
