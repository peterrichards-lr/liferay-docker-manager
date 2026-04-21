import unittest
from unittest.mock import patch, MagicMock
from ldm_core.cli import main


class TestCLIEntrypoint(unittest.TestCase):
    @patch("ldm_core.cli.LiferayManager")
    @patch("ldm_core.cli.get_parser")
    @patch("ldm_core.cli.check_for_updates")
    @patch("sys.exit")
    def test_run_command_calls_detect_project_path_with_for_init(
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

        # Setup mock manager
        mock_manager = mock_manager_class.return_value
        mock_manager.check_docker.return_value = True
        mock_manager.detect_project_path.return_value = None  # Project doesn't exist

        # Run main
        with patch("sys.argv", ["ldm", "run", "new-project"]):
            main()

        # Verify detect_project_path was called with for_init=True
        mock_manager.detect_project_path.assert_called_once_with(
            "new-project", for_init=True
        )
        # Verify cmd_run was called
        mock_manager.cmd_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
