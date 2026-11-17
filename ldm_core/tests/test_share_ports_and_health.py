"""`share start` names the right port and judges health by it (LDM-#2397 follow-up).

Found by the lfr-tunnel session reviewing its strict `-ports` change
(lfr-tunnel #2484), and confirmed against the client and real LCP.json files:

1. Every real LCP.json lists ports as objects -- `[{"port": 8080, "external":
   false}]` -- and LDM joined them as Python reprs, so the client got
   `-ports "{'port': 8080, 'external': False}"`. The released client dropped
   it and auto-discovered (the wrong-project path #2397 closed); the strict one
   refuses it. It also ran before the project's own port, bypassing #2397.
2. Every released client dials 8080 for its local health whatever `-ports`
   said. So `ldm share start --ports 8095` with nothing on 8080 never became
   "healthy": LDM stopped a working tunnel after 10s and blamed "Local target
   port 8080". That is the user report LDM-#2397 began from.
3. The strict client dials every published port, so a secondary port still
   starting failed the whole share.
"""

import itertools
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from ldm_core.handlers.share import ShareService, lcp_json_ports

# The module, not the class: a TestCase imported by name is collected again here.
from ldm_core.tests import test_share

CLOUD_LCP = {"id": "liferay", "ports": [{"port": 8080, "external": False}]}


class LcpJsonPortsAreReadFromObjects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _ports(self, entries):
        (self.dir / "LCP.json").write_text(json.dumps({"ports": entries}))
        return lcp_json_ports(self.dir)

    def test_the_liferay_cloud_object_form(self):
        self.assertEqual(
            self._ports([{"port": 9300, "external": False}, {"port": 9200}]),
            ("9300,9200", "LCP.json"),
        )

    def test_bare_numbers_are_still_accepted(self):
        self.assertEqual(self._ports([3000, "3001"]), ("3000,3001", "LCP.json"))

    def test_anything_not_a_valid_port_is_skipped(self):
        """A malformed `-ports` is refused by the strict client, and silently
        discarded -- then auto-discovered -- by the old one."""
        ports, _ = self._ports(
            [{"port": "web"}, {"external": True}, True, 0, 70000, {"port": 8080}, 8080]
        )
        self.assertEqual(ports, "8080")

    def test_no_usable_port_is_no_ports(self):
        self.assertEqual(self._ports([{"external": True}]), (None, None))

    def test_no_file_is_no_ports(self):
        self.assertEqual(lcp_json_ports(self.dir), (None, None))


class TheSharedPortComesFromTheRightPlace(unittest.TestCase):
    """Drives the real `cmd_start` and reads the argv the client got."""

    # Set by TestShareTunnelTopology.setUp, which this borrows.
    service: Any
    mock_manager: Any

    def setUp(self):
        test_share.TestShareTunnelTopology.setUp(self)  # type: ignore[arg-type]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        cwd = Path.cwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, cwd)

    def _start(self):
        res = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("subprocess.run", return_value=res) as run,
        ):
            self.service.cmd_start(subdomain="demo")
        cmd = run.call_args[0][0]
        return cmd[cmd.index("-ports") + 1] if "-ports" in cmd else None

    def test_without_a_project_the_cloud_lcp_json_port_is_used(self):
        (self.dir / "LCP.json").write_text(json.dumps(CLOUD_LCP))
        self.assertEqual(self._start(), "8080")

    def test_a_projects_own_port_beats_its_lcp_json(self):
        """Its LCP.json ports are container-side; the host port LDM published
        is the project's `port`. The reprs used to reach the client here."""
        project = self.dir / "barbara"
        (project / "liferay").mkdir(parents=True)
        (project / "liferay" / "LCP.json").write_text(json.dumps(CLOUD_LCP))
        (self.dir / "LCP.json").write_text(json.dumps(CLOUD_LCP))
        self.mock_manager.detect_project_path = MagicMock(  # type: ignore[method-assign]
            return_value=project
        )
        self.mock_manager.read_meta = MagicMock(  # type: ignore[method-assign]
            return_value={"share_provider": "lfr-tunnel", "port": "8095"}
        )

        self.assertEqual(self._start(), "8095")


