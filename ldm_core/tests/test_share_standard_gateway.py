"""LDM-#2191: `share start` announced a URL it had not got, and offered no way
to ask for the standard gateway.

Two defects, reported together.

**The announcement was never reconciled.** `cmd_start` prints a URL composed
from configuration, starts the tunnel, resolves the address it actually got,
and prints that too -- leaving two contradictory URLs on screen with nothing
saying the first did not happen:

```text
ℹ  Public URL: https://peters.dev.solaramoto.com
✅  🌍 Public Tunnel Active: https://peters.lfr-demo.se
```

An unregistered custom domain falling back to the standard gateway is correct
behaviour; the silence is not. Same shape as LDM-#2155, #2171 and #2188 -- the
verdict is computed and the earlier claim is never withdrawn.

**And there was no way to say "use the standard gateway".** The domain is
sticky: held in the project's meta and in `~/.ldmrc`, so
`ldm share start <project> --subdomain peters` silently reuses a custom domain
pinned at some point in the past. `--domain` could name a different domain but
could not name the standard one, so the only recourse was editing meta or
`~/.ldmrc` by hand.

Reported by the maintainer as *"Why is it talking about my custom domain when
I have said I want to use a standard subdomain?"* -- for which the answer was
"because you cannot say that".
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.share import ShareService
from ldm_core.tests.test_share import MockManager

CUSTOM = "dev.solaramoto.com"


class ShareCase(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.service = ShareService(self.manager)
        self.service._poll_tunnel_health = MagicMock(return_value=(True, None))  # type: ignore[method-assign]
        self.service._resolve_existing_binary = MagicMock(return_value=None)  # type: ignore[method-assign]
        self.service._get_tunnel_api_state = MagicMock(return_value={})  # type: ignore[method-assign]
        self.standard = self.service.get_default_tunnel_domain()

    def set_global_config(self, **values):
        self.manager.config.get_global_config = MagicMock(return_value=values)  # type: ignore[method-assign]

    @staticmethod
    def said(mock_ui, channel):
        calls = getattr(mock_ui, channel).call_args_list
        return " ".join(str(c.args[0]) for c in calls if c.args)


class TheStandardGatewayCanBeAskedFor(ShareCase):
    """`--domain default` -- the second defect, and the one the user hit."""

    def resolve(self, domain, project_meta=None):
        with patch("ldm_core.handlers.share.UI") as mock_ui:
            _provider, resolved = self.service.resolve_share_config(
                project_meta=project_meta, provider="lfr-tunnel", domain=domain
            )
        return resolved, mock_ui

    def test_it_selects_the_standard_gateway(self):
        resolved, _ = self.resolve("default")
        self.assertEqual(resolved, self.standard)

    def test_standard_is_accepted_as_well(self):
        resolved, _ = self.resolve("standard")
        self.assertEqual(resolved, self.standard)

    def test_it_is_not_case_sensitive(self):
        resolved, _ = self.resolve("  Default  ")
        self.assertEqual(resolved, self.standard)

    def test_it_overrides_a_custom_domain_stored_in_project_meta(self):
        # The reported case. The domain is sticky, so without this the flag
        # had no way to displace a value pinned by an earlier invocation.
        resolved, _ = self.resolve("default", project_meta={"share_domain": CUSTOM})
        self.assertEqual(resolved, self.standard)

    def test_it_overrides_a_custom_domain_stored_in_ldmrc(self):
        self.set_global_config(share_domain=CUSTOM)
        resolved, _ = self.resolve("default")
        self.assertEqual(resolved, self.standard)

    def test_the_sentinel_is_never_treated_as_a_vanity_domain(self):
        # `default` is not a registrable domain. Reaching the LDM-#1038 note
        # would tell the user to go and register it in the tunnel portal.
        _, mock_ui = self.resolve("default")
        self.assertNotIn("custom domain", self.said(mock_ui, "info"))

    def test_it_says_the_standard_gateway_was_asked_for(self):
        # The provenance line (LDM-#2019) exists so the user can see WHY an
        # address was chosen. A sentinel that resolved silently would be the
        # same invisibility this flag was added to cure.
        self.resolve("default")
        self.assertIn("--domain default", self.service._share_domain_source or "")

    def test_a_real_domain_is_still_honoured(self):
        resolved, _ = self.resolve(CUSTOM)
        self.assertEqual(resolved, CUSTOM)

    def test_a_known_base_domain_is_still_honoured(self):
        resolved, _ = self.resolve("lfr-demo.se")
        self.assertEqual(resolved, "lfr-demo.se")


class TheAnnouncedUrlIsReconciled(ShareCase):
    """The first defect: two URLs printed, neither reconciled with the other."""

    def start(self, domain, resolved_url):
        self.service._ensure_binary = MagicMock(return_value=Path("/fake/lfr-tunnel"))  # type: ignore[method-assign]
        self.service._get_auth_token = MagicMock(return_value="my-token")  # type: ignore[method-assign]
        self.service._verify_compatibility = MagicMock()  # type: ignore[method-assign]
        self.service._get_installed_version = MagicMock(return_value="1.0.0")  # type: ignore[method-assign]
        self.service.resolve_public_tunnel_url = MagicMock(return_value=resolved_url)  # type: ignore[method-assign]

        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        with patch("subprocess.run", return_value=result):
            with patch("ldm_core.handlers.share.UI") as mock_ui:
                self.service.cmd_start(subdomain="peters", domain=domain)
        return mock_ui

    def test_it_says_when_the_tunnel_came_up_somewhere_else(self):
        mock_ui = self.start(CUSTOM, f"https://peters.{self.standard}")
        warned = self.said(mock_ui, "warning")
        self.assertIn(f"peters.{self.standard}", warned)
        self.assertIn(f"peters.{CUSTOM}", warned)

    def test_it_explains_why_a_custom_domain_did_not_take(self):
        mock_ui = self.start(CUSTOM, f"https://peters.{self.standard}")
        guidance = self.said(mock_ui, "warning") + self.said(mock_ui, "hint")
        self.assertIn(CUSTOM, guidance)
        self.assertIn("--domain default", guidance)

    def test_it_is_silent_when_the_tunnel_came_up_where_announced(self):
        mock_ui = self.start(CUSTOM, f"https://peters.{CUSTOM}")
        self.assertEqual(self.said(mock_ui, "warning"), "")

    def test_it_is_silent_on_the_standard_gateway(self):
        # The ordinary case. A warning here would fire on every normal share.
        mock_ui = self.start("default", f"https://peters.{self.standard}")
        self.assertEqual(self.said(mock_ui, "warning"), "")

    def test_the_active_url_is_still_the_resolved_one(self):
        # Reconciling must not change WHICH url is announced as live -- the
        # resolved one is the true one and remains the headline.
        mock_ui = self.start(CUSTOM, f"https://peters.{self.standard}")
        active = self.said(mock_ui, "success")
        self.assertIn(f"peters.{self.standard}", active)
        self.assertNotIn(f"peters.{CUSTOM}", active)


class AskingForTheStandardGatewayIsRecorded(ShareCase):
    """What `--domain default` does to stored state, measured rather than assumed.

    The hint printed on a fallback tells the user this flag replaces the stored
    custom domain. That is a claim about `write_meta`, so it is pinned here --
    a message promising a side effect nobody verified is how LDM-#986 and
    LDM-#1833 each started.
    """

    def start_with_stored_domain(self, domain):
        written: dict = {}
        root = Path("/fake/proj")
        self.manager.detect_project_path = lambda _project_id=None: root  # type: ignore[method-assign]
        self.manager.read_meta = lambda _root: {"share_domain": CUSTOM}  # type: ignore[method-assign]
        self.manager.write_meta = lambda _root, meta: written.update(meta)  # type: ignore[method-assign]

        self.service._ensure_binary = MagicMock(return_value=Path("/fake/lfr-tunnel"))  # type: ignore[method-assign]
        self.service._get_auth_token = MagicMock(return_value="my-token")  # type: ignore[method-assign]
        self.service._verify_compatibility = MagicMock()  # type: ignore[method-assign]
        self.service._get_installed_version = MagicMock(return_value="1.0.0")  # type: ignore[method-assign]
        self.service.resolve_public_tunnel_url = MagicMock(  # type: ignore[method-assign]
            return_value=f"https://peters.{self.standard}"
        )

        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        with patch("subprocess.run", return_value=result):
            with patch("ldm_core.handlers.share.UI"):
                self.service.cmd_start(subdomain="peters", domain=domain)
        return written

    def test_it_replaces_the_custom_domain_stored_for_the_project(self):
        written = self.start_with_stored_domain("default")
        self.assertEqual(written.get("share_domain"), self.standard)

    def test_an_ordinary_run_still_stores_what_was_resolved(self):
        written = self.start_with_stored_domain(CUSTOM)
        self.assertEqual(written.get("share_domain"), CUSTOM)
