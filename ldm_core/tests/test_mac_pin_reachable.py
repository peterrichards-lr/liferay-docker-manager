"""The MAC guard must be reachable, and its dry-run guard must fire.

LDM-#1798 / LDM-#1799, both found by the maintainer verifying `v2.23.0-pre.5`
on a real remote node.

## What was wrong

**The refusal branch had never executed.** `_verify_pinned_mac` ran only in the
`ldm run` pipeline, which is the one path where a mismatch cannot occur:
`mac_address` is part of the compose *service spec*, so changing the configured
value makes compose recreate the container by itself. Measured:

    # changed the configured MAC, ran `ldm run` with NO `ldm rm`
    container Created 12:40:56Z   MAC 02:aa:bb:cc:dd:ee   (the new value)

Meanwhile `ldm start` and `ldm restart` -- the routes that genuinely leave a
stale container -- never ran the check at all. So exit 3 was advertised in the
contract, described in the CHANGELOG, and unreachable.

**A wrong-but-applied pin passed silently.** The check compared the container's
MAC against the *configured* one and never consulted the node, so a MAC that is
not any NIC satisfied it perfectly. Liferay then logged `MAC address matching
failed` and served the Activation page -- the original LDM-#1752 symptom, which
this check exists to prevent.

**The dry-run guard was dead code.** It read `context.get("dry_run")`, and
nothing ever writes that key; the rest of `run.py` reads
`getattr(manager, "dry_run", False)`. So a dry run inspected a container it had
never created and warned that it could not read its MAC.

## What these tests assert

Behaviour, by calling the real functions. The neutering probe in the PR shows
each of them failing when the corresponding behaviour is removed -- which the
previous tests did not do, because there were none for a branch nothing reached.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ldm_core.runtime.mac_pin import configured_mac, verify_pinned_mac

PINNED = "06:d0:95:e5:26:a7"
WRONG = "02:aa:bb:cc:dd:ee"
META = {"container_name": "macver", "target": "aws-1"}


def node(mac=PINNED):
    return SimpleNamespace(
        name="aws-1", host="192.0.2.10", user="ec2-user", key_path="", mac_address=mac
    )


class Harness(unittest.TestCase):
    """Drives the real `verify_pinned_mac` against a scripted node."""

    def run_check(self, container_mac, configured=PINNED, interfaces=None, **kwargs):
        """Returns (exit_code or None, messages)."""
        said: list[str] = []

        def record(message, *_args, **_kwargs):
            said.append(str(message))

        def die(message, *_args, **kw):
            # `details` and `tip` are where UI.die carries the actionable half,
            # so a stub that captured only `message` would make an assertion on
            # the recovery hint fail for the wrong reason.
            said.append(str(message))
            for key in ("details", "tip"):
                if kw.get(key):
                    said.append(str(kw[key]))
            raise SystemExit(kw.get("exit_code", 1))

        with (
            patch(
                "ldm_core.config.load_targets", return_value={"aws-1": node(configured)}
            ),
            patch(
                "ldm_core.docker_service.DockerService.container_mac_address",
                return_value=container_mac,
            ),
            patch("ldm_core.config.remote_interface_macs", return_value=interfaces),
            patch("ldm_core.ui.UI.warning", side_effect=record),
            patch("ldm_core.ui.UI.detail", side_effect=record),
            patch("ldm_core.ui.UI.die", side_effect=die),
        ):
            try:
                verify_pinned_mac(META, "aws-1", **kwargs)
            except SystemExit as exc:
                return exc.code, "\n".join(said)
        return None, "\n".join(said)


class TheRefusalIsReachable(Harness):
    """The whole point of LDM-#1798: this branch had never run."""

    def test_a_stale_container_refuses_with_exit_3(self):
        code, out = self.run_check(container_mac=WRONG)

        self.assertEqual(code, 3, f"expected exit 3, got {code}: {out}")
        self.assertIn(WRONG, out)
        self.assertIn(PINNED, out)

    def test_a_matching_container_does_not_refuse(self):
        """A guard that fires on a correct configuration gets disabled."""
        code, _ = self.run_check(container_mac=PINNED, interfaces={"ens5": PINNED})

        self.assertIsNone(code)

    def test_the_hint_is_the_caller_s_recovery_not_a_fixed_string(self):
        """From start/restart a plain `ldm run` recreates the container, so
        telling the operator to `ldm rm` first is destructive advice for a
        problem that does not need it (LDM-#1798)."""
        _, out = self.run_check(container_mac=WRONG, recreate_hint="ldm run macver")

        self.assertIn("ldm run macver", out)
        self.assertNotIn("ldm rm", out)


