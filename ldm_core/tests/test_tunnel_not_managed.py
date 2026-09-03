"""LDM resolves an lfr-tunnel client; it does not install one (LDM-#1576).

LDM used to fetch an unsigned binary from a GitHub release, chmod +x it and
run it -- `download_file` verifies neither checksum nor signature. That
download -> chmod -> execute sequence is what endpoint protection objects to,
and moving the destination would not have changed it.

The binary is never executed by these tests. `.agents/skills/testing-and-ci`
prohibits it: a real invocation risks SentinelOne quarantining the binary and
the surrounding toolchain. Every probe is mocked at the seam.
"""

import platform
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.share import ShareService
from ldm_core.tests.test_share import MockManager

BIN = "lfr-tunnel.exe" if platform.system().lower() == "windows" else "lfr-tunnel"


class _Base(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.manager.args.auto_install_lfr_tunnel = False
        self.manager.non_interactive = True
        self.manager.dry_run = False  # type: ignore[attr-defined]
        self.manager.config.get_global_config = MagicMock(  # type: ignore[method-assign]
            return_value={}
        )
        self.service = ShareService(self.manager)
        self.home = Path("/fake/home")


class TestNoDownloadRemains(unittest.TestCase):
    def test_share_no_longer_imports_the_downloader(self):
        """The fetch is gone, not merely unreachable behind a flag."""
        from ldm_core.handlers import share

        self.assertFalse(
            hasattr(share, "download_file"),
            "share.py still imports download_file; the fetch path is not gone",
        )

    def test_no_release_url_is_referenced_in_executable_code(self):
        import inspect

        from ldm_core.handlers import share

        src = inspect.getsource(share)
        code = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("#")
        )
        self.assertNotIn("releases/latest/download", code)


class TestResolutionOrder(_Base):
    """Doctor and share must agree, and existing installs must keep working."""

    def _resolve(self, present, env=None, config=None):
        """present: paths whose _get_installed_version answers a version."""
        present = {str(p) for p in present}

        def fake_version(path):
            return "1.48.12" if str(path) in present else None

        which = next((p for p in present if "/usr/local/bin" in str(p)), None)
        with patch.object(self.service, "_get_installed_version", fake_version):
            with patch(
                "ldm_core.handlers.share.get_actual_home", return_value=self.home
            ):
                with patch("shutil.which", return_value=which):
                    with patch.dict("os.environ", env or {}, clear=True):
                        self.manager.config.get_global_config = MagicMock(  # type: ignore[method-assign]
                            return_value=config or {}
                        )
                        return self.service._resolve_existing_binary()

    def test_the_whitelisted_location_is_found(self):
        want = self.home / "liferay" / "lfr-tunnel" / BIN
        with patch.object(Path, "exists", lambda _self: True):
            self.assertEqual(self._resolve([want]), want)

    def test_a_legacy_install_still_resolves(self):
        """Existing ~/.ldm/bin setups must not break."""
        want = self.home / ".ldm" / "bin" / BIN
        with patch.object(Path, "exists", lambda _self: True):
            self.assertEqual(self._resolve([want]), want)

    def test_the_whitelisted_location_wins_over_the_legacy_one(self):
        white = self.home / "liferay" / "lfr-tunnel" / BIN
        legacy = self.home / ".ldm" / "bin" / BIN
        with patch.object(Path, "exists", lambda _self: True):
            self.assertEqual(self._resolve([white, legacy]), white)

    def test_path_wins_over_both_install_locations(self):
        """A client the user installed themselves is their deliberate choice."""
        on_path = Path("/usr/local/bin") / BIN
        legacy = self.home / ".ldm" / "bin" / BIN
        with patch.object(Path, "exists", lambda _self: True):
            self.assertEqual(self._resolve([on_path, legacy]), on_path)

    def test_nothing_installed_resolves_to_none(self):
        with patch.object(Path, "exists", lambda _self: False):
            self.assertIsNone(self._resolve([]))


