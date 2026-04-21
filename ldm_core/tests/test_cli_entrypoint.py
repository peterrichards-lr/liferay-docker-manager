import unittest
from unittest.mock import patch, MagicMock
from ldm_core.cli import main


class TestCLIEntrypoint(unittest.TestCase):
    @patch("ldm_core.cli.LiferayManager")
    @patch("ldm_core.cli.get_parser")
    @patch("ldm_core.cli.check_for_updates")
    @patch("sys.exit")
    def test_run_command_delegates_to_manager(
        self, mock_exit, mock_update, mock_get_parser, mock_manager_class
    ):
        # Setup mock parser to return 'run' command
        mock_args = MagicMock()
        mock_args.command = "run"
        mock_args.project = "new-project"
        mock_args.project_flag = None
        mock_args.verbose = False
        mock_args.non_interactive = True

        mock_parser = MagicMock()
        mock_parser.parse_args.return_value = mock_args
        mock_get_parser.return_value = (mock_parser, MagicMock())

        # Setup mock update check
        mock_update.return_value = (None, None)

        # Setup mock manager
        mock_manager = mock_manager_class.return_value
        mock_manager.check_docker.return_value = True

        # Run main
        with patch("sys.argv", ["ldm", "run", "new-project"]):
            main()

        # Verify cmd_run was called with the project arg
        mock_manager.cmd_run.assert_called_once_with("new-project")

    @patch("ldm_core.cli.LiferayManager")
    @patch("ldm_core.cli.get_parser")
    @patch("ldm_core.cli.check_for_updates")
    @patch("threading.Thread")
    @patch("sys.exit")
    def test_completion_skips_update_check(
        self,
        mock_exit,
        mock_thread,
        mock_update,
        mock_get_parser,
        mock_manager_class,
    ):
        # Setup mock parser to return 'completion' command
        mock_args = MagicMock()
        mock_args.command = "completion"
        mock_args.shell = "zsh"
        mock_args.verbose = False
        mock_args.non_interactive = True

        mock_parser = MagicMock()
        mock_parser.parse_args.return_value = mock_args
        mock_get_parser.return_value = (mock_parser, MagicMock())

        # Setup mock manager
        mock_manager = mock_manager_class.return_value
        mock_manager.cmd_completion = MagicMock()

        # Run main
        with patch("sys.argv", ["ldm", "completion", "zsh"]):
            main()

        # Verify threading.Thread was NOT called (since update check is skipped)
        mock_thread.assert_not_called()
        # Verify cmd_completion WAS called
        mock_manager.cmd_completion.assert_called_once_with("zsh")

    @patch("ldm_core.cli.LiferayManager")
    @patch("ldm_core.cli.get_parser")
    @patch("ldm_core.cli.check_for_updates")
    @patch("threading.Thread")
    @patch("sys.exit")
    def test_run_starts_update_check(
        self,
        mock_exit,
        mock_thread,
        mock_update,
        mock_get_parser,
        mock_manager_class,
    ):
        # Setup mock parser to return 'run' command
        mock_args = MagicMock()
        mock_args.command = "run"
        mock_args.project = "demo"
        mock_args.verbose = False
        mock_args.non_interactive = True

        mock_parser = MagicMock()
        mock_parser.parse_args.return_value = mock_args
        mock_get_parser.return_value = (mock_parser, MagicMock())

        # Setup mock manager
        mock_manager = mock_manager_class.return_value
        mock_manager.check_docker.return_value = True
        mock_manager.cmd_run = MagicMock()

        # Run main
        with patch("sys.argv", ["ldm", "run", "demo"]):
            main()

        # Verify threading.Thread WAS called
        mock_thread.assert_called()


if __name__ == "__main__":
    unittest.main()
