"""A host sleep or Docker VM suspension does not consume the readiness timeout.

LDM-#2413: a MacBook slept mid-boot during v2.26.11-pre.2 verification. The
OrbStack VM froze, Liferay stopped 32 s into its boot, and the host's wall
clock kept counting -- so the 600 s readiness timeout expired while the portal
was not running, and the failure read "Liferay failed to become healthy".

These tests drive the real `_wait_for_ready` and `cmd_wait` loops against a
simulated VM whose boot advances only while it is running. A suspension moves
the wall clock without moving the boot, exactly as the frozen VM did. Nothing
here talks to Docker.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.runtime.readiness import ReadinessClock
from ldm_core.tests.test_readiness import MockRuntime

WARNING_30_MIN = (
    "The host or Docker VM appears to have been suspended for ~30 min; "
    "that time does not count toward the readiness timeout."
)
TIMEOUT_LINE = "\nTimed out waiting for Liferay to become healthy."

# Bounds every loop below: each poll sleeps, so a wait that has not ended
# after this many sleeps is a hang, and fails instead of spinning forever.
MAX_SLEEPS = 2000


class SimulatedVM:
    """A Liferay boot that progresses only while the VM is running.

    `suspend_on` maps a sleep call's ordinal to seconds of suspension added
    to the wall clock during that sleep. `healthy_after` and `http_after` are
    seconds of RUNNING time, so a suspension cannot bring them closer.
    """

    def __init__(self, *, healthy_after=None, http_after=0.0, suspend_on=None):
        self.wall = 1_800_000_000.0
        self.running = 0.0
        self.sleeps = 0
        self.suspended = 0.0
        self.healthy_after = healthy_after
        self.http_after = http_after
        self.suspend_on = suspend_on or {}

    def time(self):
        return self.wall

    def sleep(self, seconds):
        self.sleeps += 1
        if self.sleeps > MAX_SLEEPS:
            raise AssertionError("the wait loop did not end: no bound reached")
        self.wall += seconds
        self.running += seconds
        gap = self.suspend_on.get(self.sleeps, 0.0)
        self.wall += gap
        self.suspended += gap

    def run_command(self, cmd, **_kwargs):
        if "inspect" in cmd and "{{.State.Health.Status}}" in cmd:
            if self.healthy_after is not None and self.running >= self.healthy_after:
                return "healthy"
            return "starting"
        if "stats" in cmd:
            return "1.0%"
        return ""

    def http(self, *_args, **_kwargs):
        response = MagicMock()
        response.status_code = 200 if self.running >= self.http_after else 503
        return response


class SuspensionHarness(unittest.TestCase):
    def setUp(self):
        from ldm_core.config import TargetNode

        patches = [
            patch("ldm_core.utils.reclaim_volume_permissions"),
            patch(
                "ldm_core.diagnostics.doctor.check_for_updates",
                return_value=(None, None),
            ),
            patch(
                "ldm_core.docker_service.get_active_target",
                return_value=TargetNode(
                    name="local", host="localhost", is_default=True
                ),
            ),
            patch("subprocess.run"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.handler = MockRuntime()
        self.handler.detect_project_path = MagicMock(return_value=Path(tmp.name))  # type: ignore[method-assign]
        self.handler.get_container_status = MagicMock(return_value="running")  # type: ignore[method-assign]
        self.handler.args.browser = False
        self.readiness = self.handler.handler.readiness

    def _patched(self, vm):
        return (
            patch("time.time", side_effect=vm.time),
            patch("time.sleep", side_effect=vm.sleep),
            patch.object(self.handler, "run_command", side_effect=vm.run_command),
            patch("requests.get", side_effect=vm.http),
            patch("ldm_core.ui.UI.warning"),
            patch("ldm_core.ui.UI.error"),
            patch("ldm_core.ui.UI.die", side_effect=SystemExit),
        )

    def wait_for_ready(self, vm):
        """Run the real `_wait_for_ready`; return (result, warnings, errors)."""
        p_time, p_sleep, p_run, p_http, p_warn, p_err, p_die = self._patched(vm)
        with p_time, p_sleep, p_run, p_http, p_warn as warn, p_err as err, p_die:
            result = self.readiness._wait_for_ready(
                {"container_name": "test-container"},
                "localhost",
                timeout=600,
                stream_status=True,
            )
        return result, warn, err

    @staticmethod
    def suspension_warnings(warn):
        return [
            c.args[0]
            for c in warn.call_args_list
            if c.args and "appears to have been suspended" in str(c.args[0])
        ]


class TestASuspensionDoesNotConsumeTheTimeout(SuspensionHarness):
    def test_a_suspension_extends_the_deadline(self):
        """(a) 30 min asleep, then 400 s more of boot: within a 600 s budget."""
        vm = SimulatedVM(healthy_after=400, suspend_on={10: 1800})
        result, _warn, _err = self.wait_for_ready(vm)

        self.assertTrue(result, "the suspended 30 min were counted as waiting")
        # The wall clock really did pass the 600 s budget.
        self.assertGreater(vm.suspended + vm.running, 600)

    def test_it_warns_once_however_many_suspensions(self):
        """(a, c) One warning, with the text the issue specifies."""
        vm = SimulatedVM(healthy_after=400, suspend_on={10: 1800, 30: 900})
        result, warn, _err = self.wait_for_ready(vm)

        self.assertTrue(result)
        self.assertEqual(self.suspension_warnings(warn), [WARNING_30_MIN])

    def test_a_genuine_timeout_still_fails_normally(self):
        """(b) Steady polls and a portal that never goes healthy: a plain
        timeout, after the full 600 s, with no talk of suspension."""
        vm = SimulatedVM(healthy_after=None)
        result, warn, err = self.wait_for_ready(vm)

        self.assertFalse(result)
        err.assert_called_with(TIMEOUT_LINE)
        self.assertEqual(self.suspension_warnings(warn), [])
        self.assertGreaterEqual(vm.running, 600)
        self.assertLess(vm.running, 600 + 10)

    def test_a_timeout_after_a_suspension_says_so(self):
        """A wait that still runs out names the suspension, so the failure
        does not read as an unhealthy Liferay alone."""
        vm = SimulatedVM(healthy_after=None, suspend_on={10: 1800})
        result, _warn, err = self.wait_for_ready(vm)

        self.assertFalse(result)
        message = err.call_args.args[0]
        self.assertTrue(message.startswith(TIMEOUT_LINE), message)
        self.assertIn("suspended for ~30 min during the wait", message)
        # 600 s of RUNNING time were still given to the portal.
        self.assertGreaterEqual(vm.running, 600)


class TestAWedgedDaemonStillTimesOut(SuspensionHarness):
    def test_a_daemon_blocking_every_poll_still_fails(self):
        """A wedged daemon: every docker call blocks ~70 s, so EVERY poll
        interval exceeds the gap threshold and looks like a suspension.
        Without a cap on excluded time the wait would never end; with it,
        the wait fails after the 60 min cap plus the 600 s timeout."""
        stall = 70.0
        vm = SimulatedVM(
            healthy_after=None,
            suspend_on=dict.fromkeys(range(1, MAX_SLEEPS + 1), stall),
        )
        result, warn, err = self.wait_for_ready(vm)

        self.assertFalse(result)
        message = err.call_args.args[0]
        self.assertTrue(message.startswith(TIMEOUT_LINE), message)
        self.assertIn("suspended for ~60 min during the wait", message)
        self.assertIn("any further gaps were counted", message)
        self.assertEqual(len(self.suspension_warnings(warn)), 1)
        # Bounded: (3600 + 600) / (5 + 70) is 56 polls; a little slack.
        self.assertLess(vm.sleeps, 60)


class TestCmdWaitSharesTheSuspensionAcrossStages(SuspensionHarness):
    def test_a_suspension_in_the_health_stage_does_not_starve_http(self):
        """cmd_wait runs stages against one budget. A suspension in the first
        must be excluded from the later ones too, not just the first."""
        vm = SimulatedVM(healthy_after=500, http_after=560, suspend_on={10: 1800})
        p_time, p_sleep, p_run, p_http, p_warn, p_err, p_die = self._patched(vm)
        with p_time, p_sleep, p_run, p_http, p_warn as warn, p_err, p_die as die:
            result = self.readiness.cmd_wait(
                "test-project",
                timeout=600,
                probe_url="http://localhost:8080",
                cpu_idle_checks=1,
            )

        die.assert_not_called()
        self.assertTrue(result)
        self.assertEqual(self.suspension_warnings(warn), [WARNING_30_MIN])

    def test_cmd_wait_names_the_suspension_when_it_dies(self):
        vm = SimulatedVM(healthy_after=None, suspend_on={10: 1800})
        p_time, p_sleep, p_run, p_http, p_warn, p_err, p_die = self._patched(vm)
        with p_time, p_sleep, p_run, p_http, p_warn, p_err, p_die as die:
            with self.assertRaises(SystemExit):
                self.readiness.cmd_wait(
                    "test-project", timeout=600, probe_url="http://localhost:8080"
                )

        message = die.call_args.args[0]
        self.assertIn("failed to become ready within 600s.", message)
        self.assertIn("suspended for ~30 min during the wait", message)


class TestReadinessClock(unittest.TestCase):
    def test_a_clock_stepped_backwards_counts_as_no_time(self):
        reads = iter([1000.0, 1005.0, 900.0, 905.0])
        with patch("time.time", side_effect=lambda: next(reads)):
            clock = ReadinessClock()
            self.assertEqual(clock.tick(), 5.0)
            self.assertEqual(clock.tick(), 5.0)
            self.assertEqual(clock.tick(), 10.0)
        self.assertEqual(clock.suspended, 0.0)

    def test_long_legitimate_work_can_be_counted(self):
        reads = iter([0.0, 300.0])
        with patch("time.time", side_effect=lambda: next(reads)):
            clock = ReadinessClock()
            self.assertEqual(clock.tick(detect_gap=False), 300.0)
        self.assertEqual(clock.suspended, 0.0)


if __name__ == "__main__":
    unittest.main()
