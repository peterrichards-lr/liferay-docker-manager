"""A pinned MAC that belongs to no interface must be called out (LDM-#1780).

Reported by the AICA team from the `v2.23.0-pre.3` AWS verification, and their
analysis identified a real half of LDM-#1752 that was still open.

There are two ways the pin fails, and only one was guarded:

| Failure | Caught by |
|---|---|
| pin silently ignored (old Docker, wrong compose key, stale container) | `_verify_pinned_mac` -- exit 3 |
| **operator configures the wrong MAC** | **nothing** |

`_verify_pinned_mac` compares the *container's* MAC against the *configured*
one. A wrong configured value satisfies that comparison perfectly: LDM pins the
typo faithfully, the guard passes, and Liferay then refuses the licence. The
operator sees the original LDM-#1752 symptom -- healthy container, "License
registered" in the log, Activation page instead of Sign In -- about twenty
minutes after making the mistake. AICA report it cost them two CI runs.

So this warns at `target add`, where the typo is cheapest to fix.

## Why warn and not refuse

Liferay validates the container's MAC against the licence; it does not care what
the host's interfaces are. A licence bound to a MAC that is not a current NIC is
therefore legitimate, if unusual, and refusing would block a working setup to
catch a likely typo.

## Why "could not ask" must stay silent

An unreachable node, a timeout, or a non-Linux host must not be reported as a
wrong MAC. That is the same distinction `_verify_pinned_mac` already draws when
it cannot read the container's MAC: it warns that it could not check, rather
than claiming a mismatch.
"""

import unittest
from unittest.mock import patch

from ldm_core.config import TargetNode
from ldm_core.handlers.config import ConfigService


def node(mac="02:42:ac:11:00:99", name="aws-1"):
    return TargetNode(name=name, host="192.0.2.10", user="ubuntu", mac_address=mac)


class TheWarningFires(unittest.TestCase):
    """Drives the real method. None of these pass if the guard is removed."""

    def warn_for(self, target, interfaces):
        """Returns the warning text, or "" when nothing was said."""
        said = []

        def record(message, *_args, **_kwargs):
            said.append(message)

        with (
            patch("ldm_core.config.remote_interface_macs", return_value=interfaces),
            patch("ldm_core.ui.UI.warning", side_effect=record),
            patch("ldm_core.ui.UI.detail", side_effect=record),
        ):
            ConfigService._warn_if_mac_matches_no_interface(target)
        return "\n".join(said)

    def test_a_mac_on_no_interface_is_reported(self):
        out = self.warn_for(
            node("02:42:de:ad:be:ef"),
            {"ens5": "06:1a:2b:3c:4d:5e", "docker0": "02:42:11:22:33:44"},
        )

        self.assertIn("02:42:de:ad:be:ef", out)
        self.assertIn("matches none", out)

    def test_it_names_the_interfaces_so_the_operator_can_pick(self):
        """A node shows ens5 beside docker0 and br-*, all plausible and one
        licensed (LDM-#1752). A bare refusal would not help."""
        out = self.warn_for(
            node("02:42:de:ad:be:ef"),
            {"ens5": "06:1a:2b:3c:4d:5e", "docker0": "02:42:11:22:33:44"},
        )

        self.assertIn("ens5", out)
        self.assertIn("06:1a:2b:3c:4d:5e", out)
        self.assertIn("docker0", out)

    def test_it_explains_the_symptom_it_is_preventing(self):
        """ "Wrong MAC" is not actionable; "you will get the Activation page" is."""
        out = self.warn_for(node("02:42:de:ad:be:ef"), {"ens5": "06:1a:2b:3c:4d:5e"})

        self.assertIn("Activation", out)

    def test_a_matching_mac_says_nothing(self):
        """A guard that fires on the correct configuration gets ignored."""
        out = self.warn_for(
            node("06:1a:2b:3c:4d:5e"),
            {"ens5": "06:1a:2b:3c:4d:5e", "docker0": "02:42:11:22:33:44"},
        )

        self.assertEqual(out, "")

    def test_case_does_not_matter(self):
        """`target add` lower-cases on the way in, but a node could report
        upper case. A case mismatch must not read as a wrong MAC."""
        out = self.warn_for(node("06:1A:2B:3C:4D:5E"), {"ens5": "06:1a:2b:3c:4d:5e"})

        self.assertEqual(out, "")

    def test_no_mac_configured_says_nothing(self):
        """Pinning is opt-in. This exists to catch a wrong pin, not to demand one."""
        out = self.warn_for(node(""), {"ens5": "06:1a:2b:3c:4d:5e"})

        self.assertEqual(out, "")

    def test_an_unreachable_node_says_nothing(self):
        """Could not ask != wrong. Warning here would fire on every offline
        node and train the operator to ignore the message."""
        self.assertEqual(self.warn_for(node("02:42:de:ad:be:ef"), None), "")
        self.assertEqual(self.warn_for(node("02:42:de:ad:be:ef"), {}), "")


