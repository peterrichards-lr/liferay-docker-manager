r"""LDM-#2175: the redirect rewriter must match what its own guard admits.

`_rewrite_oauth_urls_in_zip` guards redirect URIs on `"localhost" in uri` and
then acts with `re.sub(r"https?://localhost:\d+", ...)`. The guard admits a
portless URI -- `http://localhost/o/oauth2/redirect`, which is what a client
extension fronted by a proxy on :80 declares -- and the action cannot touch it.
The URI is appended unchanged, `modified` is never set, so the `buildTimestamp`
bump is skipped too and the zip is never repacked. Nothing is reported.

The same regex appears a second time for the `typeSettings` string form, where
the asymmetry inverts: `modified = True` is set *unconditionally* after the
substitution, so that branch claims a rewrite it may not have made, repacking
the zip and bumping the timestamp for nothing.

Both are one defect -- a guard and an action that disagree about what they
match -- and the regex is only how it surfaced this time.
`TheRewriteSaysWhenItLeavesAUrlBehind` pins the general case: a URL this
rewrite is responsible for that still points at localhost afterwards is
reported, rather than passed over in silence.

**No reported failure is attributed to this.** An earlier draft of the issue
cited another team's OAuth failure; that was withdrawn once their configuration
was read -- their redirect URIs are relative, so this branch is never entered.
"""

import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml

from ldm_core.tests.test_workspace import MockWorkspaceManager

# external_url resolves to "http://my-ext.my-host.local": no root_dir means no
# meta, so no `port_my-ext`, and the non-SSL branch omits the port entirely.
EXTERNAL = "http://my-ext.my-host.local"

REDIRECT_CX = """
    my-app:
      type: oAuthApplicationHeadlessServer
      buildTimestamp: 1
      redirectURIs:
        - {uri}
"""

TYPE_SETTINGS_CX = """
    my-app:
      type: oAuthApplicationUserAgent
      buildTimestamp: 1
      typeSettings:
        - redirectURIs={uri}
"""


