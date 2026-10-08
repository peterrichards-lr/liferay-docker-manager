"""A rejected GitHub credential must not be reported as a missing package.

LDM-#2098. `ldm quickstart <workspace>` reported "No compiled LDM Package
found in GitHub Releases" against a release that has one and is publicly
readable, because an expired `GITHUB_PAT` turned a 200 into a 401:

    anonymous        -> HTTP 200   (assets present)
    with GITHUB_PAT  -> HTTP 401

`_check_github_release_for_package` handled 200 and 403 and sent everything
else -- 401 included -- to `UI.debug`, which shows nothing at default
verbosity. The caller then reported the one conclusion the evidence did not
support: that the artifact was absent.

A rejected credential and an absent artifact are different problems with
different fixes. These tests assert the user is told which one they have.
"""

import unittest
from unittest.mock import MagicMock, patch


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _call(status, token, source="GITHUB_PAT"):
    """Drive the real function and return every UI.warning string it emitted."""
    from ldm_core.workspace.importer import _check_github_release_for_package

    ui = MagicMock()
    with (
        patch("ldm_core.utils.UI", ui),
        patch("ldm_core.utils.get_github_token", return_value=token),
        patch("ldm_core.utils.github_token_source", return_value=source),
        patch("requests.get", return_value=_Response(status)),
    ):
        _check_github_release_for_package(MagicMock(), "acme", "widget", "acme/widget")
    return " ".join(str(c.args[0]) for c in ui.warning.call_args_list)


class TheRejectedCredentialIsNamed(unittest.TestCase):
    def test_a_401_warns_visibly(self):
        """It was UI.debug, so at default verbosity nothing was shown at all."""
        self.assertTrue(_call(401, "dead-token").strip(), "401 produced no warning")

    def test_a_401_says_the_credential_was_rejected(self):
        blob = _call(401, "dead-token").lower()
        self.assertIn("reject", blob)

    def test_a_401_names_which_source_supplied_the_credential(self):
        """GITHUB_PAT wins over GITHUB_TOKEN and over the gh CLI, so a user
        with three credentials configured cannot otherwise tell which one is
        being sent, let alone which to fix."""
        self.assertIn("GITHUB_PAT", _call(401, "dead-token", source="GITHUB_PAT"))

    def test_a_401_says_it_may_work_without_the_credential(self):
        """The actionable half: the release was readable anonymously."""
        blob = _call(401, "dead-token").lower()
        self.assertTrue(
            "unset" in blob or "without" in blob,
            "the 401 message does not suggest proceeding without the credential",
        )


class TheRateLimitClaimIsNotMadeBlindly(unittest.TestCase):
    def test_an_authenticated_403_does_not_assert_rate_limit(self):
        """403 WITH a credential is commonly SSO enforcement or missing
        scope. Calling it a rate limit sends the user to wait it out."""
        blob = _call(403, "some-token").lower()
        self.assertNotIn("rate limit exceeded", blob)

    def test_an_authenticated_403_still_warns(self):
        self.assertTrue(_call(403, "some-token").strip())

    def test_an_anonymous_403_still_reports_the_rate_limit(self):
        """The contrast case: without a credential, rate limiting is the
        overwhelmingly likely cause and that advice was correct."""
        self.assertIn("rate limit", _call(403, None, source=None).lower())


class TheSuccessPathIsUnchanged(unittest.TestCase):
    def test_a_200_warns_about_nothing(self):
        self.assertEqual("", _call(200, "good-token").strip())


class TheSourceMatchesWhatIsActuallySent(unittest.TestCase):
    """The helper is message-only, so a drift from get_github_token()'s
    precedence would point the user at the wrong credential -- which is
    worse than saying nothing, because they would unset a working one."""

    def _both(self, env):
        import importlib

        utils = importlib.import_module("ldm_core.utils")
        with patch.dict("os.environ", env, clear=True):
            return utils.get_github_token(), utils.github_token_source()

    def test_pat_wins_and_is_named(self):
        token, source = self._both({"GITHUB_PAT": "p", "GITHUB_TOKEN": "t"})
        self.assertEqual(token, "p")
        self.assertEqual(source, "GITHUB_PAT")

    def test_token_is_named_when_pat_is_absent(self):
        token, source = self._both({"GITHUB_TOKEN": "t"})
        self.assertEqual(token, "t")
        self.assertEqual(source, "GITHUB_TOKEN")

    def test_an_empty_pat_does_not_win(self):
        """An exported-but-empty variable is common in CI and must not
        shadow a working one -- get_github_token() uses `or`, so it skips
        an empty value, and the source must agree."""
        token, source = self._both({"GITHUB_PAT": "", "GITHUB_TOKEN": "t"})
        self.assertEqual(token, "t")
        self.assertEqual(source, "GITHUB_TOKEN")

    def test_no_credential_anywhere_names_nothing(self):
        with patch("ldm_core.utils.get_github_token", return_value=None):
            from ldm_core import utils

            with patch.dict("os.environ", {}, clear=True):
                self.assertIsNone(utils.github_token_source())
