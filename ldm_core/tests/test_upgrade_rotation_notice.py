"""LDM-#1974: an upgrade past the fix must tell the user to rotate.

`ldm system doctor --bundle` was documented as producing a *sanitized* zip for
attaching to support tickets, and wrote `~/.ldmrc`, every project's meta and the
lfr-tunnel token into it. Two went through a redaction step that is a no-op on
JSON; the token was copied verbatim with none attempted.

**A CHANGELOG entry is not enough for that.** The action is the user's and it is
time-sensitive -- a disclosed credential stays valid until rotated -- so it has
to be said at the moment they upgrade, when they are looking.

Asserted against REAL captured stdout, not a mocked `UI`. LDM-#1971 was five
fatal paths whose remedy was emitted at a tier that prints nothing; a mocked
`UI` passes whether or not the text reaches a terminal, which is the whole
defect in that family.
"""

import contextlib
import io
import unittest

from ldm_core.diagnostics.upgrade import _announce_post_upgrade_actions
from ldm_core.ui import UI


def _capture(before, after):
    out, err = io.StringIO(), io.StringIO()
    with UI.patch(verbose=False, info_mode=False, quiet_mode=False):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            _announce_post_upgrade_actions(before, after)
    return out.getvalue() + err.getvalue()


class TestItFiresWhenCrossingTheFix(unittest.TestCase):
    def test_upgrading_from_before_the_fix_warns(self):
        text = _capture("2.25.0", "2.26.0")
        self.assertIn("doctor --bundle", text)
        self.assertIn("rotate", text.lower())

    def test_it_names_every_credential_that_was_exposed(self):
        """A warning that does not say WHAT to rotate is not actionable."""
        text = _capture("2.25.0", "2.26.0")
        for secret in (
            "ngrok_authtoken",
            "admin_password",
            "db_password",
            "lfr-tunnel",
        ):
            with self.subTest(secret=secret):
                self.assertIn(secret, text)

    def test_it_says_when_nothing_was_exposed(self):
        """Most users never ran the command. Telling them so prevents a
        pointless rotation and stops the warning reading as an alarm."""
        text = _capture("2.25.0", "2.26.0")
        self.assertIn("never run that command", text)

    def test_it_reaches_real_stdout_at_default_verbosity(self):
        """The LDM-#1971 lesson. A notice emitted at a tier nobody sees is not
        a notice, and the user cannot re-run an upgrade to recover it."""
        text = _capture("2.25.0", "2.26.0")
        self.assertTrue(text.strip(), "nothing reached the terminal")


class TestItDoesNotNag(unittest.TestCase):
    def test_an_upgrade_entirely_after_the_fix_is_silent(self):
        self.assertEqual("", _capture("2.26.0", "2.26.1").strip())

    def test_an_upgrade_entirely_before_the_fix_is_silent(self):
        """Not crossing the boundary, so the state is unchanged by this
        upgrade and the notice would be noise."""
        self.assertEqual("", _capture("2.24.0", "2.25.0").strip())

    def test_a_prerelease_of_the_fixing_version_still_fires(self):
        """`2.26.0-pre.6` carries the fix, so someone landing on it is crossing
        the boundary and needs telling."""
        self.assertIn("rotate", _capture("2.25.0", "2.26.0-pre.6").lower())


class TestTheUpgradePathActuallyCallsIt(unittest.TestCase):
    """Crosses the seam between the notice and its callers.

    Written after a neuter probe: removing both call sites left every other test
    in this module green, because they invoke the function directly. Same shape
    as LDM-#1987, where 74 port assertions passed while the value never reached
    the composer, and as LDM-#1974, where the redaction tests passed while the
    bundle used the wrong function.

    `run_upgrade` replaces a binary and re-execs, so driving it end to end in a
    unit test is not practical -- the wiring is asserted against the source.
    """

    def _upgrade_source(self):
        import inspect

        from ldm_core.diagnostics import upgrade

        return inspect.getsource(upgrade)

    def test_both_success_paths_announce(self):
        """TWO sites, because the binary replacement has a plain path and a
        sudo fallback, and a user hitting the fallback needs telling just as
        much as one who does not."""
        src = self._upgrade_source()
        self.assertEqual(
            2,
            src.count("_announce_post_upgrade_actions(VERSION, latest)"),
            "the notice must fire from BOTH upgrade success paths -- the direct "
            "replacement and the sudo fallback (LDM-#1974)",
        )

    def test_it_is_announced_after_success_not_before(self):
        """Announcing before the replacement succeeded would tell a user to
        rotate credentials over an upgrade that then failed."""
        src = self._upgrade_source()
        for match in range(2):
            idx = -1
            for _ in range(match + 1):
                idx = src.index(
                    "_announce_post_upgrade_actions(VERSION, latest)", idx + 1
                )
            preceding = src[max(0, idx - 200) : idx]
            self.assertIn(
                "Successfully upgraded",
                preceding,
                "the notice must follow the success message, not precede it",
            )


class TestItNeverBreaksAnUpgrade(unittest.TestCase):
    """This runs immediately after the binary has been replaced. A traceback
    here would make a SUCCESSFUL upgrade look like a failed one."""

    def test_an_unparseable_version_never_raises(self):
        for before, after in (("nonsense", "2.26.0"), ("2.25.0", None), (None, None)):
            with self.subTest(before=before, after=after):
                _capture(before, after)  # must not raise

    def test_an_unknown_starting_version_errs_toward_warning(self):
        """Deliberate, and measured rather than assumed.

        `version_to_tuple` returns `(0, 0, 0, 0)` for anything it cannot parse
        rather than raising, so an unknown starting version compares as older
        than the fix and the notice fires.

        That is the right direction for a security notice: an unnecessary
        rotation reminder costs someone a minute, a missed one leaves a live
        credential in a shared artifact. Same reasoning as
        `is_credential_shaped` erring toward masking.
        """
        self.assertIn("rotate", _capture("nonsense", "2.26.0").lower())

    def test_an_unknown_target_version_stays_quiet(self):
        """The other direction. If we cannot tell that the upgrade landed ON or
        after the fix, there is nothing to claim about it."""
        self.assertEqual("", _capture("2.25.0", None).strip())


if __name__ == "__main__":
    unittest.main()
