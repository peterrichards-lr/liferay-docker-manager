import unittest

from ldm_core.cli import get_parser


class TestCLILogic(unittest.TestCase):
    def setUp(self):
        self.parser, _ = get_parser()

    def test_system_alias_mappings(self):
        # Test direct top-level system subcommands expand via preprocess_args/legacy_map
        args = self.parser.parse_args(["roi"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "roi")

        args = self.parser.parse_args(["seeds"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "seeds")

        args = self.parser.parse_args(["relocate", "/tmp/ext"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "relocate")
        self.assertEqual(args.target, "/tmp/ext")

        args = self.parser.parse_args(["init-ci"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "init-ci")

    def test_package_resources_integrity(self):
        # Verify essential package resource files exist in ldm_core/resources
        from pathlib import Path

        import ldm_core

        res_dir = Path(ldm_core.__file__).parent / "resources"
        self.assertTrue((res_dir / "ldm_app_icon.jpg").exists())
        self.assertTrue((res_dir / "infra-compose.yml").exists())
        self.assertTrue((res_dir / "dashboard" / "index.html").exists())
        self.assertTrue(
            (res_dir / "common_baseline" / "portal-ext.properties").exists()
        )

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

    def test_fix_hosts_parsing(self):
        # Test: ldm fix-hosts (legacy alias -> system fix-hosts)
        args = self.parser.parse_args(["fix-hosts"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "fix-hosts")
        self.assertIsNone(args.host_name)

        # Test: ldm system fix-hosts
        args = self.parser.parse_args(["system", "fix-hosts"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "fix-hosts")
        self.assertIsNone(args.host_name)

        # Test: ldm system fix-hosts demo-project.local
        args = self.parser.parse_args(["system", "fix-hosts", "demo-project.local"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "fix-hosts")
        self.assertEqual(args.host_name, "demo-project.local")

    def test_share_flags_and_subcommands(self):
        # 1. Test: ldm run flags
        args = self.parser.parse_args(
            [
                "run",
                "demo",
                "--share",
                "--share-subdomain",
                "my-sub",
                "--share-provider",
                "ngrok",
                "--share-image",
                "custom-run-img",
            ]
        )
        self.assertEqual(args.command, "run")
        self.assertEqual(args.project, "demo")
        self.assertTrue(args.share)
        self.assertEqual(args.share_subdomain, "my-sub")
        self.assertEqual(args.share_provider, "ngrok")
        self.assertEqual(args.share_image, "custom-run-img")

        # 2. Test: ldm share start
        args = self.parser.parse_args(
            [
                "share",
                "start",
                "demo",
                "--subdomain",
                "custom-sub",
                "--ports",
                "8081",
                "--provider",
                "ngrok",
                "--image",
                "custom-share-img",
            ]
        )
        self.assertEqual(args.command, "share")
        self.assertEqual(args.subcommand, "start")
        self.assertEqual(args.project, "demo")
        self.assertEqual(args.subdomain, "custom-sub")
        self.assertEqual(args.ports, "8081")
        self.assertEqual(args.provider, "ngrok")
        self.assertEqual(args.image, "custom-share-img")

        # 3. Test: ldm share status
        args = self.parser.parse_args(["share", "status", "demo"])
        self.assertEqual(args.command, "share")
        self.assertEqual(args.subcommand, "status")
        self.assertEqual(args.project, "demo")

        # 4. Test: ldm share stop
        args = self.parser.parse_args(["share", "stop", "demo"])
        self.assertEqual(args.command, "share")
        self.assertEqual(args.subcommand, "stop")
        self.assertEqual(args.project, "demo")

    def test_flat_command_aliases(self):
        # Test ldm rebuild-properties translates to config rebuild-properties
        args = self.parser.parse_args(["rebuild-properties"])
        self.assertEqual(args.command, "config")
        self.assertEqual(args.subcommand, "rebuild-properties")

        # Test ldm config rebuild-properties behaves correctly
        args = self.parser.parse_args(["config", "rebuild-properties"])
        self.assertEqual(args.command, "config")
        self.assertEqual(args.subcommand, "rebuild-properties")

        # Test ldm revert-properties translates to config revert-properties
        args = self.parser.parse_args(["revert-properties"])
        self.assertEqual(args.command, "config")
        self.assertEqual(args.subcommand, "revert-properties")

        # Test ldm reset-properties translates to config reset-properties
        args = self.parser.parse_args(["reset-properties"])
        self.assertEqual(args.command, "config")
        self.assertEqual(args.subcommand, "reset-properties")

        # Test ldm rescue translates to system rescue
        args = self.parser.parse_args(["rescue"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "rescue")

        # Test ldm nuke translates to system nuke
        args = self.parser.parse_args(["nuke"])
        self.assertEqual(args.command, "system")
        self.assertEqual(args.subcommand, "nuke")


if __name__ == "__main__":
    unittest.main()
