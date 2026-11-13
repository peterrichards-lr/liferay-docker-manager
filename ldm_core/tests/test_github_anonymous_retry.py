"""A rejected GitHub credential no longer fails a public lookup (LDM-#2347).

With an expired `GITHUB_PAT`, `ldm quickstart aica` warned that the credential
was rejected and then exited 1 with "No compiled LDM Package found", against a
public release that answers 200 anonymously. LDM-#2098 made the 401 visible;
this makes it recoverable.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core import utils


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

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


class GithubGetFallsBackToAnonymous(unittest.TestCase):
    def setUp(self):
        self.ui = MagicMock()
        patchers: list = [
            patch("ldm_core.utils._rejected_credential_sources", set()),
            patch("ldm_core.utils.UI", self.ui),
            patch("ldm_core.utils.get_github_token", return_value="dead"),
            patch("ldm_core.utils.github_token_source", return_value="GITHUB_PAT"),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_401_is_retried_without_the_credential(self):
        get, calls = _rejecting(_Response(401), _Response(200))
        with patch("requests.get", side_effect=get):
            response, token = utils.github_get("https://api.github.com/x")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(token)
        self.assertEqual(calls, ["token dead", None])

    def test_an_accepted_credential_is_used_once_and_returned(self):
        get, calls = _rejecting(_Response(200), _Response(200))
        with patch("requests.get", side_effect=get):
            _, token = utils.github_get("https://api.github.com/x")
        self.assertEqual(token, "dead")
        self.assertEqual(calls, ["token dead"])

    def test_the_rejection_is_named_once_per_source(self):
        get, _ = _rejecting(_Response(401), _Response(200))
        with patch("requests.get", side_effect=get):
            utils.github_get("https://api.github.com/x")
            utils.github_get("https://api.github.com/y")
        warned = " ".join(str(c.args[0]) for c in self.ui.warning.call_args_list)
        self.assertEqual(warned.count("GitHub rejected the credential"), 1)
        self.assertIn("GITHUB_PAT", warned)

    def test_a_403_is_not_retried(self):
        # SSO enforcement or scope: anonymous would only trade it for a
        # misleading 404 on a private repository.
        get, calls = _rejecting(_Response(403), _Response(200))
        with patch("requests.get", side_effect=get):
            response, _ = utils.github_get("https://api.github.com/x")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(calls, ["token dead"])


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
        get, _ = _rejecting(_Response(401), _Response(200, release))
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


class TheSeedLookupSeesPastARejectedCredential(unittest.TestCase):
    """Through the real `_resolve_seed_url`. A 401 here used to end in
    "Failed to fetch release info: HTTP 401", hiding whether a seed existed."""

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
        self.assertEqual(calls, ["token dead", None])


if __name__ == "__main__":
    unittest.main()
