"""LDM-#1970: `ldm config` printed stored credentials verbatim.

It dumped a real `gemini_api_key` and an `ngrok_authtoken` into a user's
terminal -- and therefore into that session's transcript, logs, and anything
capturing output.

Masking is decided from the KEY NAME, never from the rendered text:

* `ldm config <key>` prints the value alone, with no key name in the output,
  so nothing downstream could redact it after the fact.
* `UI.redact` requires `KEY=value` with no spaces. The listing prints
  `key = value`, and stored config is JSON. Neither matches.
"""

import argparse
import contextlib
import io
import shutil
import unittest

from ldm_core.handlers.config import ConfigService
from ldm_core.utils import is_credential_shaped


class TestWhichKeysAreCredentialShaped(unittest.TestCase):
    def test_the_keys_that_actually_leaked(self):
        """The two from the report, by name."""
        self.assertTrue(is_credential_shaped("gemini_api_key"))
        self.assertTrue(is_credential_shaped("ngrok_authtoken"))

    def test_common_credential_shapes(self):
        for key in (
            "admin_password",
            "db_password",
            "client_secret",
            "github_pat",
            "private_key",
            "oauth2.client.secret",
            "SOME_TOKEN",
        ):
            with self.subTest(key=key):
                self.assertTrue(is_credential_shaped(key))

    def test_ordinary_keys_are_not_masked(self):
        """The control. Masking everything would make `ldm config` useless."""
        for key in (
            "share_domain",
            "host_name",
            "port",
            "container_name",
            "tag",
            "db_type",
        ):
            with self.subTest(key=key):
                self.assertFalse(is_credential_shaped(key))

    def test_a_word_merely_ending_in_key_is_not_masked(self):
        """`endswith("key")` masks `monkey`, `donkey` and `turkey`.

        Measured, not hypothetical -- the first version of this helper did
        exactly that. The key is tokenised on separators instead.
        """
        for key in ("monkey", "donkey", "turkey", "keystore_path", "keep_state"):
            with self.subTest(key=key):
                self.assertFalse(is_credential_shaped(key))

    def test_it_errs_toward_masking(self):
        """`tombstone_keep_credentials` is a boolean flag, and is masked.

        Hiding a non-secret is a cosmetic annoyance; printing a secret is not.
        Recorded so the behaviour is deliberate rather than accidental.
        """
        self.assertTrue(is_credential_shaped("tombstone_keep_credentials"))

    def test_empty_and_none_are_safe(self):
        self.assertFalse(is_credential_shaped(""))
        self.assertFalse(is_credential_shaped(None))


class TestUiRedactCannotDoThisJob(unittest.TestCase):
    """Why masking is by key name rather than by scrubbing the output.

    Pins the limitation so nobody 'simplifies' this to a `UI.redact` call.
    """

    def test_redact_misses_the_spaced_form_the_listing_prints(self):
        from ldm_core.ui import UI

        line = "  gemini_api_key = AIzaSyExample"
        self.assertIn(
            "AIzaSyExample",
            UI.redact(line),
            "UI.redact requires KEY=value with no spaces; the config listing "
            "prints 'key = value', so redaction does not fire (LDM-#1970)",
        )

    def test_redact_misses_json(self):
        from ldm_core.ui import UI

        blob = '{"ngrok_authtoken": "2abcSecretValue"}'
        self.assertIn(
            "2abcSecretValue",
            UI.redact(blob),
            "stored config is JSON; UI.redact does nothing to it (LDM-#1974)",
        )


class TestConfigCommandDoesNotPrintCredentials(unittest.TestCase):
    """Drives the real `cmd_config` and reads real captured stdout.

    Asserting on a mocked `UI` would pass even if the value never reached the
    terminal, which is the whole defect. `print()` is what leaked, so `print()`
    is what gets captured.
    """

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".ldmrc").write_text(
            json.dumps(
                {
                    # Not credentials -- deliberately self-describing sentinels
                    # whose only job is to be searched for in captured output.
                    # detect-secrets flags them on the key name, which is the
                    # same heuristic this module is testing.
                    "gemini_api_key": "AIzaSyTHIS_MUST_NOT_BE_PRINTED",  # pragma: allowlist secret
                    "ngrok_authtoken": "2abcTHIS_MUST_NOT_BE_PRINTED",  # pragma: allowlist secret
                    "share_domain": "example.test",
                }
            ),
            encoding="utf-8",
        )

        home_patcher = patch(
            "ldm_core.handlers.config.get_actual_home", return_value=self.home
        )
        home_patcher.start()
        self.addCleanup(home_patcher.stop)

        self.manager = MagicMock()
        self.manager.args = argparse.Namespace(reveal=False)
        self.service = ConfigService(self.manager)

    def _run(self, *args):
        """Runs cmd_config with stdout and stderr genuinely captured."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.service.cmd_config(*args)
        return out.getvalue(), err.getvalue()

    def test_the_listing_does_not_print_the_secrets(self):
        out, _ = self._run()
        self.assertNotIn("AIzaSyTHIS_MUST_NOT_BE_PRINTED", out)
        self.assertNotIn("2abcTHIS_MUST_NOT_BE_PRINTED", out)

    def test_the_listing_still_shows_the_keys_and_ordinary_values(self):
        """Masking must not make the command useless.

        The key names stay visible so a user can see what is configured; only
        the values go.
        """
        out, _ = self._run()
        self.assertIn("gemini_api_key", out)
        self.assertIn("ngrok_authtoken", out)
        self.assertIn("example.test", out, "non-credential values still print")

    def test_getting_a_credential_key_refuses_and_exits_non_zero(self):
        """A silent empty result would be worse than the leak.

        `TOKEN=$(ldm config gemini_api_key)` must fail loudly rather than bind
        an empty string and break somewhere unrelated.
        """
        with self.assertRaises(SystemExit) as caught:
            self._run("gemini_api_key")
        self.assertNotEqual(0, caught.exception.code)

    def test_the_refusal_names_the_way_out(self):
        out, err = self._run_expecting_exit("gemini_api_key")
        combined = out + err
        self.assertNotIn("AIzaSyTHIS_MUST_NOT_BE_PRINTED", combined)
        self.assertIn("--reveal", combined, "the refusal must say how to proceed")

    def _run_expecting_exit(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with contextlib.suppress(SystemExit):
                self.service.cmd_config(*args)
        return out.getvalue(), err.getvalue()

    def test_reveal_prints_the_value(self):
        """The escape hatch has to work, or people will cat ~/.ldmrc instead."""
        self.manager.args = argparse.Namespace(reveal=True)
        out, _ = self._run("gemini_api_key")
        self.assertIn("AIzaSyTHIS_MUST_NOT_BE_PRINTED", out)

    def test_getting_an_ordinary_key_is_unchanged(self):
        """`ldm config share_domain` is a documented scripting mechanism."""
        out, _ = self._run("share_domain")
        self.assertEqual("example.test", out.strip())


if __name__ == "__main__":
    unittest.main()
