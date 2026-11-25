"""Per-project custom share domains (LDM-#2503).

v2.26.16-pre.1 passed a vanity `share_domain` -- usually a machine-wide value
from ~/.ldmrc -- to every share as `-domain`, while announcing and pinning
`<subdomain>.<domain>`. lfr-tunnel serves a custom domain as the WHOLE host,
one tunnel at a time, and before v1.52.12 its `-status-json` could not find
such a tunnel, so LDM stopped working shares. pre.3 reverted it.

These tests drive `cmd_start`, the health check and the composer, and assert
what crosses the boundary: the argv the client gets, the meta LDM writes, what
the user is told, and the `web.server.host` Liferay is given. Nothing executes
a binary; `subprocess.run` is recorded, never run. The payloads are shaped on
lfr-tunnel's own `StatusOutput` (pkg/client/state.go), `/api/info`
(pkg/client/inspector.go) and `-background` refusal report
(cmd/lfr-tunnel/background_wait.go).
"""

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetNode
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.share import ShareService
from ldm_core.handlers.share_custom_domain import (
    client_supports_custom_domain,
    custom_domain_refusal,
    public_url_for_custom_domain,
)
from ldm_core.tests import test_share

CUSTOM = "dev.solaramoto.com"


def _exit(*_args, exit_code=1, **_kwargs):
    """What UI.die does once UI is patched out: leave."""
    raise SystemExit(exit_code)


def _completed(returncode=0, stdout="", stderr=""):
    res = MagicMock()
    res.returncode = returncode
    res.stdout = stdout
    res.stderr = stderr
    return res


# What `-background` prints on stderr when the gateway refuses (v1.52.11+).
REFUSED_TAKEN = (
    "[Error] The background tunnel for subdomain 'demo' did not start "
    "(exit status 1):\n"
    "  2026/11/25 10:00:01 INFO [Warning] Registration on region 'eu' failed "
    "(attempt 1/3): gateway error (409): Domain is already taken\n"
    "  2026/11/25 10:00:04 INFO [Error] Failed to register: gateway error "
    "(409): Domain is already taken\n"
    "[Client] Full log: /h/.lfr-tunnel/logs/client-demo.log\n"
)
REFUSED_UNREGISTERED = (
    "[Error] The background tunnel for subdomain 'demo' did not start "
    "(exit status 1):\n"
    "  2026/11/25 10:00:01 INFO [Error] Failed to register: gateway error "
    "(403): this custom domain is not registered to you -- register it in the "
    "portal, beside your subdomain reservations, then connect again\n"
    "  2026/11/25 10:00:01 INFO [Client] Subdomain reservation or limit issue "
    "detected.\n"
    "[Client] Full log: /h/.lfr-tunnel/logs/client-demo.log\n"
)


class _Start(unittest.TestCase):
    """A native start with a project, the client and the gateway stubbed."""

    service: Any
    mock_manager: Any

    def setUp(self):
        test_share.TestShareTunnelTopology.setUp(self)  # type: ignore[arg-type]
        self.service._get_installed_version.return_value = "1.52.12"
        self.meta: dict = {}
        self.written: dict = {}
        self.root = Path("/fake/projects/proj")
        self.mock_manager.detect_project_path = lambda _p=None: self.root  # type: ignore[method-assign]
        self.mock_manager.read_meta = lambda _r: dict(self.meta)  # type: ignore[method-assign]
        self.mock_manager.write_meta = lambda _r, m: self.written.update(m)  # type: ignore[method-assign]
        cred = patch(
            "ldm_core.handlers.share_credentials.assert_default_credentials_acceptable"
        )
        cred.start()
        self.addCleanup(cred.stop)

    def start(self, mock_run, run_result=None, **kwargs):
        mock_run.return_value = run_result or _completed()
        with patch("ldm_core.handlers.share.UI") as ui:
            ui.die.side_effect = _exit
            self.service.cmd_start(**kwargs)
        return ui

    @staticmethod
    def argv(mock_run):
        (cmd,) = [
            c.args[0]
            for c in mock_run.call_args_list
            if c.args and "-background" in c.args[0]
        ]
        return cmd

    @staticmethod
    def said(ui, channel):
        return " ".join(
            str(c.args[0]) for c in getattr(ui, channel).call_args_list if c.args
        )


