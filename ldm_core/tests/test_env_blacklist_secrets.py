"""Credential-shaped host variables must not reach containers (LDM-#1910).

`LDM_`-prefix stripping forwards every `LDM_`-prefixed variable into every
container with the prefix removed, and the documentation calls that "the
recommended way to inject global configurations" -- so users put things there.
Nothing then distinguishes a config value from a credential: a developer's
GitHub PAT, exported as `LDM_BOT_PAT` for an unrelated tool, was observed
being forwarded as `BOT_PAT`.

These assert the OUTCOME -- what `get_host_passthrough_env` returns -- not that
a pattern string appears in a file. A test that greps `env-blacklist.txt` for
`*_SECRET` passes while the matching is broken.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.constants import SCRIPT_DIR
from ldm_core.utils import is_env_var_blacklisted, load_env_blacklist

SHIPPED = load_env_blacklist(SCRIPT_DIR / "common" / "env-blacklist.txt")

_SAID: list[str] = []


def _capture(message, *_args, **_kwargs):
    """Records a UI.detail line. A named function, not a lambda: ruff
    flags the unused *args/**kwargs a lambda would need here."""
    _SAID.append(message)


def _forwarded(env):
    """Keys `get_host_passthrough_env` would put into a container."""
    from ldm_core.workspace import metadata as m

    ws = MagicMock()
    ws.scan_client_extensions.return_value = []
    ws.scan_standalone_services.return_value = []
    paths = {
        "root": Path("/tmp/ldm-1910-none"),
        "cx": Path("/tmp/ldm-1910-none/cx"),
        "ce_dir": Path("/tmp/ldm-1910-none/ce"),
    }
    with patch.dict(os.environ, env, clear=True):
        with patch("ldm_core.ui.UI.detail"):
            return {v.split("=", 1)[0] for v in m.get_host_passthrough_env(ws, paths)}


class TestCredentialsAreWithheld(unittest.TestCase):
    def test_an_ldm_prefixed_token_does_not_reach_a_container(self):
        """The observed case: LDM_BOT_PAT arriving as BOT_PAT."""
        self.assertNotIn("BOT_PAT", _forwarded({"LDM_BOT_PAT": "ghp_x"}))

    def test_credential_shapes_are_blocked(self):
        for name in (
            "LDM_API_SECRET",
            "LDM_DB_PASSWORD",
            "LDM_GH_TOKEN",
            "LDM_SIGNING_PRIVATE_KEY",
            "LDM_AWS_ACCESS_KEY",
            "LDM_SVC_CREDENTIALS",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name[4:], _forwarded({name: "x"}))

    def test_blocking_applies_to_a_configured_passthrough_prefix_too(self):
        """A credential is a credential however it was asked for.

        Naming a prefix in LDM_FORWARD_PREFIXES is not evidence the user meant
        to ship a secret into a third-party container image.
        """
        got = _forwarded(
            {
                "LDM_FORWARD_PREFIXES": "LIFERAY_",
                "LIFERAY_OAUTH_CLIENT_SECRET": "fake",  # pragma: allowlist secret
                "LIFERAY_OAUTH_CLIENT_ID": "abc",
                "LIFERAY_API_URL": "https://x",
            }
        )
        self.assertNotIn("LIFERAY_OAUTH_CLIENT_SECRET", got)
        self.assertIn("LIFERAY_OAUTH_CLIENT_ID", got, "a client id is not a secret")
        self.assertIn("LIFERAY_API_URL", got, "a URL is not a secret")


class TestTheDocumentedFeaturesStillWork(unittest.TestCase):
    """A guard that breaks documented behaviour gets deleted within the week."""

    def test_ai_provider_keys_still_forward(self):
        """`*_API_KEY` would kill the passthrough prefixes that exist to carry
        exactly these -- the shipped negations are what keep it working."""
        for name in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "MISTRAL_API_KEY",
        ):
            with self.subTest(name=name):
                self.assertIn(name, _forwarded({name: "k"}))

    def test_ordinary_configuration_is_unaffected(self):
        self.assertIn("COMPANY_ID", _forwarded({"LDM_COMPANY_ID": "123"}))


class TestTheEscapeHatch(unittest.TestCase):
    """Without this a blanket block turns a silent leak into a silent breakage,
    with no recourse: `_get_effective_blacklist` concatenates the shipped list
    with the project's, so before LDM-#1910 a project could only ever add.
    """

    def test_a_negation_restores_a_blocked_variable(self):
        self.assertTrue(is_env_var_blacklisted("MY_TOKEN", ["*_TOKEN"]))
        self.assertFalse(is_env_var_blacklisted("MY_TOKEN", ["*_TOKEN", "!MY_TOKEN"]))

    def test_a_negation_can_be_a_glob(self):
        self.assertFalse(is_env_var_blacklisted("ACME_TOKEN", ["*_TOKEN", "!ACME_*"]))

    def test_a_negation_does_not_leak_to_other_names(self):
        self.assertTrue(is_env_var_blacklisted("OTHER_TOKEN", ["*_TOKEN", "!MY_TOKEN"]))

    def test_the_shipped_list_blocks_and_permits_as_intended(self):
        for name, blocked in (
            ("LDM_BOT_PAT", True),
            ("GITHUB_TOKEN", True),
            ("AWS_SECRET_ACCESS_KEY", True),
            ("LIFERAY_OAUTH_CLIENT_SECRET", True),
            ("OPENAI_API_KEY", False),
            ("LIFERAY_API_URL", False),
            ("LIFERAY_OAUTH_CLIENT_ID", False),
        ):
            with self.subTest(name=name):
                self.assertEqual(blocked, is_env_var_blacklisted(name, SHIPPED))


class TestTheUserIsTold(unittest.TestCase):
    """A dropped variable was indistinguishable from one never set, which is
    how LDM-#1903 cost an external team days."""

    def _notice(self, env):
        from ldm_core.workspace import metadata as m

        ws = MagicMock()
        ws.scan_client_extensions.return_value = []
        ws.scan_standalone_services.return_value = []
        paths = {
            "root": Path("/tmp/ldm-1910-none"),
            "cx": Path("/tmp/ldm-1910-none/cx"),
            "ce_dir": Path("/tmp/ldm-1910-none/ce"),
        }
        _SAID.clear()
        with patch.dict(os.environ, env, clear=True):
            with patch("ldm_core.ui.UI.detail", side_effect=_capture):
                m.get_host_passthrough_env(ws, paths)
        return " ".join(_SAID)

    def test_a_withheld_variable_is_named(self):
        said = self._notice({"LDM_BOT_PAT": "ghp_secret_value"})
        self.assertIn("LDM_BOT_PAT", said)

    def test_the_value_is_never_printed(self):
        """Withholding a credential is not served by printing it."""
        said = self._notice({"LDM_BOT_PAT": "ghp_secret_value"})
        self.assertNotIn("ghp_secret_value", said)

    def test_the_remedy_is_named(self):
        said = self._notice({"LDM_BOT_PAT": "x"})
        self.assertIn("env-blacklist.txt", said)

    def test_the_exact_negation_line_is_given_not_a_placeholder(self):
        """The users most likely to see this are the ones whose working setup
        just stopped. Making them derive the syntax from an example is friction
        at exactly the wrong moment."""
        said = self._notice({"LDM_BOT_PAT": "x"})
        self.assertIn("!LDM_BOT_PAT", said)
        self.assertNotIn("!MY_VAR", said)

    def test_nothing_is_said_when_nothing_is_withheld(self):
        self.assertEqual("", self._notice({"LDM_COMPANY_ID": "123"}))


if __name__ == "__main__":
    unittest.main()
