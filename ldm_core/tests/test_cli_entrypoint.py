import unittest
from unittest.mock import MagicMock, patch

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
        mock_manager.runtime.cmd_run.assert_called_once_with("new-project")

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
        # Setup mock parser to return 'completion' command (now under system namespace)
        mock_args = MagicMock()
        mock_args.command = "system"
        mock_args.subcommand = "completion"
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
        mock_manager.runtime.cmd_run = MagicMock()

        # Run main
        with patch("sys.argv", ["ldm", "run", "demo"]):
            main()

        # Verify threading.Thread WAS called
        mock_thread.assert_called()

    def test_cli_python_version_mismatch(self):
        import importlib

        import ldm_core.cli

        with (
            patch("sys.version_info", (3, 9, 0)),
            patch("sys.stderr.write") as mock_write,
            patch("sys.exit") as mock_exit,
        ):
            # Reload the module to trigger the early version check
            importlib.reload(ldm_core.cli)
            self.assertTrue(mock_write.called)
            self.assertIn("LDM requires Python 3.10", mock_write.call_args[0][0])
            mock_exit.assert_called_once_with(1)

        # Clean up: reload the module again under normal Python to avoid side effects
        importlib.reload(ldm_core.cli)

    @patch("ldm_core.cli.LiferayManager")
    @patch("ldm_core.cli.get_parser")
    @patch("ldm_core.cli.check_for_updates")
    @patch("sys.exit")
    def test_share_commands_delegate_to_share_service(
        self, mock_exit, mock_update, mock_get_parser, mock_manager_class
    ):
        mock_update.return_value = (None, None)
        mock_manager = mock_manager_class.return_value
        mock_manager.check_docker.return_value = True

        mock_parser = MagicMock()
        mock_get_parser.return_value = (mock_parser, MagicMock())

        # 1. Test 'share start'
        mock_args = MagicMock()
        mock_args.command = "share"
        mock_args.subcommand = "start"
        mock_args.project = "demo"
        mock_args.project_flag = None
        mock_args.subdomain = "my-sub"
        mock_args.ports = "8082"
        mock_args.provider = "ngrok"
        mock_args.image = None
        mock_args.verbose = False
        mock_args.non_interactive = True
        mock_parser.parse_args.return_value = mock_args

        with patch(
            "sys.argv",
            [
                "ldm",
                "share",
                "start",
                "demo",
                "--subdomain",
                "my-sub",
                "--ports",
                "8082",
                "--provider",
                "ngrok",
            ],
        ):
            main()
        mock_manager.share.cmd_start.assert_called_once_with(
            project_id="demo",
            subdomain="my-sub",
            ports="8082",
            provider="ngrok",
            image=None,
        )

        # 2. Test 'share status'
        mock_args = MagicMock()
        mock_args.command = "share"
        mock_args.subcommand = "status"
        mock_args.project = "demo"
        mock_args.project_flag = None
        mock_args.verbose = False
        mock_args.non_interactive = True
        mock_parser.parse_args.return_value = mock_args

        with patch("sys.argv", ["ldm", "share", "status", "demo"]):
            main()
        mock_manager.share.cmd_status.assert_called_once_with(project_id="demo")

        # 3. Test 'share stop'
        mock_args = MagicMock()
        mock_args.command = "share"
        mock_args.subcommand = "stop"
        mock_args.project = "demo"
        mock_args.project_flag = None
        mock_args.verbose = False
        mock_args.non_interactive = True
        mock_parser.parse_args.return_value = mock_args

        with patch("sys.argv", ["ldm", "share", "stop", "demo"]):
            main()
        mock_manager.share.cmd_stop.assert_called_once_with(project_id="demo")


if __name__ == "__main__":
    unittest.main()