@patch.dict(os.environ, {}, clear=True)
@patch("subprocess.run")
class ACustomDomainIsRequestedForTheProjectThatNamesIt(_Start):
    def test_a_custom_domain_on_the_command_line_is_passed_as_domain(self, mock_run):
        self.start(mock_run, subdomain="demo", domain=CUSTOM)
        cmd = self.argv(mock_run)
        self.assertEqual(cmd[cmd.index("-domain") + 1], CUSTOM)
        # Still passed: -status-json and -stop find the tunnel by it.
        self.assertEqual(cmd[cmd.index("-subdomain") + 1], "demo")

    def test_it_is_announced_as_the_whole_host(self, mock_run):
        ui = self.start(mock_run, subdomain="demo", domain=CUSTOM)
        self.assertIn(f"https://{CUSTOM}", self.said(ui, "info"))
        self.assertNotIn(f"demo.{CUSTOM}", self.said(ui, "info"))

    def test_it_is_recorded_as_the_projects_own(self, mock_run):
        self.start(mock_run, subdomain="demo", domain=CUSTOM)
        self.assertEqual(self.written.get("share_custom_domain"), CUSTOM)

    def test_a_stored_request_is_honoured_on_the_next_run(self, mock_run):
        self.meta = {"share_custom_domain": CUSTOM, "share_domain": CUSTOM}
        self.start(mock_run, subdomain="demo")
        cmd = self.argv(mock_run)
        self.assertEqual(cmd[cmd.index("-domain") + 1], CUSTOM)

    def test_domain_default_clears_the_stored_request(self, mock_run):
        self.meta = {"share_custom_domain": CUSTOM, "share_domain": CUSTOM}
        self.start(mock_run, subdomain="demo", domain="default")
        self.assertNotIn("-domain", self.argv(mock_run))
        self.assertNotIn("share_custom_domain", self.written)

    def test_a_gateway_base_domain_is_not_a_custom_domain(self, mock_run):
        self.start(mock_run, subdomain="demo", domain="lfr-demo.se")
        self.assertNotIn("-domain", self.argv(mock_run))

    def test_a_host_on_a_gateway_domain_is_not_a_custom_domain(self, mock_run):
        self.start(mock_run, domain="peters.lfr-demo.se")
        self.assertNotIn("-domain", self.argv(mock_run))

    def test_a_base_domain_the_gateway_advertises_is_not_a_custom_domain(
        self, mock_run
    ):
        self.service._advertised_base_domains = MagicMock(  # type: ignore[method-assign]
            return_value=["lfr-demo.se", "lfr-demo.cloud"]
        )
        self.start(mock_run, subdomain="demo", domain="lfr-demo.cloud")
        self.assertNotIn("-domain", self.argv(mock_run))


@patch.dict(os.environ, {}, clear=True)
@patch("subprocess.run")
class ACustomDomainFromSharedConfigurationIsNotRequested(_Start):
    """Every project would ask for it, and the gateway leases it to one."""

    def test_one_in_ldmrc_is_not_passed_and_the_user_is_told(self, mock_run):
        self.mock_manager.config.get_global_config = lambda: {"share_domain": CUSTOM}  # type: ignore[method-assign]
        ui = self.start(mock_run, subdomain="demo")
        self.assertNotIn("-domain", self.argv(mock_run))
        warned = self.said(ui, "warning")
        self.assertIn(CUSTOM, warned)
        self.assertIn(f"--domain {CUSTOM}", warned)
        default = self.service.get_default_tunnel_domain()
        self.assertIn(f"https://demo.{default}", self.said(ui, "info"))

    def test_nor_is_it_copied_into_the_project(self, mock_run):
        self.mock_manager.config.get_global_config = lambda: {"share_domain": CUSTOM}  # type: ignore[method-assign]
        self.start(mock_run, subdomain="demo")
        self.assertNotIn("share_custom_domain", self.written)
        self.assertNotEqual(self.written.get("share_domain"), CUSTOM)

    def test_one_an_earlier_release_copied_into_share_domain_is_not_passed(
        self, mock_run
    ):
        # Every share start before #2503 wrote the resolved domain -- ~/.ldmrc's
        # included -- into the project's share_domain.
        self.meta = {"share_domain": CUSTOM}
        self.start(mock_run, subdomain="demo")
        self.assertNotIn("-domain", self.argv(mock_run))


