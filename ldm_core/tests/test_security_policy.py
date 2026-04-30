import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestSecurityPolicy(unittest.TestCase):
    @patch("ldm_core.cli.platform.system")
    @patch("os.geteuid")
    @patch("sys.exit")
    @patch("ldm_core.cli.get_parser")
    @patch("ldm_core.cli.UI")
    def test_no_sudo_guard_triggers_for_root(
        self, mock_ui, mock_get_parser, mock_exit, mock_geteuid, mock_system
    ):
        # Setup mocks
        mock_system.return_value = "Linux"
        mock_geteuid.return_value = 0  # Root

        # Mock the parser and arguments
        mock_parser = MagicMock()
        mock_subparsers = MagicMock()
        mock_get_parser.return_value = (mock_parser, mock_subparsers)

        mock_args = MagicMock()
        mock_args.command = "run"
        mock_parser.parse_args.return_value = mock_args

        # We need to mock SCRIPT_DIR and Path.exists for the allow_root check
        with (
            patch("ldm_core.cli.SCRIPT_DIR", Path("/tmp")),
            patch("pathlib.Path.exists", return_value=False),
            patch.dict(os.environ, {"LDM_ALLOW_ROOT": "false"}),
            patch("ldm_core.cli.LiferayManager") as mock_manager_cls,
        ):
            # Setup mock manager instance
            mock_manager = mock_manager_cls.return_value
            mock_manager.detect_project_path.return_value = None

            # Make mock_exit raise SystemExit to simulate real exit behavior
            mock_exit.side_effect = SystemExit(1)

            from ldm_core.cli import main

            with self.assertRaises(SystemExit):
                main()

            # Verify exit was called with 1
            mock_exit.assert_called_with(1)
            # Verify UI error was called
            mock_ui.error.assert_called_with(
                "Security Risk: Do not run LDM with 'sudo'."
            )

    @patch("ldm_core.cli.platform.system")
    @patch("os.geteuid")
    @patch("sys.exit")
    @patch("ldm_core.cli.get_parser")
    def test_sudo_guard_allows_standard_user(
        self, mock_get_parser, mock_exit, mock_geteuid, mock_system
    ):
        # Setup mocks
        mock_system.return_value = "Linux"
        mock_geteuid.return_value = 1000  # Standard User

        # Mock the parser and arguments
        mock_parser = MagicMock()
        mock_subparsers = MagicMock()
        mock_get_parser.return_value = (mock_parser, mock_subparsers)

        mock_args = MagicMock()
        mock_args.command = "run"
        mock_parser.parse_args.return_value = mock_args

        # Mock LiferayManager to avoid real execution
        with patch("ldm_core.cli.LiferayManager") as mock_manager_cls:
            mock_manager = mock_manager_cls.return_value
            mock_manager.detect_project_path.return_value = None
            mock_manager.check_docker.return_value = True

            from ldm_core.cli import main

            try:
                main()
            except Exception:
                # Expecting error because we didn't mock the 'cmds' dict execution fully
                pass

            # Verify exit was NOT called with 1
            # In standard user mode, it proceeds past the guard
            for call in mock_exit.call_args_list:
                self.assertNotEqual(call[0][0], 1)

    def test_redaction_consistency(self):
        from ldm_core.ui import UI

        secret_text = (
            "Connecting with MYSQL_PASSWORD=mysecret123 and --password=anothersecret"
        )
        redacted = UI.redact(secret_text)
        self.assertIn("PASSWORD=[REDACTED]", redacted)
        self.assertIn("--password=[REDACTED]", redacted)
        self.assertNotIn("mysecret123", redacted)
        self.assertNotIn("anothersecret", redacted)


if __name__ == "__main__":
    unittest.main()