class RewriteCase(unittest.TestCase):
    def setUp(self):
        self.manager = MockWorkspaceManager()

    def rewrite(self, cx_yaml, host_name="my-host.local", ext_name="my-ext"):
        """Repack a one-file CX zip through the rewrite; return (config, UI)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / f"{ext_name}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("client-extension.yaml", textwrap.dedent(cx_yaml))

            with patch("ldm_core.workspace.utils.UI") as mock_ui:
                self.manager.workspace._rewrite_oauth_urls_in_zip(
                    zip_path, host_name, ext_name
                )

            with zipfile.ZipFile(zip_path, "r") as zf:
                data = yaml.safe_load(zf.read("client-extension.yaml").decode("utf-8"))
        return data["my-app"], mock_ui

    @staticmethod
    def warnings(mock_ui):
        return " ".join(
            str(c.args[0]) for c in mock_ui.warning.call_args_list if c.args
        )


class ThePortIsOptional(RewriteCase):
    def test_a_portless_redirect_uri_is_rewritten(self):
        block, _ = self.rewrite(
            REDIRECT_CX.format(uri="http://localhost/o/oauth2/redirect")
        )
        self.assertEqual(block["redirectURIs"], [f"{EXTERNAL}/o/oauth2/redirect"])

    def test_a_ported_redirect_uri_is_still_rewritten(self):
        block, _ = self.rewrite(REDIRECT_CX.format(uri="http://localhost:8080/cb"))
        self.assertEqual(block["redirectURIs"], [f"{EXTERNAL}/cb"])

    def test_a_uri_that_never_mentions_localhost_is_untouched(self):
        block, _ = self.rewrite(REDIRECT_CX.format(uri="https://example.com/cb"))
        self.assertEqual(block["redirectURIs"], ["https://example.com/cb"])

    def test_the_timestamp_is_bumped_when_only_a_portless_uri_changed(self):
        # The second half of the defect. `modified` stayed False on exactly the
        # path that failed, so the bump that forces Liferay to re-evaluate the
        # extension over a snapshot DB never fired -- and the zip was never
        # repacked, discarding the rewrite even where one had been made.
        block, _ = self.rewrite(REDIRECT_CX.format(uri="http://localhost/cb"))
        self.assertNotEqual(block["buildTimestamp"], 1)

    def test_a_portless_uri_in_typesettings_is_rewritten(self):
        block, _ = self.rewrite(TYPE_SETTINGS_CX.format(uri="http://localhost/cb"))
        self.assertEqual(block["typeSettings"], [f"redirectURIs={EXTERNAL}/cb"])

    def test_the_siblings_replace_wholesale_and_so_have_no_port_hole(self):
        # The audit the issue asks for. `.serviceAddress` and `homePageURL`
        # pair the same substring guard with a WHOLESALE replacement rather
        # than a regex, so the portless form was never at risk there. Pinned so
        # a later tidy-up that "unifies" them onto the shared helper cannot
        # reintroduce the hole it was written to close.
        block, _ = self.rewrite("""
            my-app:
              type: oAuthApplicationHeadlessServer
              buildTimestamp: 1
              .serviceAddress: http://localhost
              homePageURL: http://localhost
        """)
        self.assertEqual(block[".serviceAddress"], "my-ext.my-host.local")
        self.assertEqual(block["homePageURL"], EXTERNAL)


class TheModifiedFlagMeansSomethingActuallyChanged(RewriteCase):
    """`typeSettings` set it unconditionally; the list form under-set it."""

    def test_typesettings_does_not_claim_a_rewrite_it_did_not_make(self):
        # `app.localhost.example.com` is a real host that merely contains the
        # label. It satisfies both substring guards and matches neither
        # substitution, so before the fix this repacked the zip and bumped the
        # timestamp while changing nothing at all.
        block, _ = self.rewrite(
            TYPE_SETTINGS_CX.format(uri="https://app.localhost.example.com/cb")
        )
        self.assertEqual(
            block["typeSettings"], ["redirectURIs=https://app.localhost.example.com/cb"]
        )
        self.assertEqual(block["buildTimestamp"], 1)

    def test_a_bare_label_is_not_mistaken_for_the_host(self):
        block, _ = self.rewrite(
            REDIRECT_CX.format(uri="https://app.localhost.example.com/cb")
        )
        self.assertEqual(
            block["redirectURIs"], ["https://app.localhost.example.com/cb"]
        )


class TheRewriteSaysWhenItLeavesAUrlBehind(RewriteCase):
    """The general defect, of which the regex was one instance.

    A guard that matches a value the action cannot touch is a silent no-op, and
    silence is why this went unnoticed. The rewrite now checks its own outcome
    -- did anything it is responsible for still point at localhost? -- which is
    a question no guard can answer about itself.
    """

    def test_it_reports_a_url_it_could_not_rewrite(self):
        _, ui = self.rewrite(
            REDIRECT_CX.format(uri="https://app.localhost.example.com/cb")
        )
        self.assertIn("app.localhost.example.com", self.warnings(ui))

    def test_it_is_silent_when_every_url_was_rewritten(self):
        _, ui = self.rewrite(REDIRECT_CX.format(uri="http://localhost/cb"))
        self.assertEqual(self.warnings(ui), "")

    def test_it_is_silent_when_localhost_is_the_intended_destination(self):
        # host_name="localhost" resolves external_url to a localhost URL, so a
        # URL still naming localhost afterwards is correct, not stranded.
        # Without this the warning would fire on every ordinary local import.
        _, ui = self.rewrite(
            REDIRECT_CX.format(uri="http://localhost:8080/cb"), host_name="localhost"
        )
        self.assertEqual(self.warnings(ui), "")

    def test_it_ignores_blocks_the_rewrite_never_touches(self):
        # Only the two oAuth types are rewritten, so a localhost URL in any
        # other block is none of this function's business and must not be
        # reported as something it failed to do.
        _, ui = self.rewrite("""
            my-app:
              type: oAuthApplicationHeadlessServer
              buildTimestamp: 1
              redirectURIs:
                - http://localhost/cb
            my-widget:
              type: customElement
              homePageURL: http://localhost:3000/widget
        """)
        self.assertEqual(self.warnings(ui), "")
