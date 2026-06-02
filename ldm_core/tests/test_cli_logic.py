import unittest

from ldm_core.cli import get_parser


class TestCLILogic(unittest.TestCase):
    def setUp(self):
        self.parser, _ = get_parser()

    def test_intermixed_flags_prune(self):
        # Test: ldm prune -y (legacy alias -> system prune)
        args = self.parser.parse_args(["prune", "-y"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "prune")
        self.assertTrue(args.non_interactive)

    def test_intermixed_flags_run(self):
        # Test: ldm run demo -v
        args = self.parser.parse_args(["run", "demo", "-v"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.project, "demo")
        self.assertTrue(args.verbose)

    def test_global_flags_before_command(self):
        # Test: ldm -y -v prune (legacy alias -> system prune)
        args = self.parser.parse_args(["-y", "-v", "prune"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "prune")
        self.assertTrue(args.non_interactive)
        self.assertTrue(args.verbose)

    def test_status_project_arg(self):
        # Test: ldm status forge
        args = self.parser.parse_args(["status", "forge"])
        self.assertEqual(args.command, "status")
        self.assertEqual(args.project, "forge")

    def test_cli_disambiguation_logs(self):
        # Test: ldm logs liferay
        # Without a project specified, argparse puts 'liferay' in 'project'
        args = self.parser.parse_args(["logs", "liferay"])
        self.assertEqual(args.command, "logs")
        self.assertEqual(args.project, "liferay")
        self.assertEqual(args.service, [])

    def test_cli_disambiguation_stop(self):
        # Test: ldm stop db
        args = self.parser.parse_args(["stop", "db"])
        self.assertEqual(args.command, "stop")
        self.assertEqual(args.project, "db")
        self.assertIsNone(args.service)

    def test_version_flag(self):
        # argparse handles --version by exiting, so we test that it's defined
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["--version"])

    def test_new_cli_flags(self):
        # Test: ldm run --tag-latest --no-captcha demo
        args = self.parser.parse_args(["run", "--tag-latest", "--no-captcha", "demo"])
        self.assertTrue(args.tag_latest)
        self.assertTrue(args.no_captcha)
        self.assertEqual(args.project, "demo")

        # Test: ldm init-from source --tag-latest
        args = self.parser.parse_args(["init-from", "source_path", "--tag-latest"])
        self.assertTrue(args.tag_latest)
        self.assertEqual(args.source, "source_path")

        # Test: ldm infra-restart --search (legacy alias -> infra restart)
        args = self.parser.parse_args(["infra-restart", "--search"])
        self.assertEqual(args.command, "infra")
        self.assertEqual(args.subcommand, "restart")
        self.assertTrue(args.search)

    def test_logs_advanced_flags_parsing(self):
        # Test: ldm logs demo -n 50 -t --since 1h --until 10m
        args = self.parser.parse_args(
            ["logs", "demo", "-n", "50", "-t", "--since", "1h", "--until", "10m"]
        )
        self.assertEqual(args.command, "logs")
        self.assertEqual(args.project, "demo")
        self.assertEqual(args.tail, "50")
        self.assertTrue(args.timestamps)
        self.assertEqual(args.since, "1h")
        self.assertEqual(args.until, "10m")
        self.assertIsNone(args.instance)

    def test_logs_instance_flag(self):
        # Test: ldm logs demo liferay --instance 2
        args = self.parser.parse_args(["logs", "demo", "liferay", "--instance", "2"])
        self.assertEqual(args.command, "logs")
        self.assertEqual(args.project, "demo")
        self.assertEqual(args.instance, 2)

        # Test: short form -i 3
        args = self.parser.parse_args(["logs", "demo", "liferay", "-i", "3"])
        self.assertEqual(args.instance, 3)

        # Test: without --instance defaults to None
        args = self.parser.parse_args(["logs", "demo"])
        self.assertIsNone(args.instance)

    def test_feature_flag_parsing(self):
        # Test: ldm run demo --feature LPS-122920 dev beta
        args = self.parser.parse_args(
            ["run", "demo", "--feature", "LPS-122920", "dev", "beta"]
        )
        self.assertEqual(args.command, "run")
        self.assertEqual(args.project, "demo")
        self.assertEqual(args.feature, ["LPS-122920", "dev", "beta"])

    def test_piped_input_preserves_interactive(self):
        from unittest.mock import patch

        from ldm_core.manager import LiferayManager

        args = self.parser.parse_args(["run"])

        # Simulate piping input (isatty is False) but no CI flags
        with patch("sys.stdin.isatty", return_value=False):
            with patch("os.getenv", return_value=None):
                manager = LiferayManager(args)
                self.assertFalse(
                    manager.non_interactive,
                    "Piping standard input should not force non-interactive mode",
                )


if __name__ == "__main__":
    unittest.main()