def _status(state="connected", status="unhealthy"):
    return MagicMock(
        returncode=0,
        stdout=json.dumps(
            {
                "running": True,
                "connection_state": state,
                "status": status,
                "inspector_port": 4040,
            }
        ),
        stderr="",
    )


def _info(destination, auth_valid=True, leased=True):
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "auth": {"valid": auth_valid},
        "subdomain": {"leased": leased, "conflict": False},
        "destination": destination,
        "connection": {"state": "connected"},
    }
    return response


OLD_CLIENT = {"host": "127.0.0.1", "port": 8080, "responsive": False}


class HealthIsJudgedByThePortLdmAskedFor(unittest.TestCase):
    """Drives the real `_poll_tunnel_health` against a client's answers."""

    def setUp(self):
        self.service = ShareService(test_share.MockManager())
        self.service._ensure_binary = MagicMock(  # type: ignore[method-assign]
            return_value=Path("/fake/lfr-tunnel")
        )

    def _poll(self, info, ports="8095", reachable=None, status=None):
        dial = MagicMock()
        if reachable is False:
            dial.side_effect = OSError("refused")
        with (
            patch("subprocess.run", return_value=status or _status()),
            patch("requests.get", return_value=info),
            patch("socket.create_connection", dial),
            # One pass of the loop, then the timeout.
            patch("time.time", side_effect=itertools.count(0, 6)),
            patch("time.sleep"),
            patch("ldm_core.handlers.share.UI") as ui,
        ):
            ok, reason = self.service._poll_tunnel_health(
                "demo", ports=ports, timeout=10
            )
        return ok, reason, dial, ui

    def test_an_old_client_blaming_8080_is_overruled_when_8095_answers(self):
        """The reported failure: a working tunnel stopped over port 8080."""
        ok, _, dial, _ = self._poll(_info(OLD_CLIENT))

        self.assertTrue(ok)
        self.assertEqual(dial.call_args[0][0], ("127.0.0.1", 8095))

    def test_an_old_client_still_fails_when_the_asked_port_is_down(self):
        ok, reason, _, _ = self._poll(_info(OLD_CLIENT), reachable=False)

        self.assertFalse(ok)
        # Names what LDM asked for; the old client's 8080 is meaningless.
        self.assertIn("8095", reason)
        self.assertNotIn("8080", reason)

    def test_a_strict_client_with_a_secondary_port_down_still_shares(self):
        info = _info(
            {
                "ports": [
                    {"port": 8095, "responsive": True},
                    {"port": 3000, "name_suffix": "3000", "responsive": False},
                ]
            }
        )
        ok, _, dial, ui = self._poll(info, ports="8095,3000")

        self.assertTrue(ok)
        dial.assert_not_called()  # the client's per-port report is used
        self.assertIn("3000", ui.warning.call_args[0][0])

    def test_a_strict_client_with_the_primary_down_fails_naming_it(self):
        info = _info(
            {
                "ports": [
                    {"port": 8095, "responsive": False},
                    {"port": 3000, "responsive": True},
                ]
            }
        )
        ok, reason, _, _ = self._poll(info, ports="8095,3000")

        self.assertFalse(ok)
        self.assertIn("8095", reason)
        self.assertNotIn("3000", reason)

    def test_a_rejected_token_is_never_overruled(self):
        ok, _, _, _ = self._poll(_info(OLD_CLIENT, auth_valid=False))
        self.assertFalse(ok)

    def test_an_unleased_subdomain_is_never_overruled(self):
        ok, _, _, _ = self._poll(_info(OLD_CLIENT, leased=False))
        self.assertFalse(ok)

    def test_a_disconnected_tunnel_is_never_overruled(self):
        ok, _, _, _ = self._poll(_info(OLD_CLIENT), status=_status(state="connecting"))
        self.assertFalse(ok)

    def test_without_named_ports_the_clients_verdict_stands(self):
        """Discovery chose the ports; LDM has nothing better to judge by."""
        ok, _, dial, _ = self._poll(_info(OLD_CLIENT), ports=None)

        self.assertFalse(ok)
        dial.assert_not_called()


if __name__ == "__main__":
    unittest.main()
