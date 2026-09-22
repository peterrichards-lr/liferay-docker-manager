"""LDM resolves an lfr-tunnel client; it does not install one (LDM-#1576).

LDM used to fetch an unsigned binary from a GitHub release, chmod +x it and
run it -- `download_file` verifies neither checksum nor signature. That
download -> chmod -> execute sequence is what endpoint protection objects to,
and moving the destination would not have changed it.

The binary is never executed by these tests. `.agents/skills/testing-and-ci`
prohibits it: a real invocation risks SentinelOne quarantining the binary and
the surrounding toolchain. Every probe is mocked at the seam.
"""

import os
import platform
import subprocess
import tempfile
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

    def test_a_legacy_install_is_no_longer_resolved(self):
        """LDM-#1883 deliberately ends the "~/.ldm/bin setups must not break"
        guarantee this test used to assert.

        Resolving that path meant EXECUTING it -- `_get_installed_version`
        runs each candidate to read its version -- so LDM was launching a
        binary it did not install and cannot vouch for, from a location its
        own comments call outside the EDR whitelist. The observed response
        removes the binary and the surrounding toolchain with it, `brew`
        included.

        Breaking this is the cost of the fix, and it is a real cost. It is
        accepted because the recovery is one line, and the refusal names it:
        set `LDM_LFR_TUNNEL_BIN`, or move the binary to the whitelisted
        location. A symlink from the legacy path to the approved binary still
        works -- see TestAnUnapprovedBinaryIsNeverExecuted.
        """
        want = self.home / ".ldm" / "bin" / BIN
        with patch.object(Path, "exists", lambda _self: True):
            self.assertIsNone(self._resolve([want]))

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


class TestLegacySymlinkIsResolvedBeforeExecution(_Base):
    """LDM-#1871: candidate 5 (~/.ldm/bin/lfr-tunnel) is outside the EDR
    whitelist. `_get_installed_version` executes whatever path it is given
    (`subprocess.run([str(bin_path), "-version"], ...)`), so if the legacy
    location is a symlink to the whitelisted binary, the *invoked* path must
    still be the resolved, whitelisted one -- not the symlink -- or EDR keying
    on the invoked path sees an execution from a non-whitelisted location
    regardless of which bytes actually run.

    Uses real files/symlinks on disk (not a mocked `Path.exists`/`.resolve()`)
    so the resolution is genuinely observed, not merely asserted from reading
    the implementation.
    """

    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.home = Path(self._tmpdir.name)

    def _resolve_with_only_legacy_symlink(self, real_bin, legacy_bin):
        """Runs `_resolve_existing_binary` with everything but the legacy
        (symlinked) candidate absent, recording every path actually probed.
        """
        probed = []

        def fake_version(path):
            probed.append(path)
            return "1.48.12" if path == real_bin.resolve() else None

        env = {"LDM_LFR_TUNNEL_BIN": "", "LFR_TUNNEL_BIN": ""}
        with patch.object(self.service, "_get_installed_version", fake_version):
            with patch(
                "ldm_core.handlers.share.get_actual_home", return_value=self.home
            ):
                with patch("shutil.which", return_value=None):
                    with patch.dict(os.environ, env, clear=True):
                        result = self.service._resolve_existing_binary()
        return result, probed

    def test_legacy_symlink_resolves_to_the_real_whitelisted_path(self):
        # LDM-#1883 changed what this may point AT. It used to stand in for
        # "somewhere InfoSec approved that isn't the notional default", but
        # resolving a legacy symlink to an arbitrary location still ends in
        # LDM executing a binary from outside the whitelist -- the hole #1871
        # left open. A legacy symlink is now honoured only when it resolves to
        # the whitelisted binary, which is what this sets up.
        #
        # What #1871 asserted, and what still matters here, is unchanged: the
        # RESOLVED path is what gets invoked, never the symlink.
        real_dir = self.home / "liferay" / "lfr-tunnel"
        real_dir.mkdir(parents=True)
        real_bin = real_dir / BIN
        real_bin.write_text("stand-in binary, never executed by this test\n")

        # Legacy location (candidate 5) is a symlink to it. Candidate 4 (the
        # notional whitelisted default under ~/liferay/lfr-tunnel) is left
        # absent so resolution falls through to the legacy candidate.
        legacy_dir = self.home / ".ldm" / "bin"
        legacy_dir.mkdir(parents=True)
        legacy_bin = legacy_dir / BIN
        legacy_bin.symlink_to(real_bin)

        result, probed = self._resolve_with_only_legacy_symlink(real_bin, legacy_bin)

        self.assertEqual(
            result,
            real_bin.resolve(),
            "resolution must return the symlink's real target, not the symlink itself",
        )
        self.assertIn(
            real_bin.resolve(),
            probed,
            "the resolved real path must be what gets probed/executed",
        )
        self.assertNotIn(
            legacy_bin,
            probed,
            "the unresolved symlink path must never be handed to "
            "_get_installed_version (which executes it)",
        )