@patch.dict(os.environ, {}, clear=True)
@patch("subprocess.run")
class ACustomDomainIsRefusedWhereItCannotWork(_Start):
    def test_a_client_older_than_v1_52_12_is_refused_with_an_upgrade_hint(
        self, mock_run
    ):
        self.service._get_installed_version.return_value = "1.52.11"
        mock_run.return_value = _completed()
        with (
            patch("ldm_core.handlers.share.UI") as ui,
            self.assertRaises(SystemExit) as ctx,
        ):
            ui.die.side_effect = _exit
            self.service.cmd_start(subdomain="demo", domain=CUSTOM)
        self.assertEqual(ctx.exception.code, 3)
        (call,) = ui.die.call_args_list
        self.assertIn("v1.52.12", call.args[0])
        self.assertIn("-upgrade", call.kwargs["tip"])
        self.assertFalse(
            [
                c
                for c in mock_run.call_args_list
                if c.args and "-background" in c.args[0]
            ]
        )

    def test_an_older_client_is_fine_without_a_custom_domain(self, mock_run):
        self.service._get_installed_version.return_value = "1.52.11"
        self.start(mock_run, subdomain="demo", domain="lfr-demo.se")
        self.assertNotIn("-domain", self.argv(mock_run))

    def test_the_docker_provider_is_refused(self, mock_run):
        with self.assertRaises(SystemExit) as ctx:
            self.start(
                mock_run, subdomain="demo", domain=CUSTOM, provider="lfr-tunnel-docker"
            )
        self.assertEqual(ctx.exception.code, 3)
        self.assertFalse(self.written)
        self.assertFalse(mock_run.called)


@patch.dict(os.environ, {}, clear=True)
@patch("subprocess.run")
class AGatewayRefusalIsExplained(_Start):
    def _refused(self, mock_run, stderr):
        mock_run.return_value = _completed(returncode=1, stderr=stderr)
        with (
            patch("ldm_core.handlers.share.UI") as ui,
            self.assertRaises(SystemExit) as ctx,
        ):
            ui.die.side_effect = _exit
            self.service.cmd_start(subdomain="demo", domain=CUSTOM)
        self.assertEqual(ctx.exception.code, 3)
        (call,) = ui.die.call_args_list
        return call

    def test_a_taken_domain_says_so_and_quotes_the_gateway(self, mock_run):
        call = self._refused(mock_run, REFUSED_TAKEN)
        self.assertIn("already in use", call.args[0])
        self.assertIn("Domain is already taken", call.kwargs["details"])
        self.assertIn("one tunnel at a time", call.kwargs["tip"])

    def test_a_taken_domain_names_another_project_that_claims_it(self, mock_run):
        other = Path("/fake/projects/other")
        metas = {other: {"share_custom_domain": CUSTOM}, self.root: {}}
        self.mock_manager.find_dxp_roots = lambda: [  # type: ignore[attr-defined]
            {"path": self.root},
            {"path": other},
        ]
        self.mock_manager.read_meta = lambda r: dict(metas.get(r, self.meta))  # type: ignore[method-assign]
        call = self._refused(mock_run, REFUSED_TAKEN)
        self.assertIn("other", call.kwargs["tip"])

    def test_an_unregistered_domain_points_at_the_portal(self, mock_run):
        call = self._refused(mock_run, REFUSED_UNREGISTERED)
        self.assertIn("not registered to you", call.args[0])
        self.assertIn("gateway error (403)", call.kwargs["details"])
        self.assertIn("portal", call.kwargs["tip"])

    def test_any_other_failure_keeps_the_generic_report(self, mock_run):
        call = self._refused(mock_run, "[Error] dial tcp: connection refused\n")
        self.assertEqual(call.args[0], "Tunnel failed to start.")


@patch.dict(os.environ, {}, clear=True)
@patch("subprocess.run")
class ASecondProjectIsWarned(_Start):
    def test_another_project_claiming_the_domain_is_named_before_starting(
        self, mock_run
    ):
        other = Path("/fake/projects/other")
        metas = {other: {"share_custom_domain": CUSTOM}}
        self.mock_manager.find_dxp_roots = lambda: [
            {"path": self.root},
            {"path": other},
        ]  # type: ignore[attr-defined]
        self.mock_manager.read_meta = lambda r: dict(metas.get(r, self.meta))  # type: ignore[method-assign]
        ui = self.start(mock_run, subdomain="demo", domain=CUSTOM)
        warned = self.said(ui, "warning")
        self.assertIn("other", warned)
        self.assertIn("ldm share stop", warned)
        # Warned, not refused: the other project may not be shared right now.
        self.assertIn("-domain", self.argv(mock_run))


