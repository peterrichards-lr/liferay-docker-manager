"""Liferay's licence binds to the container's MAC (LDM-#1752).

On a remote node the container takes a bridge-assigned address and validation
fails. Measured on aws-1 with `liferay/dxp:2026.q3.0`, the container recreated
identically except for `--mac-address`:

    before  02:42:ac:12:00:03  "license validation failed ... MAC address
                                matching failed"  -> DXP Activation page
    after   06:d0:95:e5:26:a7  "DXP Non-Production license validation passed"
                               -> Sign In form, and the .li file on disk

Same image, mounts, environment and licence. Only the MAC.

**The post-start check is the point of this feature, not the compose key.**
Writing `mac_address` into the compose file is a request. Measured on Compose
v5.2.0 / CLI 29.7.2 against daemon 25.0.14 both forms applied together with no
conflict and no deprecation warning -- but that is one toolchain, and a version
honouring neither would drop the MAC in silence. So would a container created
before the configured value changed, which cannot be corrected in place:
`docker network connect --mac-address` does not exist on 25.0.14.

All three failures present identically -- healthy container, `License
registered` in the log, a portal serving the Activation page -- which is why
LDM re-reads the MAC from the running container and refuses on a mismatch.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetNode

PINNED = "06:d0:95:e5:26:a7"
BRIDGE = "02:42:ac:12:00:03"


class TheTargetCarriesTheMac(unittest.TestCase):
    def test_it_round_trips(self):
        node = TargetNode(name="aws-1", host="10.0.0.1", mac_address=PINNED)

        self.assertEqual(
            TargetNode.from_dict("aws-1", node.to_dict()).mac_address, PINNED
        )

    def test_it_defaults_to_empty(self):
        """Never inferred: `ip a` shows ens5 beside docker0 and br-*, all
        plausible and one correct."""
        self.assertEqual(TargetNode(name="local").mac_address, "")

    def test_an_older_config_without_the_key_still_loads(self):
        node = TargetNode.from_dict("aws-1", {"host": "10.0.0.1"})

        self.assertEqual(node.mac_address, "")


class TheComposeFileRequestsIt(unittest.TestCase):
    """Both forms, because neither is trusted."""

    def _service(self, target, configured):
        from ldm_core.handlers.composer import ComposerService

        composer = ComposerService.__new__(ComposerService)
        composer.manager = SimpleNamespace(target=target)
        node = TargetNode(name=target or "local", mac_address=configured)
        with patch("ldm_core.config.load_targets", return_value={target: node}):
            return composer._resolve_target_mac({"target": target})

    def test_a_remote_target_with_a_configured_mac_pins_it(self):
        self.assertEqual(self._service("aws-1", PINNED), PINNED)

    def test_a_local_target_is_left_alone(self):
        """Local runs validate today; pinning would change working behaviour."""
        self.assertEqual(self._service("local", PINNED), "")

    def test_a_remote_target_without_a_configured_mac_is_left_alone(self):
        self.assertEqual(self._service("aws-1", ""), "")


class TheRunningContainerIsChecked(unittest.TestCase):
    """The guarantee. Each case below otherwise boots healthy and then refuses
    to sign in."""

    def _verify(self, *, target, configured, actual, dry_run=False):
        from ldm_core.pipelines.run import _verify_pinned_mac

        manager = MagicMock()
        manager.target = target
        context = MagicMock()
        context.get.side_effect = lambda k, d=None: {
            "dry_run": dry_run,
            "project_id": "proj",
        }.get(k, d)

        node = TargetNode(name=target or "local", mac_address=configured)
        died: dict = {}

        def capture_die(msg, details=None, tip=None, exit_code=1):
            died.update(msg=msg, tip=tip, exit_code=exit_code)
            raise SystemExit(exit_code)

        with (
            patch("ldm_core.config.load_targets", return_value={target: node}),
            patch(
                "ldm_core.docker_service.DockerService.container_mac_address",
                return_value=actual,
            ),
            patch("ldm_core.utils.liferay_container_of", return_value="proj"),
            patch("ldm_core.pipelines.run.UI.die", side_effect=capture_die),
            patch("ldm_core.pipelines.run.UI.warning") as warned,
        ):
            try:
                _verify_pinned_mac(manager, context, {"container_name": "proj"})
            except SystemExit:
                pass
        self.warned = warned
        return died or None

    def test_a_dropped_pin_is_refused(self):
        """The toolchain ignored the key LDM wrote."""
        died = self._verify(target="aws-1", configured=PINNED, actual=BRIDGE)

        self.assertIsNotNone(died, "a bridge MAC was accepted as the pinned one")
        self.assertEqual(died["exit_code"], 3)

    def test_the_refusal_names_both_values(self):
        """'MAC mismatch' alone leaves the operator to work out which is which."""
        died = self._verify(target="aws-1", configured=PINNED, actual=BRIDGE)

        self.assertIn(BRIDGE, died["msg"])
        self.assertIn(PINNED, died["msg"])

    def test_the_refusal_says_a_recreate_is_required(self):
        """It cannot be corrected in place on 25.0.14, so saying 'fix the MAC'
        would send the operator after something impossible."""
        died = self._verify(target="aws-1", configured=PINNED, actual=BRIDGE)

        self.assertIn("ldm rm", died["tip"])

    def test_a_correct_pin_passes(self):
        self.assertIsNone(
            self._verify(target="aws-1", configured=PINNED, actual=PINNED)
        )

    def test_case_differences_do_not_trip_it(self):
        """`docker inspect` casing must not read as a mismatch."""
        self.assertIsNone(
            self._verify(target="aws-1", configured=PINNED.upper(), actual=PINNED)
        )

    def test_no_configured_mac_means_no_check(self):
        """This catches a pin that did not take; it does not require one."""
        self.assertIsNone(self._verify(target="aws-1", configured="", actual=BRIDGE))

    def test_a_local_target_is_not_checked(self):
        self.assertIsNone(
            self._verify(target="local", configured=PINNED, actual=BRIDGE)
        )

    def test_a_dry_run_is_not_checked(self):
        """Nothing was started, so there is no MAC to read (LDM-#1704)."""
        self.assertIsNone(
            self._verify(target="aws-1", configured=PINNED, actual=None, dry_run=True)
        )

    def test_an_unreadable_mac_warns_rather_than_blocking(self):
        """Unreadable is not the same as wrong, and blocking a boot on a
        diagnosis that did not run would be worse than the gap it closes."""
        died = self._verify(target="aws-1", configured=PINNED, actual=None)

        self.assertIsNone(died)
        self.assertTrue(self.warned.called)


if __name__ == "__main__":
    unittest.main()


class TheCliCanRecordIt(unittest.TestCase):
    """A documented flag that does not exist is worse than no flag.

    The first draft of the docs said `ldm target set aws-1 --mac-address ...`.
    That command assigns a target to the active PROJECT and takes no such
    option -- so the instruction was wrong twice over, and would have sent an
    operator round in circles while the portal kept serving the Activation
    page (LDM-#1752).
    """

    def _add_parser(self):
        from ldm_core.cli import get_parser

        _parser, subparsers = get_parser()
        target = subparsers.choices["target"]
        return target._subparsers._group_actions[0].choices["add"]

    def test_the_flag_is_declared(self):
        options = {o for a in self._add_parser()._actions for o in a.option_strings}

        self.assertIn("--mac-address", options)

    def test_it_is_declared_on_add_not_set(self):
        """`set` assigns a target to a project; node properties live on `add`."""
        from ldm_core.cli import get_parser

        _parser, subparsers = get_parser()
        target = subparsers.choices["target"]
        set_parser = target._subparsers._group_actions[0].choices["set"]
        options = {o for a in set_parser._actions for o in a.option_strings}

        self.assertNotIn("--mac-address", options)

    def test_the_docs_name_the_command_that_exists(self):
        """The docs and the parser must agree, or the instruction is fiction."""
        from pathlib import Path

        doc = (
            Path(__file__).resolve().parent.parent.parent
            / "docs"
            / "explanation"
            / "remote-node-architecture.md"
        ).read_text(encoding="utf-8")

        self.assertIn("ldm target add", doc)
        self.assertNotIn("ldm target set aws-1 --mac-address", doc)

    def test_the_docs_state_the_bridge_only_caveat(self):
        """ "Safe" unqualified would be actively wrong on macvlan or host
        networking, where the duplicate MAC reaches the wire."""
        from pathlib import Path

        doc = (
            Path(__file__).resolve().parent.parent.parent
            / "docs"
            / "explanation"
            / "remote-node-architecture.md"
        ).read_text(encoding="utf-8")

        self.assertIn("macvlan", doc)
        self.assertIn("172.18.0.0/16", doc)
