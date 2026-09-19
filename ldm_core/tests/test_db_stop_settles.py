"""`ldm db stop` must let the daemon settle before calling a stop a failure.

LDM-#1805, continuing LDM-#1615.

## The measurement

`docker stop` is synchronous and returns success -- it echoes the container
name -- but `docker ps` can still list the container for a moment afterwards.
From a real CI failure (v2.22.0, Fedora), both engines in one run:

    [CMD] docker stop liferay-db-global
    [STDOUT] liferay-db-global
    [CMD] docker ps -q -f name=^liferay-db-global$
                                       <- gone, correctly

    [CMD] docker stop liferay-db-mysql-global
    [STDOUT] liferay-db-mysql-global    <- the stop SUCCEEDED
    [CMD] docker ps -q -f name=^liferay-db-mysql-global$
    [STDOUT] 92e4917f4414               <- still listed

So the stop worked and the verification was too eager. MySQL loses that race
more often because InnoDB shutdown makes it the slower of the two to be reaped.

At least seven CI failures across distros *and* workflows -- debian and ubuntu
(LDM-#1615), fedora, rockylinux and Release E2E (LDM-#1805) -- every one
passing on a re-run with no code change. Which is what a race looks like from
the outside, and why dropping a distro would have relocated it rather than
fixed it.

## What must NOT be lost

The guard exists because `DockerService.stop` discards failure (LDM-#1547), and
reporting a stop that did not happen is the defect it was written for. A fixed
sleep would paper over the race *and* slow every correct stop; returning early
would restore the original bug. So the wait is bounded and still fails.
"""

import unittest
from typing import Any
from unittest.mock import patch

from ldm_core.docker_service import DockerService


class TheWaitIsBounded(unittest.TestCase):
    """Drives the real `wait_until_stopped` against a scripted daemon."""

    def _wait(self, running_sequence, real_sleep=False, **kwargs):
        """`running_sequence` is what successive is_running calls return.

        `real_sleep=True` leaves `time.sleep` alone. Patching it out makes the
        loop spin as fast as the CPU allows, so any assertion about *how often*
        it polls would be measuring the stub rather than the pacing -- which is
        exactly what a first draft of this file did, reporting 6560 polls in
        50ms and calling it unbounded.
        """
        calls = {"n": 0}

        def fake_is_running(*_args, **_kwargs):
            i = calls["n"]
            calls["n"] += 1
            return running_sequence[min(i, len(running_sequence) - 1)]

        patches = [
            patch.object(DockerService, "is_running", side_effect=fake_is_running)
        ]
        if not real_sleep:
            patches.append(patch("ldm_core.docker_service.time.sleep"))

        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = DockerService.wait_until_stopped("db", **kwargs)
        return result, calls["n"]

    def test_an_already_stopped_container_returns_immediately(self):
        """The common case must not pay for the rare one."""
        ok, polls = self._wait([False])

        self.assertTrue(ok)
        self.assertEqual(polls, 1, "a settled container was polled more than once")

    def test_a_container_that_settles_late_is_accepted(self):
        """This is the bug: listed as running just after a successful stop."""
        ok, polls = self._wait([True, True, False])

        self.assertTrue(ok, "a stop that succeeded was reported as a failure")
        self.assertGreater(polls, 1)

    def test_a_container_that_never_stops_is_still_reported(self):
        """The guard must keep its teeth -- LDM-#1547 exists because a stop
        that did not happen was being reported as success."""
        ok, _ = self._wait([True], timeout=0.01, interval=0.001)

        self.assertFalse(ok)

    def test_it_gives_up_rather_than_hanging(self):
        """An unbounded wait would turn a visible failure into a hung command."""
        ok, polls = self._wait([True], real_sleep=True, timeout=0.05, interval=0.01)

        self.assertFalse(ok)
        # ~5 polls at 10ms over a 50ms deadline. Generous, because CI timing is
        # not precise -- the point is that it paces itself rather than busy-waits.
        self.assertLess(polls, 50, "busy-waited instead of pacing with sleep")


class TheStopGuardUsesIt(unittest.TestCase):
    """LDM-#1805: the fix is worthless if `cmd_stop` still reads eagerly.

    Driven through the real `cmd_stop`, not by reading its source -- a wiring
    test that grepped for the call would pass against the original code too,
    since a call to *something* was always there.
    """

    def _run_stop(self, settles):
        from types import SimpleNamespace

        from ldm_core.handlers.database import DatabaseService

        svc = DatabaseService.__new__(DatabaseService)
        svc.manager = SimpleNamespace(target=None)  # type: ignore[assignment]
        died: dict[str, Any] = {}

        def capture(message, *_a, **kw):
            died["msg"] = str(message)
            died["code"] = kw.get("exit_code")
            raise SystemExit(kw.get("exit_code", 1))

        with (
            patch.object(DockerService, "exists", return_value=True),
            patch.object(DockerService, "is_running", return_value=True),
            patch.object(DockerService, "stop", return_value="db"),
            patch.object(DockerService, "wait_until_stopped", return_value=settles),
            patch("ldm_core.ui.UI.die", side_effect=capture),
            patch("ldm_core.ui.UI.success"),
            patch("ldm_core.ui.UI.detail"),
            patch("ldm_core.ui.UI.warning"),
        ):
            try:
                svc.cmd_stop()
            except SystemExit:
                pass
        return died

    def test_a_late_settling_stop_is_not_refused(self):
        self.assertEqual(self._run_stop(settles=True), {})

    def test_a_stop_that_never_settles_still_refuses_with_exit_3(self):
        died = self._run_stop(settles=False)

        self.assertEqual(died.get("code"), 3)
        self.assertIn("still running", died.get("msg", ""))


if __name__ == "__main__":
    unittest.main()