class TheHealthCheckIsHonest(unittest.TestCase):
    """`share.py` reported "Connection state is 'connected'" as the reason a
    share failed, and stopped the tunnel, when only the status lookup had."""

    def setUp(self):
        self.service = ShareService(test_share.MockManager())
        self.service._ensure_binary = MagicMock(return_value=Path("/fake/lfr-tunnel"))  # type: ignore[method-assign]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = patch(
            "ldm_core.handlers.share.get_actual_home", return_value=Path(self.tmp.name)
        )
        home.start()
        self.addCleanup(home.stop)

    @staticmethod
    def _info(client_subdomain):
        info = MagicMock()
        info.status_code = 200
        info.json.return_value = {
            "status": "healthy",
            "client_subdomain": client_subdomain,
            "connection": {"state": "connected"},
            "auth": {"valid": True, "error_message": None},
            "subdomain": {
                "requested": client_subdomain,
                "assigned": client_subdomain,
                "leased": True,
                "conflict": False,
                "public_urls": [f"https://{CUSTOM}"],
            },
            "destination": {"host": "127.0.0.1", "port": 8080, "responsive": True},
        }
        return info

    def _poll(self, info):
        # The pre-#2595 lookup: a connected tunnel reported as not running.
        not_found = _completed(stdout='{"running":false}')
        with (
            patch("subprocess.run", return_value=not_found),
            patch("requests.get", return_value=info),
            patch("ldm_core.handlers.share.UI") as ui,
        ):
            ok, reason = self.service._poll_tunnel_health("demo", timeout=0)
        return ok, reason, ui

    def test_a_connected_healthy_tunnel_is_not_declared_failed(self):
        ok, reason, ui = self._poll(self._info("demo"))
        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertIn(
            "-status-json", " ".join(str(c.args[0]) for c in ui.warning.call_args_list)
        )

    def test_another_tunnels_inspector_is_not_taken_for_this_one(self):
        ok, reason, _ui = self._poll(self._info("someone-else"))
        self.assertFalse(ok)
        self.assertIn("Status Lookup Failed", reason)
        self.assertIn("someone-else", reason)
        self.assertNotIn("Connection state is 'connected'", reason)

    def test_a_refusal_in_the_log_is_not_called_a_subdomain_conflict(self):
        log = Path(self.tmp.name) / ".lfr-tunnel" / "logs" / "client-demo.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            "INFO [Error] Failed to register: gateway error (409): Domain is "
            "already taken\n",
            encoding="utf-8",
        )
        with (
            patch("subprocess.run", return_value=_completed(returncode=1)),
            patch("requests.get", side_effect=Exception("refused")),
            patch("ldm_core.handlers.share.UI"),
        ):
            ok, reason = self.service._poll_tunnel_health(
                "demo", timeout=0, custom_domain=CUSTOM
            )
        self.assertFalse(ok)
        self.assertIn(f"Custom domain '{CUSTOM}' is already in use", reason)
        self.assertNotIn("Subdomain Conflict", reason)


class TheStatusIsReadNotComposed(unittest.TestCase):
    # lfr-tunnel StatusOutput for a tunnel started with -subdomain demo -domain.
    STATUS = (
        '{"running":true,"pid":4242,"inspector_port":4040,'
        '"inspector_url":"http://127.0.0.1:4040","subdomain":"demo",'
        f'"public_urls":["https://{CUSTOM}"],"ports":[8080],'
        '"start_time":"2026-11-25T10:00:00Z","region":"eu",'
        '"server_url":"https://tunnel.lfr-demo.se",'
        '"connection_state":"connected","status":"healthy"}'
    )

    def test_the_public_url_comes_from_status_json(self):
        import json

        self.assertEqual(
            public_url_for_custom_domain(json.loads(self.STATUS), CUSTOM),
            f"https://{CUSTOM}",
        )
        self.assertIsNone(
            public_url_for_custom_domain(json.loads(self.STATUS), "other.example")
        )

    def test_the_project_fallback_is_the_whole_host(self):
        manager = test_share.MockManager()
        manager.args.share_domain = None
        manager.args.domain = None
        manager.args.share_provider = None
        manager.detect_project_path = lambda _p=None: Path("/fake/proj")  # type: ignore[method-assign]
        manager.read_meta = lambda _r: {"share_custom_domain": CUSTOM}  # type: ignore[method-assign]
        service = ShareService(manager)
        service.resolve_public_tunnel_urls = MagicMock(return_value=[])  # type: ignore[method-assign]
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                service.resolve_public_tunnel_url("demo", "proj"), f"https://{CUSTOM}"
            )

    def test_the_client_version_gate(self):
        self.assertFalse(client_supports_custom_domain("1.52.11"))
        self.assertTrue(client_supports_custom_domain("1.52.12"))
        self.assertTrue(client_supports_custom_domain("v1.53.0"))
        self.assertFalse(client_supports_custom_domain(None))

    def test_quota_and_quarantine_are_named(self):
        quota = custom_domain_refusal(
            "gateway error (403): Custom domain reservation quota limit reached", CUSTOM
        )
        self.assertIsNotNone(quota)
        self.assertEqual(quota.kind, "quota")  # type: ignore[union-attr]
        held = custom_domain_refusal(
            "gateway error (409): Domain is currently in quarantine", CUSTOM
        )
        self.assertEqual(held.kind, "quarantine")  # type: ignore[union-attr]
        self.assertIsNone(
            custom_domain_refusal("gateway error (401): bad token", CUSTOM)
        )


