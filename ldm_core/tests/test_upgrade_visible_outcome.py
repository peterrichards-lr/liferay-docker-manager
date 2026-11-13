"""`ldm system upgrade` states its outcome at default verbosity.

LDM-#2358: with an update available, `--check` printed only its heading, and
`-y` on a pre-release returned without a word. Both outcomes went through
`UI.detail`, which prints only under `--info`/`--verbose`.

These tests drive the real `run_upgrade` with the real `UI` sinks and read
what reaches the terminal. Nothing patches `UI.detail` or `UI.info`, and the
verbosity flags are pinned to what a plain invocation leaves them at -- the
condition the bug needs, and the one a test that stubbed the sinks would hide.
Only the GitHub lookup is faked.
"""

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ldm_core.ui import UI

LATEST = "2.26.9"


def _no_download(*_args, **_kwargs):
    """Interlock: an outcome that should return before the download must never
    reach it. Without this a regression would start a real download."""
    raise AssertionError("run_upgrade reached the download step")


class TestUpgradeOutcomeIsVisible(unittest.TestCase):
    def setUp(self):
        # A plain `ldm system upgrade` passes none of --info, --verbose or
        # --quiet. NO_COLOR keeps the assertions free of ANSI codes.
        for flag in ("VERBOSE", "INFO_MODE", "QUIET_MODE", "NON_INTERACTIVE"):
            self.addCleanup(setattr, UI, flag, getattr(UI, flag, False))
            setattr(UI, flag, False)
        self.addCleanup(setattr, UI, "NO_COLOR", getattr(UI, "NO_COLOR", False))
        UI.NO_COLOR = True

    def _run(self, installed, *, check_only=False, version=None, yes=False):
        """Returns everything `run_upgrade` wrote to stdout and stderr."""
        from ldm_core.diagnostics import upgrade as up

        # A real namespace, not a MagicMock: `run_upgrade` reads its flags with
        # `getattr(args, name, False)`, and every MagicMock attribute is truthy.
        handler = MagicMock()
        handler.manager.args = SimpleNamespace(
            pre_release=False,
            check_only=check_only,
            version=version,
            repair=False,
            non_interactive=yes,
        )
        # `-y` sets both of these in production (manager.py).
        handler.manager.non_interactive = yes
        UI.NON_INTERACTIVE = yes

        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(up, "VERSION", installed),
            patch.object(
                up,
                "check_for_updates",
                return_value=(LATEST, "https://example.invalid/ldm"),
            ),
            patch.object(up.tempfile, "mkstemp", side_effect=_no_download),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            up.run_upgrade(handler)
        return out.getvalue() + err.getvalue()

    def _after_heading(self, said):
        return said.split("LDM Self-Upgrade", 1)[-1]

    def test_check_from_stable_reports_the_new_version(self):
        said = self._after_heading(self._run("2.26.8", check_only=True))
        self.assertIn(f"A new version of LDM is available: v{LATEST}", said)
        self.assertIn("ldm upgrade", said)

    def test_check_from_prerelease_reports_the_new_version(self):
        said = self._after_heading(self._run("2.26.9-pre.1", check_only=True))
        self.assertIn(f"A new version of LDM is available: v{LATEST}", said)

    def test_check_with_a_target_version_reports_it(self):
        said = self._after_heading(
            self._run("2.26.8", check_only=True, version=f"v{LATEST}")
        )
        self.assertIn(f"Target version v{LATEST} is available.", said)

    def test_yes_on_a_prerelease_says_it_did_not_upgrade_and_how_to(self):
        said = self._after_heading(self._run("2.26.9-pre.1", yes=True))
        self.assertIn("Not upgraded: staying on v2.26.9-pre.1", said)
        self.assertIn("--beta", said)
        self.assertIn(f"-v v{LATEST}", said)

    def test_declining_the_tier_switch_explains_it_and_says_so(self):
        from ldm_core.diagnostics import upgrade as up

        with patch.object(up.UI, "confirm", return_value=False):
            said = self._after_heading(self._run("2.26.9-pre.1"))
        self.assertIn("You are currently on a beta build (v2.26.9-pre.1)", said)
        self.assertIn(f"The latest stable version is v{LATEST}", said)
        self.assertIn("Upgrade cancelled: staying on v2.26.9-pre.1", said)


if __name__ == "__main__":
    unittest.main()
