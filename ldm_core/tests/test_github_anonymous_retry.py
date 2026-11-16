"""Public GitHub lookups go anonymous first (LDM-#2382).

LDM-#2347 made a rejected credential recoverable: on a 401, retry without it.
But the credential still went first, on every lookup, though every default
lookup is against a public repository and the credential existed only to lift
the anonymous rate limit (LDM-#726). A user's `gh` login was read and sent on
each daily update check, and an expired one cost every lookup a 401 plus a
warning printed to stdout, where it became a verification report's file name.

Now the credential is read and sent only when the anonymous answer is
rate-limited, or a 404, which is how GitHub hides a private repository.
"""

import contextlib
import io
import unittest
from unittest.mock import MagicMock, patch

from ldm_core import utils


class _Response:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def _rejecting(authenticated, anonymous):
    """A fake GitHub: one status for requests carrying a credential, another
    for requests without."""
    calls = []

    def get(url, headers=None, **kwargs):
        sent = (headers or {}).get("Authorization")
        calls.append(sent)
        return authenticated if sent else anonymous

    return get, calls


RATE_LIMITED = _Response(403, headers={"X-RateLimit-Remaining": "0"})


class GithubGetGoesAnonymousFirst(unittest.TestCase):
    def setUp(self):
        self.ui = MagicMock()
        self.token = MagicMock(return_value="dead")
        patchers: list = [
            patch("ldm_core.utils._rejected_credential_sources", set()),
            patch("ldm_core.utils.UI", self.ui),
            patch("ldm_core.utils.get_github_token", self.token),
            patch("ldm_core.utils.github_token_source", return_value="GITHUB_PAT"),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_public_answer_never_reads_or_sends_the_credential(self):
        """The point of LDM-#2382: not even `gh auth token` is run."""
        get, calls = _rejecting(_Response(401), _Response(200))
        with patch("requests.get", side_effect=get):
            response, token = utils.github_get("https://api.github.com/x")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(token)
        self.assertEqual(calls, [None])
        self.token.assert_not_called()
        self.ui.warning.assert_not_called()

    def test_a_rate_limit_retries_with_the_credential(self):
        get, calls = _rejecting(_Response(200), RATE_LIMITED)
        with patch("requests.get", side_effect=get):
            response, token = utils.github_get("https://api.github.com/x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(token, "dead")
        self.assertEqual(calls, [None, "token dead"])

    def test_a_429_retries_with_the_credential(self):
        get, calls = _rejecting(_Response(200), _Response(429))
        with patch("requests.get", side_effect=get):
            _, token = utils.github_get("https://api.github.com/x")
        self.assertEqual(token, "dead")
        self.assertEqual(calls, [None, "token dead"])

    def test_a_404_retries_with_the_credential_for_a_private_repository(self):
        get, calls = _rejecting(_Response(200), _Response(404))
        with patch("requests.get", side_effect=get):
            response, token = utils.github_get("https://api.github.com/x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(token, "dead")
        self.assertEqual(calls, [None, "token dead"])

    def test_a_403_that_is_not_a_rate_limit_is_not_retried(self):
        get, calls = _rejecting(_Response(200), _Response(403))
        with patch("requests.get", side_effect=get):
            response, _ = utils.github_get("https://api.github.com/x")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(calls, [None])

    def test_a_rejected_credential_keeps_the_rate_limited_answer(self):
        get, calls = _rejecting(_Response(401), RATE_LIMITED)
        with patch("requests.get", side_effect=get):
            response, token = utils.github_get("https://api.github.com/x")
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(token)
        self.assertEqual(calls, [None, "token dead"])

    def test_a_rejected_credential_after_a_404_reports_the_401(self):
        """A private repository answers 404 anonymously too, so "not found"
        would be a claim the lookup cannot support (LDM-#2364)."""
        get, _ = _rejecting(_Response(401), _Response(404))
        with patch("requests.get", side_effect=get):
            response, token = utils.github_get("https://api.github.com/x")
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(token)

    def test_no_credential_keeps_the_anonymous_answer(self):
        self.token.return_value = None
        get, calls = _rejecting(_Response(200), _Response(404))
        with patch("requests.get", side_effect=get):
            response, token = utils.github_get("https://api.github.com/x")
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(token)
        self.assertEqual(calls, [None])

    def test_the_rejection_is_named_once_per_source(self):
        get, _ = _rejecting(_Response(401), _Response(404))
        with patch("requests.get", side_effect=get):
            utils.github_get("https://api.github.com/x")
            utils.github_get("https://api.github.com/y")
        warned = " ".join(str(c.args[0]) for c in self.ui.warning.call_args_list)
        self.assertEqual(warned.count("GitHub rejected the credential"), 1)
        self.assertIn("Renew or unset GITHUB_PAT", warned)


class TheRejectionWarningStaysOffStdout(unittest.TestCase):
    """Through the real UI. On stdout it became the verification report's
    file name, via `ldm system doctor --slug`."""

    def test_the_warning_is_on_stderr_only(self):
        get, _ = _rejecting(_Response(401), _Response(404))
        out, err = io.StringIO(), io.StringIO()
        with (
            patch("ldm_core.utils._rejected_credential_sources", set()),
            patch("ldm_core.utils.get_github_token", return_value="dead"),
            patch("ldm_core.utils.github_token_source", return_value="GITHUB_PAT"),
            patch("requests.get", side_effect=get),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            utils.github_get("https://api.github.com/x")
        self.assertEqual(out.getvalue(), "")
        self.assertIn("GitHub rejected the credential", err.getvalue())


class TheAdviceFitsTheSource(unittest.TestCase):
    def test_a_variable_is_renewed_or_unset(self):
        self.assertEqual(
            utils.github_credential_advice("GITHUB_TOKEN"),
            "Renew or unset GITHUB_TOKEN",
        )

    def test_the_gh_cli_is_logged_in_or_out(self):
        advice = utils.github_credential_advice("the gh CLI")
        self.assertIn("gh auth login", advice)
        self.assertIn("gh auth logout", advice)
        self.assertNotIn("unset", advice)


class QuickstartFindsThePublicPackage(unittest.TestCase):
    """Through the real `_check_github_release_for_package`."""

    def test_an_expired_pat_still_finds_a_public_release(self):
        from ldm_core.workspace.importer import _check_github_release_for_package

        release = {
            "assets": [
                {"name": "aica.ldmp", "size": 50_000, "url": "u1"},
                {"name": "aica.ldmp.sha256", "size": 80, "url": "u2"},
            ]
        }
        get, calls = _rejecting(_Response(401), _Response(200, release))
        with (
            patch("ldm_core.utils._rejected_credential_sources", set()),
            patch("ldm_core.utils.UI"),
            patch("ldm_core.utils.get_github_token", return_value="dead"),
            patch("ldm_core.utils.github_token_source", return_value="GITHUB_PAT"),
            patch("requests.get", side_effect=get),
        ):
            has_ldmp, ldmp, sha, token, unanswered = _check_github_release_for_package(
                MagicMock(), "peterrichards-lr", "aica", "peterrichards-lr/aica"
            )
        self.assertTrue(has_ldmp)
        self.assertIsNone(unanswered)
        self.assertEqual(ldmp["name"], "aica.ldmp")
        self.assertEqual(sha["name"], "aica.ldmp.sha256")
        # The download reuses this, so it must not resend the rejected one.
        self.assertIsNone(token)
        self.assertEqual(calls, [None])

    def test_a_private_release_is_found_with_the_credential(self):
        from ldm_core.workspace.importer import _check_github_release_for_package

        release = {
            "assets": [
                {"name": "aica.ldmp", "size": 50_000, "url": "u1"},
                {"name": "aica.ldmp.sha256", "size": 80, "url": "u2"},
            ]
        }
        get, calls = _rejecting(_Response(200, release), _Response(404))
        with (
            patch("ldm_core.utils._rejected_credential_sources", set()),
            patch("ldm_core.utils.UI"),
            patch("ldm_core.utils.get_github_token", return_value="good"),
            patch("ldm_core.utils.github_token_source", return_value="the gh CLI"),
            patch("requests.get", side_effect=get),
        ):
            has_ldmp, _, _, token, unanswered = _check_github_release_for_package(
                MagicMock(), "peterrichards-lr", "aica", "peterrichards-lr/aica"
            )
        self.assertTrue(has_ldmp)
        self.assertIsNone(unanswered)
        # The download needs it too.
        self.assertEqual(token, "good")
        self.assertEqual(calls, [None, "token good"])


class TheSeedLookupNeedsNoCredential(unittest.TestCase):
    """Through the real `_resolve_seed_url`. A 401 here used to end in
    "Failed to fetch release info: HTTP 401", hiding whether a seed existed;
    now the public lookup never sends the credential at all."""

    def test_the_seed_is_found_anonymously(self):
        from ldm_core.handlers.assets import AssetService

        release = {
            "tag_name": "seeds",
            "assets": [{"name": "s.tar.gz", "browser_download_url": "https://dl/s"}],
        }
        get, calls = _rejecting(_Response(401), _Response(200, release))
        service = AssetService(MagicMock())
        with (
            patch("ldm_core.utils._rejected_credential_sources", set()),
            patch("ldm_core.utils.UI"),
            patch("ldm_core.utils.get_github_token", return_value="dead"),
            patch("ldm_core.utils.github_token_source", return_value="GITHUB_PAT"),
            patch("requests.head", return_value=_Response(404)),
            patch("requests.get", side_effect=get),
        ):
            url = service._resolve_seed_url(
                "https://github.com/x/releases/download/seeds/s.tar.gz",
                "2026.q3.5",
                "seeds",
                "s.tar.gz",
                {"Authorization": "token dead"},
            )
        self.assertEqual(url, "https://dl/s")
        self.assertEqual(calls, [None])


if __name__ == "__main__":
    unittest.main()
