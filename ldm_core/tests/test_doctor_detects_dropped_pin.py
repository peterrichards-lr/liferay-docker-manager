"""`ldm doctor` must catch a MAC pin an older client dropped (LDM-#1789).

`_verify_pinned_mac` runs in the `ldm run` pipeline and nowhere else, so it can
only catch a pin that failed during a run *this client performed*. It cannot
see the case that actually bites.

Compose is generated **client-side**. An `ldm` older than v2.23.0 has no
`--mac-address` at all, so running one against a node configured for a pin
renders a compose file without it. Measured on a real EC2 node:

```
node configured : mac_address = 06:ff:c5:f9:cf:a5
$ ldm-2.22.0 -y run macpin --node aws-node
  exit          : 0
  container MAC : 02:42:ac:12:00:03     <- bridge address
```

Exit 0, "Project started", no mention of the MAC anywhere. That is the original
LDM-#1752 symptom -- healthy container, `License registered`, Activation page
instead of Sign In -- reachable purely by using an older binary.

The fix cannot live in the old client, so it lives in a diagnostic the current
one runs.

## Why it must stay silent in the ordinary cases

A finding on every stopped project, every local project, or every project
without a pin is a finding nobody reads. "Could not read the MAC" is not a
mismatch -- the container is most likely just not running.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetNode

ENS5 = "06:ff:c5:f9:cf:a5"
BRIDGE = "02:42:ac:12:00:03"


class DoctorHarness(unittest.TestCase):
    """Drives the real `_check_pinned_mac`."""

    def check(self, *, meta, node_mac=ENS5, container_mac=BRIDGE, node=True):
        from ldm_core.diagnostics.doctor import DoctorRunner

        doctor = DoctorRunner.__new__(DoctorRunner)
        doctor.results = []
        hints: list[str] = []

        def record_hint(text, *_args, **_kwargs):
            hints.append(str(text))

        doctor.add_hint = record_hint  # type: ignore[method-assign]

        targets = (
            {"aws-1": TargetNode(name="aws-1", host="10.0.0.9", mac_address=node_mac)}
            if node
            else {}
        )

        with (
            patch("ldm_core.config.load_targets", return_value=targets),
            patch(
                "ldm_core.docker_service.DockerService.container_mac_address",
                return_value=container_mac,
            ),
            patch("ldm_core.utils.liferay_container_of", return_value="proj"),
        ):
            doctor._check_pinned_mac(MagicMock(name="proj"), meta)

        return doctor.results, "\n".join(hints)


class ADroppedPinIsReported(DoctorHarness):
    def test_a_bridge_address_on_a_pinned_node_is_flagged(self):
        """The whole point: this is what an old client leaves behind."""
        results, hint = self.check(meta={"target": "aws-1"})

        self.assertTrue(results, "doctor said nothing about a dropped pin")
        self.assertEqual(results[0][2], "warn")
        self.assertIn(BRIDGE, results[0][1])

    def test_the_hint_names_both_values_and_the_remedy(self):
        """ "MAC pin dropped" is not actionable. The recreate is."""
        _, hint = self.check(meta={"target": "aws-1"})

        self.assertIn(BRIDGE, hint)
        self.assertIn(ENS5, hint)
        self.assertIn("ldm rm", hint)

    def test_it_explains_that_an_old_client_causes_this(self):
        """Compose is generated client-side, which is not obvious and is the
        reason the project looks correctly configured while being wrong."""
        _, hint = self.check(meta={"target": "aws-1"})

        self.assertIn("client-side", hint)

    def test_a_correct_pin_reports_healthy_not_silence(self):
        """Confirming the pin is intact is worth saying in a health report."""
        results, hint = self.check(meta={"target": "aws-1"}, container_mac=ENS5)

        self.assertEqual(results[0][2], True)
        self.assertEqual(hint, "")


class ItStaysQuietOtherwise(DoctorHarness):
    def test_a_local_project_is_not_checked(self):
        self.assertEqual(self.check(meta={"target": "local"})[0], [])
        self.assertEqual(self.check(meta={})[0], [])

    def test_a_node_with_no_pin_is_not_checked(self):
        """Pinning is opt-in; this catches a lost pin, not a missing one."""
        self.assertEqual(self.check(meta={"target": "aws-1"}, node_mac="")[0], [])

    def test_an_unknown_node_is_not_checked(self):
        self.assertEqual(self.check(meta={"target": "aws-1"}, node=False)[0], [])

    def test_an_unreadable_mac_is_not_a_mismatch(self):
        """Almost always a stopped container. Reporting a dropped pin on every
        stopped project is how a check gets ignored."""
        self.assertEqual(
            self.check(meta={"target": "aws-1"}, container_mac=None)[0], []
        )


class ItIsWiredIntoDoctor(unittest.TestCase):
    """A check nobody calls is the LDM-#1774 shape.

    This class exists because the first version of this file did not have it:
    removing the call from `_check_project_specific` left all eight tests
    passing, because every one of them invoked `_check_pinned_mac` directly.
    """

    def test_project_health_runs_the_mac_check(self):
        from ldm_core.diagnostics.doctor import DoctorRunner

        seen: list[dict] = []
        doctor = DoctorRunner.__new__(DoctorRunner)
        doctor.results = []
        doctor.hints = []
        doctor.handler = MagicMock()
        doctor.handler.manager.read_meta.return_value = {"target": "aws-1"}
        doctor.project_paths = [MagicMock(name="proj")]
        doctor.add_hint = lambda *_a, **_k: None  # type: ignore[method-assign]

        def record_call(_path, meta):
            seen.append(meta)

        with (
            patch.object(DoctorRunner, "_check_pinned_mac", side_effect=record_call),
            patch("ldm_core.ui.UI.heading"),
        ):
            try:
                doctor._check_project_specific()
            except Exception:
                # The rest of the health check needs far more of a real
                # project than this. All that matters is whether the MAC check
                # was reached before anything else gave up.
                pass

        self.assertTrue(seen, "project health completed without checking the MAC pin")


if __name__ == "__main__":
    unittest.main()
