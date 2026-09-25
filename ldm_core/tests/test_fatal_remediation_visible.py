"""LDM-#1971: fatal errors whose remediation was printed at a tier nobody sees.

`UI.detail` prints only under `--info` or `--verbose`. Several fatal paths put
the error at `UI.error` and the *fix* at `UI.detail`, so the user saw the
refusal and nothing else -- and could not recover the advice by re-running with
`--verbose`, because the process had already exited.

These tests capture REAL stdout/stderr at DEFAULT verbosity. Asserting against
a mocked `UI` would pass with the bug fully present, because the call is made
either way; the question is only whether it reaches the terminal.
"""

import contextlib
import io
import platform
import re
import unittest
from unittest.mock import patch

from ldm_core.ui import UI


@contextlib.contextmanager
def _default_verbosity():
    """No --info, no --verbose, no --quiet. What a plain `ldm ...` gets."""
    with UI.patch(verbose=False, info_mode=False, quiet_mode=False):
        yield


def _capture(fn):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        with contextlib.suppress(SystemExit):
            fn()
    return out.getvalue() + err.getvalue()


class TestSudoRefusalShowsTheFix(unittest.TestCase):
    """The root guard runs on EVERY invocation, so it is the first thing a
    new Linux user meets when Docker needs group membership."""

    def _run_as_root_on_linux(self):
        import argparse

        from ldm_core.cli import _check_root_safety

        args = argparse.Namespace(command="run", subcommand=None)
        with (
            patch("os.geteuid", return_value=0),
            patch.object(platform, "system", return_value="Linux"),
            patch.dict(
                "os.environ", {"LDM_ALLOW_ROOT": "false", "GITHUB_ACTIONS": "false"}
            ),
            patch("ldm_core.cli.SCRIPT_DIR") as script_dir,
        ):
            script_dir.__truediv__.return_value.exists.return_value = False
            with _default_verbosity():
                return _capture(lambda: _check_root_safety(args))

    def test_it_still_refuses(self):
        """The guard itself must not be weakened by making it talkative."""
        output = self._run_as_root_on_linux()
        self.assertIn("Do not run LDM with 'sudo'", output)

    def test_the_usermod_fix_reaches_the_terminal(self):
        """The whole point. Without this the user is told 'no' and nothing else."""
        output = self._run_as_root_on_linux()
        self.assertIn(
            "usermod -aG docker",
            output,
            "the remediation is suppressed at default verbosity (LDM-#1971)",
        )

    def test_the_reason_and_the_docs_link_reach_the_terminal(self):
        output = self._run_as_root_on_linux()
        self.assertIn("cache ownership", output)
        self.assertIn("troubleshooting-sudo--root-issues", output)


class TestColimaRemediationShowsTheCommands(unittest.TestCase):
    """This one printed the literal words "To fix this, run:" followed by
    nothing at all, then exited."""

    def test_the_commands_follow_the_heading(self):
        import inspect

        from ldm_core.handlers import base

        source = inspect.getsource(base.BaseHandler)
        heading = source.index('UI.info("\\nTo fix this, run:")')
        block = source[heading : heading + 1200]
        self.assertIn("colima stop", block)
        self.assertNotIn(
            "UI.detail(",
            block[: block.index("colima start --mount") + 200],
            "the colima remediation must not sit behind --verbose (LDM-#1971)",
        )


class TestNoFatalPathStillHidesItsRemediation(unittest.TestCase):
    """A guard against the pattern coming back.

    The bug was reported at one site; auditing found five. This asserts the
    five fixed ones stay fixed, by checking the sources carry no `UI.detail`
    inside a block that ends in an exit.
    """

    KNOWN_FIXED = (
        ("ldm_core/cli.py", "usermod -aG docker"),
        ("ldm_core/handlers/base.py", "usermod -aG docker"),
        ("ldm_core/handlers/base.py", "colima start"),
        ("ldm_core/handlers/ai.py", "aistudio.google.com"),
        ("ldm_core/diagnostics/upgrade.py", "manual installation command"),
    )

    # The emitting calls, as opposed to the colour constants. Searching
    # backwards for a bare "UI." lands on `{UI.CYAN}` inside the f-string and
    # silently passes -- three of the five sites below did exactly that until
    # a neuter probe caught it.
    _EMITTER = re.compile(r"UI\.(detail|info|error|warning|raw|die|success|hint)\(")

    def test_each_fixed_site_prints_at_default_verbosity(self):
        from pathlib import Path

        from ldm_core.constants import SCRIPT_DIR

        root = Path(SCRIPT_DIR)
        for rel, needle in self.KNOWN_FIXED:
            with self.subTest(site=rel, needle=needle):
                text = (root / rel).read_text(encoding="utf-8")
                idx = text.find(needle)
                self.assertNotEqual(-1, idx, f"{needle} no longer in {rel}")

                emitters = [m for m in self._EMITTER.finditer(text) if m.start() < idx]
                self.assertTrue(emitters, f"{rel}: no UI call precedes '{needle}'")
                tier = emitters[-1].group(1)
                self.assertNotEqual(
                    "detail",
                    tier,
                    f"{rel}: '{needle}' is back behind --info/--verbose (LDM-#1971)",
                )


if __name__ == "__main__":
    unittest.main()
