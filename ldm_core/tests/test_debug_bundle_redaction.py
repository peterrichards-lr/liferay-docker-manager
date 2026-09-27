"""LDM-#1974: the "sanitized" debug bundle shipped live credentials.

`ldm system doctor --bundle` is documented as producing a *sanitized* zip of
logs and config, explicitly for attaching to support tickets and GitHub issues.
It wrote:

* `~/.ldmrc` -- global config, including `ngrok_authtoken` and any stored API key
* every project's `meta` -- `admin_password`, `db_password` and similar
* the lfr-tunnel token, **verbatim**, via `z.write` with no redaction attempted

The first two went through `UI.redact`, which is a **no-op on JSON**: it matches
`KEY=value` with no spaces, and these files are `"key": "value"`. So the name of
the feature told the user it was safe to share and the content was not.

Same root as LDM-#1970, where `ldm config` printed the same values for the same
reason -- a secret is identifiable from its KEY, never from the rendered text.
"""

import json
import unittest

from ldm_core.ui import UI
from ldm_core.utils import redact_json_text

# The shape of a real ~/.ldmrc and a real project meta, with sentinels whose
# only job is to be searched for.
# Not credentials -- self-describing sentinels whose only job is to be searched
# for in the output. detect-secrets flags them on the KEY name, which is the
# same heuristic this module exists to test.
LDMRC = {
    "ngrok_authtoken": "SENTINEL_TUNNEL_TOKEN",  # pragma: allowlist secret
    "gemini_api_key": "SENTINEL_API_KEY",  # pragma: allowlist secret
    "share_domain": "example.test",
}
META = {
    "container_name": "proj",
    "admin_password": "SENTINEL_ADMIN_PW",  # pragma: allowlist secret
    "db_password": "SENTINEL_DB_PW",  # pragma: allowlist secret
    "port": 8080,
    "credentials": [
        {
            "admin_password": "SENTINEL_NESTED_PW",  # pragma: allowlist secret
            "user": "test@x.test",
        }
    ],
}


class TestUiRedactCannotSanitiseTheseFiles(unittest.TestCase):
    """The reason the bundle needed a different function, pinned.

    Without this, someone reverts to `UI.redact` because it *looks* like the
    redaction helper and the bundle silently ships credentials again.
    """

    def test_redact_is_a_noop_on_ldmrc(self):
        raw = json.dumps(LDMRC)
        self.assertIn("SENTINEL_TUNNEL_TOKEN", UI.redact(raw))
        self.assertIn("SENTINEL_API_KEY", UI.redact(raw))

    def test_redact_is_a_noop_on_meta(self):
        raw = json.dumps(META)
        self.assertIn("SENTINEL_ADMIN_PW", UI.redact(raw))


class TestJsonRedactionRemovesTheValues(unittest.TestCase):
    def test_top_level_credentials_go(self):
        out = redact_json_text(json.dumps(LDMRC))
        self.assertNotIn("SENTINEL_TUNNEL_TOKEN", out)
        self.assertNotIn("SENTINEL_API_KEY", out)

    def test_nested_credentials_go(self):
        """`meta["credentials"]` is a list of dicts, so a top-level-only pass
        would leave the interesting ones behind."""
        out = redact_json_text(json.dumps(META))
        for sentinel in ("SENTINEL_ADMIN_PW", "SENTINEL_DB_PW", "SENTINEL_NESTED_PW"):
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, out)

    def test_the_bundle_stays_useful(self):
        """A bundle with everything stripped diagnoses nothing. Keys remain, and
        non-credential values remain."""
        out = redact_json_text(json.dumps(META))
        self.assertIn("container_name", out)
        self.assertIn("proj", out)
        self.assertIn("8080", out)
        self.assertIn("admin_password", out, "the key should still be visible")

    def test_it_is_still_valid_json(self):
        """The bundle is read by people and sometimes by tooling."""
        out = redact_json_text(json.dumps(META))
        self.assertEqual("[REDACTED]", json.loads(out)["admin_password"])

    def test_a_non_json_document_is_returned_unchanged(self):
        """Deliberate: returning something that looks processed would be worse
        than returning the input. A caller handling a non-JSON file must decide
        separately rather than assume this sanitised it.
        """
        self.assertEqual("not json", redact_json_text("not json"))
        self.assertEqual("", redact_json_text(""))


class TestTheBundleActuallyCallsIt(unittest.TestCase):
    """Crosses the seam between the helper and its caller.

    Written after a neuter probe: reverting `doctor.py` to `UI.redact` left
    every other test in this module green, because they exercise
    `redact_json_text` directly and never check that the bundle uses it. That is
    the same shape as LDM-#1987, where 74 port assertions passed while the
    value never reached the composer.
    """

    def _bundle_source(self):
        import inspect

        from ldm_core.diagnostics import doctor

        src = inspect.getsource(doctor)
        start = src.index("# 2. Config Files")
        return src[start : start + 2200]

    def test_ldmrc_goes_through_the_json_redactor(self):
        src = self._bundle_source()
        self.assertIn("redact_json_text(ldmrc.read_text())", src)
        self.assertNotIn(
            "UI.redact(ldmrc.read_text())",
            src,
            "UI.redact is a no-op on JSON; the bundle ships the token and any "
            "API key in plaintext (LDM-#1974)",
        )

    def test_project_meta_goes_through_the_json_redactor(self):
        src = self._bundle_source()
        self.assertIn("redact_json_text(meta_file.read_text())", src)
        self.assertNotIn(
            "UI.redact(meta_file.read_text())",
            src,
            "UI.redact is a no-op on JSON; the bundle ships admin_password and "
            "db_password in plaintext (LDM-#1974)",
        )


class TestTheBundleDoesNotShipTheTunnelToken(unittest.TestCase):
    """The worst of the three: written with `z.write`, so unlike the JSON above
    no redaction was even attempted -- a live credential file copied whole."""

    def test_the_token_file_is_not_added_verbatim(self):
        import inspect

        from ldm_core.diagnostics import doctor

        src = inspect.getsource(doctor)
        self.assertNotIn(
            'z.write(\n                global_ldm_dir / "lfr-tunnel" / "token"',
            src,
        )
        self.assertNotIn(
            '"ldm_config/lfr-tunnel/token"\n',
            src,
            "the token path is being written as a file entry again (LDM-#1974)",
        )

    def test_its_presence_is_still_reported(self):
        """Absence of the value, not absence of the fact. Whether a tunnel is
        configured is the part that helps diagnose anything."""
        import inspect

        from ldm_core.diagnostics import doctor

        src = inspect.getsource(doctor)
        self.assertIn("a tunnel token is present at", src)


if __name__ == "__main__":
    unittest.main()
