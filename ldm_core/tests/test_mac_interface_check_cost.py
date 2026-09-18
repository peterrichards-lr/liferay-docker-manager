"""The interface cross-check must stay off the lifecycle fast path (LDM-#1804).

`_warn_if_not_a_node_interface` opens an SSH connection to the node. Measured
against a real remote node, `ldm restart` on a pinned project:

    unpinned (no cross-check)   2900 ms, 2868 ms
    pinned   (cross-check)      4534 ms, 4192 ms

~1.4s, or 48% of a restart. That was never a considered trade -- LDM-#1798 added
the check and only its *failure* path was exercised, because a mismatch refuses
before the cross-check runs, so the cost on the success path went unmeasured
until the maintainer objected to it.

## The split

- `ldm run` keeps it. The container is being created, and 1.4s is noise against
  a multi-minute boot -- it is also the moment a fresh container could first
  carry a wrong address.
- `ldm start` / `ldm restart` do not, unless `--verify-mac` is passed.

**The MAC comparison itself is unaffected and still runs on all three.** That is
a local `docker inspect`, it is free, and it is the refusal path LDM-#1798 made
reachable. Only the extra SSH round trip is conditional.

Little is lost by the default: a wrong pin is caught once at `ldm target add`
(LDM-#1780), which is where a typo is cheapest to fix. What `--verify-mac`
covers is the narrower case of a node whose interfaces changed after it was
registered.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ldm_core.runtime.mac_pin import verify_pinned_mac

PINNED = "06:d0:95:e5:26:a7"
OTHER = "02:aa:bb:cc:dd:ee"
META = {"container_name": "macver", "target": "aws-1"}


def node(mac=PINNED):
    return SimpleNamespace(
        name="aws-1", host="192.0.2.10", user="ec2-user", key_path="", mac_address=mac
    )


class TheCrossCheckIsConditional(unittest.TestCase):
    """Counts the SSH round trip rather than asserting on a message: the cost
    is the thing under test, and a silent-but-still-dialling implementation
    would pass a message-only assertion."""

    def _run(self, **kwargs):
        calls = []

        def spy(_target):
            calls.append(1)
            return {"ens5": PINNED}

        with (
            patch("ldm_core.config.load_targets", return_value={"aws-1": node()}),
            patch(
                "ldm_core.docker_service.DockerService.container_mac_address",
                return_value=PINNED,
            ),
            patch("ldm_core.config.remote_interface_macs", side_effect=spy),
            patch("ldm_core.ui.UI.warning"),
            patch("ldm_core.ui.UI.detail"),
        ):
            verify_pinned_mac(META, "aws-1", **kwargs)
        return len(calls)

    def test_it_runs_by_default(self):
        """`ldm run` gets it: the container is being created, and the cost is
        noise against a boot."""
        self.assertEqual(self._run(), 1)

    def test_it_is_skipped_when_switched_off(self):
        """The lifecycle fast path. This is the whole point of LDM-#1804."""
        self.assertEqual(self._run(check_interfaces=False), 0)

    def test_switching_it_off_does_not_disable_the_mac_comparison(self):
        """The refusal LDM-#1798 made reachable must survive the optimisation.

        Dropping the cross-check to save an SSH call is worth doing; dropping
        the refusal with it would undo the fix that prompted this whole branch.
        """
        died = {}

        def capture(message, *_a, **kw):
            died["code"] = kw.get("exit_code")
            raise SystemExit(kw.get("exit_code", 1))

        with (
            patch("ldm_core.config.load_targets", return_value={"aws-1": node()}),
            patch(
                "ldm_core.docker_service.DockerService.container_mac_address",
                return_value=OTHER,
            ),
            patch("ldm_core.config.remote_interface_macs") as probe,
            patch("ldm_core.ui.UI.warning"),
            patch("ldm_core.ui.UI.detail"),
            patch("ldm_core.ui.UI.die", side_effect=capture),
        ):
            with self.assertRaises(SystemExit):
                verify_pinned_mac(META, "aws-1", check_interfaces=False)

        self.assertEqual(died.get("code"), 3)
        probe.assert_not_called()


