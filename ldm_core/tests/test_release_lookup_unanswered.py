"""An unanswered release lookup is not a missing package (LDM-#2364).

`ldm quickstart aica` with an expired `GITHUB_PAT` printed:

    GitHub rejected the credential from GITHUB_PAT (HTTP 401), so LDM is
    continuing without it.
    GitHub API rate limit exceeded. Falling back to standard git clone.
    No compiled LDM Package (.ldmp) found in GitHub Releases.

The release had a package. The anonymous retry (LDM-#2347) was rate limited,
so GitHub never answered the question, and `_check_github_release_for_package`
could only return "no package". The middle line was false too: quickstart
never clones.

These tests drive the real `cmd_quickstart` -> `cmd_import` ->
`_check_github_release_for_package` -> `github_get`, stubbing only
`requests.get` with real `requests.Response` objects, and read what the user
sees at default verbosity. Any other route to the network trips an interlock
raised as a BaseException, so the lookup's own `except Exception` cannot
swallow it and turn it into a pass.
"""

import contextlib
import io
import json
import os
import re
import socket
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from requests.structures import CaseInsensitiveDict

from ldm_core.tests.test_workspace import MockWorkspaceManager

RESET_EPOCH = 1_800_000_000
RESET_HHMM = datetime.fromtimestamp(RESET_EPOCH).strftime("%H:%M")
FALSE_CLAIM = "No compiled LDM Package"
ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


class RealNetworkAttempted(BaseException):
    """BaseException so no `except Exception` on the path can absorb it."""


def _refuse_network(*_args, **_kwargs):
    raise RealNetworkAttempted("a test attempted a real network request")


def _response(status, payload=None, headers=None):
    r = requests.Response()
    r.status_code = status
    r.headers = CaseInsensitiveDict(headers or {})
    r._content = json.dumps(payload or {}).encode()
    r.url = "https://api.github.com/repos/x/y/releases/latest"
    return r


def _github(authenticated, anonymous):
    """Fake GitHub answering by whether a credential was sent."""

    def get(url, headers=None, **_kwargs):
        if isinstance(anonymous, Exception):
            raise anonymous
        return authenticated if (headers or {}).get("Authorization") else anonymous

    return get