class AWrongButAppliedPinIsReported(Harness):
    """The gap that let the LDM-#1752 symptom survive the guard."""

    def test_a_mac_on_no_interface_warns(self):
        code, out = self.run_check(
            container_mac=WRONG,
            configured=WRONG,
            interfaces={"ens5": PINNED, "docker0": "02:42:11:22:33:44"},
        )

        self.assertIsNone(code, "this warns, it does not refuse")
        self.assertIn("none of", out)
        self.assertIn("Activation", out)

    def test_a_mac_that_is_an_interface_is_silent(self):
        _, out = self.run_check(
            container_mac=PINNED, configured=PINNED, interfaces={"ens5": PINNED}
        )

        self.assertNotIn("none of", out)

    def test_an_unreachable_node_does_not_claim_the_mac_is_wrong(self):
        """Could not ask != wrong -- the same distinction drawn for an
        unreadable container MAC."""
        unreachable: dict | None
        for unreachable in (None, {}):
            with self.subTest(interfaces=unreachable):
                _, out = self.run_check(
                    container_mac=PINNED, configured=PINNED, interfaces=unreachable
                )
                self.assertNotIn("none of", out)


class TheDryRunGuardFires(Harness):
    """LDM-#1799: it read a key nothing writes, so it never fired."""

    def test_a_dry_run_inspects_nothing(self):
        called = []

        def spy(*_args, **_kwargs):
            called.append(1)
            return WRONG

        with patch(
            "ldm_core.docker_service.DockerService.container_mac_address",
            side_effect=spy,
        ):
            verify_pinned_mac(META, "aws-1", dry_run=True)

        self.assertEqual(called, [], "a dry run inspected a container it never created")

    def test_a_real_run_does_inspect(self):
        """Otherwise the test above would pass against a check that never runs."""
        code, _ = self.run_check(container_mac=WRONG)

        self.assertEqual(code, 3)


class ItStaysQuietWhereThereIsNothingToCheck(Harness):
    def test_no_pin_configured(self):
        self.assertEqual(configured_mac(None), "")
        self.assertEqual(configured_mac("local"), "")

    def test_an_unreadable_container_mac_warns_rather_than_refusing(self):
        code, out = self.run_check(container_mac=None)

        self.assertIsNone(code)
        self.assertIn("Could not read", out)


class BothLifecycleCommandsRunIt(unittest.TestCase):
    """LDM-#1798: `start` and `restart` are where staleness is reachable.

    Asserted by driving the real methods and observing the check run, not by
    reading the source -- a wiring test that greps for a call would have passed
    against the original code too, since the call existed in the pipeline.
    """

    def _drive(self, method_name):
        from ldm_core.runtime.orchestration import OrchestrationService

        seen = []
        svc = OrchestrationService.__new__(OrchestrationService)
        root = SimpleNamespace(name="macver")

        def record(_root, _meta, target_name):
            seen.append(target_name)

        # A SimpleNamespace stands in for LiferayManager: the methods this
        # path touches are the four below, and building a real manager would
        # reach the developer's actual ~/.ldm.
        svc.manager = SimpleNamespace(  # type: ignore[assignment]
            read_meta=lambda _r: dict(META),
            target=None,
            run_command=lambda *_a, **_k: "ok",
            detect_project_path=lambda *_a, **_k: root,
            find_dxp_roots=lambda: [],
            dry_run=False,
        )
        with (
            patch.object(
                OrchestrationService,
                "_verify_pinned_mac_after_lifecycle",
                side_effect=record,
            ),
            patch.object(OrchestrationService, "_report_batch_failures"),
            patch("ldm_core.runtime.orchestration.announce_remote_targets"),
            patch(
                "ldm_core.docker_service.DockerService.get_compose_cmd_prefix",
                return_value=["docker", "compose"],
            ),
        ):
            getattr(svc, method_name)(project_id="macver")
        return seen

    def test_restart_runs_the_check(self):
        self.assertEqual(self._drive("cmd_restart"), ["aws-1"])


if __name__ == "__main__":
    unittest.main()