class TheInterfaceReaderIsHonest(unittest.TestCase):
    """`remote_interface_macs` must distinguish "no answer" from "no match"."""

    def read_with(self, ssh_output):
        from ldm_core import config

        target = node()
        with patch.object(config, "run_command", return_value=ssh_output):
            return config.remote_interface_macs(target)

    def test_it_parses_interface_and_mac(self):
        macs = self.read_with("ens5 06:1a:2b:3c:4d:5e\ndocker0 02:42:11:22:33:44\n")

        self.assertEqual(
            macs, {"ens5": "06:1a:2b:3c:4d:5e", "docker0": "02:42:11:22:33:44"}
        )

    def test_loopback_is_dropped(self):
        """All-zeroes would let any node "match" a configured 00:00:... and
        pads the message with noise."""
        macs = self.read_with("lo 00:00:00:00:00:00\nens5 06:1a:2b:3c:4d:5e\n")

        self.assertEqual(macs, {"ens5": "06:1a:2b:3c:4d:5e"})

    def test_no_answer_is_none_not_empty(self):
        """The caller treats None as "could not ask" and stays silent. Returning
        {} for an unreachable node would be indistinguishable from a node with
        no interfaces, and either way must not produce a warning."""
        self.assertIsNone(self.read_with(""))
        self.assertIsNone(self.read_with(None))

    def test_garbage_lines_are_skipped_not_fatal(self):
        macs = self.read_with("something unexpected here\nens5 06:1a:2b:3c:4d:5e\n")

        self.assertEqual(macs, {"ens5": "06:1a:2b:3c:4d:5e"})

    def test_a_local_target_is_never_asked(self):
        from ldm_core import config

        target = TargetNode(name="local", host="localhost")
        with patch.object(config, "run_command") as called:
            self.assertIsNone(config.remote_interface_macs(target))
        called.assert_not_called()


class ItIsWiredIntoTargetAdd(unittest.TestCase):
    """A guard nobody calls is the LDM-#1774 shape. Asserted by driving
    `cmd_target_add` and observing the check run, not by reading the source."""

    def test_target_add_runs_the_check(self):
        seen: list[TargetNode] = []
        handler = ConfigService.__new__(ConfigService)

        with (
            patch("ldm_core.config.save_target_node"),
            patch.object(
                ConfigService,
                "_warn_if_mac_matches_no_interface",
                side_effect=seen.append,
            ),
            patch("ldm_core.handlers.config.is_local_host", return_value=True),
        ):
            ConfigService.cmd_target_add(
                handler,
                name="aws-1",
                host="192.0.2.10",
                mac_address="02:42:AC:11:00:99",
            )

        self.assertTrue(seen, "target add saved the node without checking the MAC")
        self.assertEqual(seen[0].mac_address, "02:42:ac:11:00:99")


if __name__ == "__main__":
    unittest.main()
