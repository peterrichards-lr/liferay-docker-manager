import unittest
from ldm_core.cli import get_parser


class TestCLILogic(unittest.TestCase):
    def setUp(self):
        self.parser, _ = get_parser()

    def test_intermixed_flags_prune(self):
        # Test: ldm prune -y
        args = self.parser.parse_args(["prune", "-y"])
        self.assertEqual(args.command, "prune")
        self.assertTrue(args.non_interactive)

    def test_intermixed_flags_run(self):
        # Test: ldm run demo -v
        args = self.parser.parse_args(["run", "demo", "-v"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.project, "demo")
        self.assertTrue(args.verbose)

    def test_global_flags_before_command(self):
        # Test: ldm -y -v prune
        args = self.parser.parse_args(["-y", "-v", "prune"])
        self.assertEqual(args.command, "prune")
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


if __name__ == "__main__":
    unittest.main()
