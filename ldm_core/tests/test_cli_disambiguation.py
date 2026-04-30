import unittest
from unittest.mock import MagicMock, patch

from ldm_core.cli import main


class TestCLIDisambiguation(unittest.TestCase):
    """
    Verifies that the CLI entrypoint correctly disambiguates between
    Project Names and Service Names (e.g. 'ldm logs liferay').
    """

    @patch("ldm_core.cli.LiferayManager")
    @patch("ldm_core.cli.get_parser")
    @patch("ldm_core.cli.check_for_updates")
    @patch("sys.exit")
    def test_logs_service_disambiguation(
        self, mock_exit, mock_update, mock_get_parser, mock_manager_class
    ):
        # Setup mock parser to return 'logs liferay'
        # Argparse incorrectly puts 'liferay' in project positional arg
        mock_args = MagicMock()
        mock_args.command = "logs"
        mock_args.project = "liferay"
        mock_args.service = []  # empty list for logs nargs='*'
        mock_args.all = False
        mock_args.infra = False
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

        # KEY: detect_project_path must return None for 'liferay' to prove it's NOT a project
        mock_manager.detect_project_path.side_effect = [
            None,  # First call (disambiguation check for 'liferay')
            None,  # Second call (p_name detection for docker_required)
        ]

        # Run main
        with patch("sys.argv", ["ldm", "logs", "liferay"]):
            main()

        # VERIFICATION:
        # The logic should have shifted 'liferay' to the service list
        mock_manager.cmd_logs.assert_called_once()
        args, _kwargs = mock_manager.cmd_logs.call_args
        # Positional arg 1 (p_name) should be None
        self.assertIsNone(args[0])
        # Positional arg 2 (service) should be ['liferay']
        self.assertEqual(args[1], ["liferay"])


if __name__ == "__main__":
    unittest.main()