class TestNotFoundIsExplained(_Base):
    """No client and nothing configured: explain, do not prompt."""

    def _ensure(self):
        with patch.object(self.service, "_get_installed_version", return_value=None):
            with patch(
                "ldm_core.handlers.share.get_actual_home", return_value=self.home
            ):
                with patch.object(
                    self.service, "_resolve_existing_binary", return_value=None
                ):
                    with self.assertRaises(SystemExit) as ctx:
                        self.service._ensure_binary()
                    return ctx.exception

    def test_it_exits_with_the_infrastructure_code(self):
        self.assertEqual(self._ensure().code, 3)

    def test_it_never_prompts_when_there_is_nothing_to_authorise(self):
        """A question with no action behind it is not a question."""
        self.manager.non_interactive = False
        with patch("ldm_core.handlers.share.UI.confirm") as confirm:
            self._ensure()
        confirm.assert_not_called()

    def test_the_guidance_names_the_whitelisted_dir_and_the_docker_provider(self):
        with patch("ldm_core.handlers.share.UI.die", side_effect=SystemExit(3)) as die:
            with patch.object(
                self.service, "_get_installed_version", return_value=None
            ):
                with patch(
                    "ldm_core.handlers.share.get_actual_home", return_value=self.home
                ):
                    with self.assertRaises(SystemExit):
                        self.service._ensure_binary()
        tip = die.call_args.kwargs.get("tip", "")
        self.assertIn("liferay", tip)
        self.assertIn("lfr-tunnel-docker", tip)
        self.assertIn("lfr_tunnel_bin", tip)


class TestAutoInstallFlagIsDeprecatedNotBroken(_Base):
    def test_the_flag_still_parses_on_every_subcommand_that_declared_it(self):
        from ldm_core.cli import get_parser

        parser, _subparsers = get_parser()
        for argv in (
            ["run", ".", "--auto-install-lfr-tunnel"],
            ["import", "x", "--auto-install-lfr-tunnel"],
            ["share", "start", ".", "--auto-install-lfr-tunnel"],
        ):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertTrue(args.auto_install_lfr_tunnel)

    def test_it_warns_that_auto_install_is_gone_when_nothing_can_install(self):
        self.manager.args.auto_install_lfr_tunnel = True
        with patch("ldm_core.handlers.share.UI.warning") as warn:
            with patch.object(
                self.service, "_get_installed_version", return_value=None
            ):
                with patch(
                    "ldm_core.handlers.share.get_actual_home", return_value=self.home
                ):
                    with patch.object(
                        self.service, "_resolve_existing_binary", return_value=None
                    ):
                        with self.assertRaises(SystemExit):
                            self.service._ensure_binary()
        self.assertTrue(warn.called, "the deprecated flag warned about nothing")
        self.assertIn("no longer downloads", warn.call_args[0][0])


class TestCustomInstallerSurvives(_Base):
    """The user's own command is their choice; only LDM's fetch was removed."""

    def test_the_configured_command_is_run_and_the_result_used(self):
        self.manager.args.auto_install_lfr_tunnel = True
        self.manager.config.get_global_config = MagicMock(  # type: ignore[method-assign]
            return_value={"lfr_tunnel_install_cmd": "brew install lfr-tunnel"}
        )
        installed = Path("/opt/homebrew/bin") / BIN

        versions = {str(installed): "1.48.12"}
        with patch(
            "ldm_core.handlers.share.run_command", return_value=MagicMock()
        ) as run:
            with patch.object(
                self.service,
                "_get_installed_version",
                lambda p: versions.get(str(p)),
            ):
                with patch(
                    "ldm_core.handlers.share.get_actual_home", return_value=self.home
                ):
                    # absent on the first look, present after the command:
                    # a single return_value would let _get_binary_path find it
                    # and return before the installer ever ran.
                    with patch.object(
                        self.service,
                        "_resolve_existing_binary",
                        side_effect=[None, installed],
                    ):
                        got = self.service._ensure_binary()

        self.assertEqual(got, installed)
        self.assertEqual(run.call_args[0][0], ["brew", "install", "lfr-tunnel"])


if __name__ == "__main__":
    unittest.main()
