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

    def test_version_flag(self):
        # argparse handles --version by exiting, so we test that it's defined
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["--version"])


if __name__ == "__main__":
    unittest.main()