class TestAnUnapprovedBinaryIsNeverExecuted(_Base):
    """LDM-#1883: LDM must not run a binary in order to decide whether it
    should be running it.

    LDM-#1871 made sure an approved binary is *invoked* by its approved path.
    It left the prior question open: `_get_installed_version` executes each
    candidate to read its version, so for the legacy `~/.ldm/bin` location
    that meant launching a binary LDM did not install and cannot vouch for.
    The observed endpoint-protection response removes the binary and the
    surrounding toolchain with it.

    Non-execution is observed at the seam that would spawn the process:
    `share.subprocess.run` is replaced by a recorder, so the argv LDM *would*
    have launched is captured exactly. `_get_installed_version` itself stays
    real, so the resolution path under test is the production one -- only the
    call that crosses the process boundary is stubbed.

    That recorder replaced a `chmod +x` stub these tests used to genuinely
    execute (LDM-#1898). Spawning a process named `lfr-tunnel` from a temp
    directory is exactly what `.agents/skills/testing-and-ci` prohibits and
    what the module docstring above already promised these tests never do;
    endpoint protection killed a developer session twice before anyone
    noticed the contradiction. Recording the argv is also strictly stronger
    than the stub was -- it captures the full invocation rather than `$0`.
    Every assertion below is unchanged.
    """

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.invoked: list[str] = []
        self.whitelisted = self.home / "liferay" / "lfr-tunnel" / BIN
        self.legacy = self.home / ".ldm" / "bin" / BIN

    def _make_binary(self, path):
        """A real file on disk, so `.exists()` and `.resolve()` are genuinely
        observed rather than mocked -- but never marked executable, and never
        spawned.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stand-in binary; never executed (LDM-#1898)\n")

    def _record(self, argv, **_kwargs):
        """Stands in for `subprocess.run`, recording what would have run."""
        self.invoked.append(str(argv[0]))
        return subprocess.CompletedProcess(argv, 0, "lfr-tunnel v1.48.12\n", "")

    def _resolve(self):
        env = {"LDM_LFR_TUNNEL_BIN": "", "LFR_TUNNEL_BIN": ""}
        with (
            patch("ldm_core.handlers.share.subprocess.run", self._record),
            patch("ldm_core.handlers.share.get_actual_home", return_value=self.home),
            patch("shutil.which", return_value=None),
            patch.dict(os.environ, env, clear=True),
        ):
            return self.service._resolve_existing_binary()

    def _invocations(self):
        return self.invoked

    def test_a_different_binary_in_the_legacy_location_is_never_run(self):
        """The whole point: discovery must not launch what it cannot vouch for."""
        self._make_binary(self.legacy)

        result = self._resolve()

        self.assertEqual(
            [],
            self._invocations(),
            "LDM executed an unapproved binary to read its version",
        )
        self.assertIsNone(result, "an unapproved binary must not be resolved")

    def test_the_refusal_says_what_to_do_about_it(self):
        self._make_binary(self.legacy)
        with patch("ldm_core.handlers.share.UI.info") as info:
            self._resolve()
        said = " ".join(str(c) for c in info.call_args_list)
        self.assertIn("LDM_LFR_TUNNEL_BIN", said)

    def test_a_legacy_symlink_to_the_approved_binary_still_works(self):
        """LDM-#1871's guarantee must survive: existing setups keep working."""
        self._make_binary(self.whitelisted)
        self.legacy.parent.mkdir(parents=True, exist_ok=True)
        self.legacy.symlink_to(self.whitelisted)

        result = self._resolve()

        self.assertIsNotNone(result, "a symlink to the approved binary must resolve")
        ran = self._invocations()
        self.assertEqual(1, len(ran), "it should be probed exactly once")
        self.assertEqual(
            str(self.whitelisted.resolve()),
            str(Path(ran[0]).resolve()),
            "must be invoked by the APPROVED path, not the symlink (LDM-#1871)",
        )

    def test_the_approved_location_is_unaffected(self):
        self._make_binary(self.whitelisted)
        result = self._resolve()
        self.assertIsNotNone(result)
        self.assertEqual(1, len(self._invocations()))


if __name__ == "__main__":
    unittest.main()