@patch.dict(os.environ, {}, clear=True)
@patch("subprocess.run")
class ALocalhostNameIsLoopback(_Start):
    """A `*.localhost` name is loopback (RFC 6761), like `localhost` itself."""

    def test_no_target_host_for_a_dot_localhost_name(self, mock_run):
        self.meta = {"host_name": "proj.localhost"}
        self.start(mock_run, subdomain="demo")
        self.assertNotIn("-target-host", self.argv(mock_run))

    def test_a_real_host_name_is_still_the_target(self, mock_run):
        self.meta = {"host_name": "lfr.example.net"}
        self.start(mock_run, subdomain="demo")
        cmd = self.argv(mock_run)
        self.assertEqual(cmd[cmd.index("-target-host") + 1], "lfr.example.net")

    def test_it_does_not_let_the_native_provider_reach_a_remote_node(self, _run):
        service = ShareService(MagicMock())
        remote = TargetNode(name="node-a", host="10.0.1.7")
        with (
            patch("ldm_core.config.get_active_target", return_value=remote),
            patch("ldm_core.handlers.share.UI") as ui,
            self.assertRaises(SystemExit) as ctx,
        ):
            ui.die.side_effect = _exit
            service._assert_provider_can_reach_target(
                "lfr-tunnel", {"target": "node-a", "host_name": "proj.localhost"}, "p"
            )
        self.assertEqual(ctx.exception.code, 3)


class _ComposerManager:
    def __init__(self, args):
        self.args = args
        self.config = MagicMock()
        self.config.get_global_config.return_value = {}
        self.share = ShareService(self)
        self.share._advertised_base_domains = MagicMock(return_value=[])  # type: ignore[method-assign]
        self.share.resolve_public_tunnel_urls = MagicMock(return_value=[])  # type: ignore[method-assign]
        self.non_interactive = True

    def get_resolved_ip(self, _host):
        return "127.0.0.1"


class LiferayIsPinnedToTheCustomDomain(unittest.TestCase):
    def _props(self, meta):
        args = MagicMock()
        args.share = True
        args.share_provider = None
        args.share_subdomain = None
        args.share_domain = None
        args.domain = None
        args.tunnel_managed_cors = False
        manager = _ComposerManager(args)
        composer = ComposerService(manager)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("ldm_core.handlers.share.UI"),
        ):
            composer._inject_liferay_share_env(
                {"root": Path("/fake")},
                {"share": "true", "share_provider": "lfr-tunnel", **meta},
                "localhost",
                "proj",
                False,
                8080,
            )
        return manager.config.update_portal_ext.call_args.args[1]

    def test_web_server_host_is_the_custom_domain(self):
        props = self._props({"share_custom_domain": CUSTOM, "share_domain": CUSTOM})
        self.assertEqual(props["web.server.host"], CUSTOM)
        self.assertIn(CUSTOM, props["virtual.hosts.valid.hosts"].split(","))

    def test_a_copied_share_domain_does_not_pin_it(self):
        props = self._props({"share_domain": CUSTOM})
        self.assertNotEqual(props["web.server.host"], CUSTOM)
        self.assertNotIn(f"proj.{CUSTOM}", props["web.server.host"])


if __name__ == "__main__":
    unittest.main()