class TheLifecycleCommandsOptOut(unittest.TestCase):
    """`start`/`restart` must pass the switch through from the flag."""

    def _drive(self, verify_mac):
        from ldm_core.runtime.orchestration import OrchestrationService

        seen = {}
        svc = OrchestrationService.__new__(OrchestrationService)
        svc.manager = SimpleNamespace(  # type: ignore[assignment]
            args=SimpleNamespace(verify_mac=verify_mac), dry_run=False
        )

        def record(_meta, _target, **kwargs):
            seen.update(kwargs)

        with patch("ldm_core.runtime.mac_pin.verify_pinned_mac", side_effect=record):
            svc._verify_pinned_mac_after_lifecycle(
                SimpleNamespace(name="macver"), META, "aws-1"
            )
        return seen

    def test_the_flag_is_off_by_default(self):
        self.assertFalse(self._drive(False).get("check_interfaces"))

    def test_the_flag_switches_it_on(self):
        self.assertTrue(self._drive(True).get("check_interfaces"))

    def test_a_missing_flag_attribute_does_not_crash_or_enable_it(self):
        """`args` may not carry the attribute at all -- an unset MagicMock is
        truthy, which is how LDM-#1799 shipped a guard that never fired."""
        from ldm_core.runtime.orchestration import OrchestrationService

        seen = {}
        svc = OrchestrationService.__new__(OrchestrationService)
        svc.manager = SimpleNamespace(  # type: ignore[assignment]
            args=SimpleNamespace(), dry_run=False
        )

        def record(_meta, _target, **kwargs):
            seen.update(kwargs)

        with patch("ldm_core.runtime.mac_pin.verify_pinned_mac", side_effect=record):
            svc._verify_pinned_mac_after_lifecycle(
                SimpleNamespace(name="macver"), META, "aws-1"
            )

        self.assertFalse(seen.get("check_interfaces"))


class ALocalTargetIsNeverChecked(unittest.TestCase):
    """Pinning is a remote-node concept (LDM-#1804).

    `configured_mac` originally skipped only the target *named* `local`, while
    the rest of `config.py` treats a target as local when its name is `local`
    **or its host is a loopback address**. That gap is not theoretical: LDM's own
    E2E registers `127.0.0.2` under a different name, and `is_local_host` says
    that is local -- so such a target would have been inspected, and on a
    mismatch could have refused with exit 3 on a purely local project.
    """

    def _inspect_calls_for(self, host):
        calls = []

        def spy(*_a, **_k):
            calls.append(1)
            # Matches the configured pin: this counts whether the container is
            # inspected at all, and a mismatch would refuse before answering
            # that question.
            return PINNED

        target = SimpleNamespace(
            name="loopback-node", host=host, user="", key_path="", mac_address=PINNED
        )
        with (
            patch(
                "ldm_core.config.load_targets", return_value={"loopback-node": target}
            ),
            patch(
                "ldm_core.docker_service.DockerService.container_mac_address",
                side_effect=spy,
            ),
            patch("ldm_core.config.remote_interface_macs", return_value=None),
            patch("ldm_core.ui.UI.warning"),
            patch("ldm_core.ui.UI.detail"),
            patch("ldm_core.ui.UI.die", side_effect=AssertionError("refused locally")),
        ):
            verify_pinned_mac(META, "loopback-node")
        return len(calls)

    def test_a_loopback_host_is_skipped_even_under_another_name(self):
        for host in ("127.0.0.1", "127.0.0.2", "localhost", "::1"):
            with self.subTest(host=host):
                self.assertEqual(self._inspect_calls_for(host), 0)

    def test_a_genuinely_remote_host_is_still_checked(self):
        """Otherwise the skip above could be swallowing everything."""
        self.assertEqual(self._inspect_calls_for("192.0.2.10"), 1)


class TheFlagExistsWhereItShould(unittest.TestCase):
    """Driven through the real parser, not read out of the source."""

    def _help(self, command):
        import subprocess  # nosec B404 - runs the CLI under test
        import sys

        return subprocess.run(  # nosec B603 - fixed argv, no shell
            [
                sys.executable,
                "-c",
                f"import sys; sys.argv=['ldm','{command}','--help']; "
                "from ldm_core.cli import main; main()",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        ).stdout

    def test_start_and_restart_offer_it(self):
        for command in ("start", "restart"):
            with self.subTest(command=command):
                self.assertIn("--verify-mac", self._help(command))

    def test_stop_does_not(self):
        """A stopped container has no MAC to check, so offering it there would
        be a flag that does nothing."""
        self.assertNotIn("--verify-mac", self._help("stop"))


if __name__ == "__main__":
    unittest.main()