class _QuickstartHarness(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.home = Path(self._td.name)
        # An existing project, replaced with --replace: nothing may be deleted
        # before the lookup fails.
        self.project_dir = self.home / "liferay-ai-commerce-accelerator"
        self.project_dir.mkdir()
        self.meta = self.project_dir / "meta"
        self.meta.write_text("tag=2026.q3.0\n")
        self.manager = MockWorkspaceManager()
        self.manager.non_interactive = True
        self.manager.args.replace = True

    def tearDown(self):
        self._td.cleanup()

    def _quickstart(self, get, env, gh_token=None):
        out, err = io.StringIO(), io.StringIO()
        self.package_imports: list = []
        environ = {"LDM_HOME": str(self.home), "LDM_DRY_RUN": "", **env}
        with (
            patch.dict(os.environ, environ, clear=True),
            patch("ldm_core.utils._rejected_credential_sources", set()),
            patch("ldm_core.ui.UI.VERBOSE", False),
            # Only reached with no env credential; it would spawn `gh`.
            patch(
                "ldm_core.utils.get_github_token",
                side_effect=lambda: (
                    os.environ.get("GITHUB_PAT")
                    or os.environ.get("GITHUB_TOKEN")
                    or gh_token
                ),
            ),
            patch("requests.get", side_effect=get),
            patch("requests.adapters.HTTPAdapter.send", side_effect=_refuse_network),
            patch.object(socket.socket, "connect", _refuse_network),
            patch(
                "ldm_core.workspace.importer._import_ldm_package",
                side_effect=lambda *a, **_k: self.package_imports.append(a),
            ),
            patch.object(
                MockWorkspaceManager,
                "detect_project_path",
                return_value=self.project_dir,
            ),
            patch("ldm_core.handlers.workspace.WorkspaceService._ensure_stopped"),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            self.manager.workspace.cmd_quickstart("aica")
        said = ANSI.sub("", out.getvalue() + err.getvalue())
        return ctx.exception.code, said

    def _assert_unanswered(self, said):
        self.assertIn("Could not query GitHub Releases", said)
        self.assertNotIn(FALSE_CLAIM, said)
        self.assertNotIn("Falling back", said)
        # --replace deleted nothing and no restore began.
        self.assertTrue(self.meta.exists())
        self.assertEqual(self.package_imports, [])
        self.manager.snapshot.cmd_restore.assert_not_called()


class QuickstartSaysTheLookupWentUnanswered(_QuickstartHarness):
    def test_rate_limit_after_a_rejected_pat_names_the_pat_and_the_reset(self):
        """The reported case: 401 with GITHUB_PAT, then an anonymous 403."""
        get = _github(
            _response(401),
            _response(
                403,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(RESET_EPOCH),
                },
            ),
        )
        code, said = self._quickstart(get, {"GITHUB_PAT": "expired"})
        self._assert_unanswered(said)
        self.assertEqual(code, 3)
        self.assertIn("rate limit exceeded", said)
        self.assertIn(f"resets at {RESET_HHMM}", said)
        self.assertIn("unset GITHUB_PAT", said)
        # get_github_token() prefers GITHUB_PAT, so GITHUB_TOKEN alone is no fix.
        self.assertIn("ahead of GITHUB_TOKEN", said)

    def test_a_401_is_an_authentication_failure(self):
        code, said = self._quickstart(
            _github(_response(401), _response(401)), {"GITHUB_PAT": "expired"}
        )
        self._assert_unanswered(said)
        self.assertEqual(code, 2)
        self.assertIn("HTTP 401", said)
        self.assertIn("GITHUB_PAT", said)

    def test_a_403_with_a_credential_is_not_called_a_rate_limit(self):
        code, said = self._quickstart(
            _github(_response(403), _response(200)), {"GITHUB_TOKEN": "sso-blocked"}
        )
        self._assert_unanswered(said)
        self.assertEqual(code, 2)
        self.assertIn("HTTP 403", said)
        self.assertIn("GITHUB_TOKEN", said)
        self.assertIn("SSO", said)
        self.assertNotIn("rate limit exceeded", said)

    def test_an_authenticated_rate_limit_is_still_a_rate_limit(self):
        get = _github(
            _response(
                403,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(RESET_EPOCH),
                },
            ),
            _response(200),
        )
        code, said = self._quickstart(get, {"GITHUB_TOKEN": "busy"})
        self._assert_unanswered(said)
        self.assertEqual(code, 3)
        self.assertIn(f"rate limit exceeded (resets at {RESET_HHMM})", said)

    def test_an_anonymous_429_advises_a_credential(self):
        code, said = self._quickstart(_github(_response(200), _response(429)), {})
        self._assert_unanswered(said)
        self.assertEqual(code, 3)
        self.assertIn("rate limit exceeded", said)
        self.assertIn("set GITHUB_PAT or GITHUB_TOKEN", said)
        self.assertNotIn("resets at", said)

    def test_a_network_failure_is_unanswered(self):
        code, said = self._quickstart(
            _github(None, requests.ConnectionError("no route")), {}
        )
        self._assert_unanswered(said)
        self.assertEqual(code, 3)
        self.assertIn("no route", said)


class AnAnsweredLookupIsUnchanged(_QuickstartHarness):
    """Controls: when GitHub did answer, "no package" is the true answer."""

    def test_a_release_without_an_ldmp_still_says_so(self):
        release = {"assets": [{"name": "notes.txt", "size": 50, "url": "u"}]}
        code, said = self._quickstart(
            _github(_response(200, release), _response(200, release)), {}
        )
        self.assertEqual(code, 1)
        self.assertIn(FALSE_CLAIM, said)
        self.assertNotIn("Could not query", said)
        self.assertTrue(self.meta.exists())

    def test_no_release_at_all_still_says_so(self):
        code, said = self._quickstart(_github(_response(404), _response(404)), {})
        self.assertEqual(code, 1)
        self.assertIn(FALSE_CLAIM, said)
        self.assertNotIn("Could not query", said)

    def test_a_present_package_is_imported(self):
        release = {
            "assets": [
                {"name": "aica.ldmp", "size": 50_000, "url": "u1"},
                {"name": "aica.ldmp.sha256", "size": 80, "url": "u2"},
            ]
        }
        # The stubbed import creates nothing, so quickstart then stops at
        # "Failed to locate"; what matters is that the package path was taken.
        self.meta.unlink()
        self.project_dir.rmdir()
        _, said = self._quickstart(
            _github(_response(401), _response(200, release)),
            {"GITHUB_PAT": "expired"},
        )
        self.assertEqual(len(self.package_imports), 1)
        self.assertNotIn("Could not query", said)
        self.assertNotIn(FALSE_CLAIM, said)


if __name__ == "__main__":
    unittest.main()
