"""A restarted container is not ready on its previous boot's log (LDM-#2346).

`docker logs` on a restarted container returns the previous run's output too.
The first readiness poll read `--tail 2000` with no lower bound, found the old
run's `Server startup in` line, and latched the sticky startup flag -- so
`ldm run` after `ldm stop` printed "Liferay ready (2s awaiting readiness)"
while the container was still `health: starting`.

These tests drive the real `_wait_for_ready` loop against a simulated daemon
whose `docker logs` honours `--since` and `--tail`, so a bounded window
genuinely drops the earlier run's lines and an unbounded one genuinely
returns them. Nothing here talks to Docker.
"""

import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.runtime.readiness import log_window_args, parse_started_at
from ldm_core.tests.test_readiness import MockRuntime

STARTUP = (
    "org.apache.catalina.startup.Catalina.start Server startup in [64925] milliseconds"
)

# Epoch seconds on the simulated daemon's clock.
PREVIOUS_BOOT = 1_700_000_000.0
THIS_START = PREVIOUS_BOOT + 3600.0
NEW_STARTUP_AT = THIS_START + 90.0


def _rfc3339_nano(epoch: float) -> str:
    """How the daemon prints `.State.StartedAt`: nine fractional digits, Z."""
    whole = int(epoch)
    nanos = round((epoch - whole) * 1e9)
    stamp = datetime.fromtimestamp(whole, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{stamp}.{nanos:09d}Z"


def _parse_rfc3339(value: str) -> float:
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z$", value)
    if not m:
        raise AssertionError(f"daemon would reject --since {value!r}")
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    frac = float(f"0.{m.group(2)}") if m.group(2) else 0.0
    return base.timestamp() + frac


class SimulatedDaemon:
    """Container logs, health and StartedAt, answering by the args received."""

    def __init__(self, *, restarted: bool, inspect_fails: bool = False):
        self.now = THIS_START + 1.0
        self.inspect_fails = inspect_fails
        self.lines: list[tuple[float, str]] = []
        if restarted:
            self.lines += [
                (PREVIOUS_BOOT + 1, "[LIFERAY] Starting Liferay DXP."),
                (PREVIOUS_BOOT + 80, STARTUP),
                (PREVIOUS_BOOT + 200, "Stopping service [Catalina]"),
            ]
        self.lines += [
            (THIS_START + 0.5, "[LIFERAY] Starting Liferay DXP."),
            (THIS_START + 30, "INFO  [main] Starting Liferay Digital Experience"),
            (NEW_STARTUP_AT, STARTUP),
        ]
        self.logs_calls: list[list[str]] = []

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return self.now

    def run_command(self, cmd, **_kwargs):
        if "inspect" in cmd:
            template = cmd[cmd.index("-f") + 1]
            if template == "{{.State.StartedAt}}":
                if self.inspect_fails:
                    return "Error: No such object: test-container"
                return _rfc3339_nano(THIS_START)
            return "starting"  # healthcheck never goes healthy in these tests
        if "logs" in cmd:
            self.logs_calls.append(list(cmd))
            visible = [(t, line) for t, line in self.lines if t <= self.now]
            if "--since" in cmd:
                since = cmd[cmd.index("--since") + 1]
                rel = re.fullmatch(r"(\d+)s", since)
                floor = self.now - int(rel.group(1)) if rel else _parse_rfc3339(since)
                visible = [(t, line) for t, line in visible if t >= floor]
            if "--tail" in cmd:
                visible = visible[-int(cmd[cmd.index("--tail") + 1]) :]
            return "\n".join(line for _, line in visible)
        return ""


class TestARestartIsNotReadyOnThePreviousBoot(unittest.TestCase):
    def setUp(self):
        from ldm_core.config import TargetNode

        patches = [
            patch("ldm_core.utils.reclaim_volume_permissions"),
            patch("requests.get", return_value=MagicMock(status_code=200)),
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
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.handler = MockRuntime()
        self.handler.detect_project_path = MagicMock(return_value=Path(tmp.name))  # type: ignore[method-assign]
        self.handler.args.browser = False

    def _wait(self, daemon: SimulatedDaemon):
        """Run the real wait loop; return the daemon time at which it said ready."""
        ready_at: list[float] = []
        readiness = self.handler.handler.readiness
        with (
            patch("time.time", side_effect=daemon.time),
            patch("time.sleep", side_effect=daemon.sleep),
            patch.object(self.handler, "run_command", side_effect=daemon.run_command),
            patch(
                "ldm_core.ui.UI.success",
                side_effect=lambda *_a, **_k: ready_at.append(daemon.now),
            ),
        ):
            result = readiness._wait_for_ready(
                {"container_name": "test-container"},
                "localhost",
                timeout=600,
                stream_status=True,
            )
        self.assertTrue(result, "the wait should end ready, not time out")
        self.assertTrue(ready_at, "readiness was never reported")
        return ready_at[0]

    def test_a_restart_waits_for_this_boots_startup_line(self):
        daemon = SimulatedDaemon(restarted=True)
        ready_at = self._wait(daemon)
        self.assertGreaterEqual(
            ready_at,
            NEW_STARTUP_AT,
            "reported ready on the PREVIOUS boot's 'Server startup in' line, "
            f"{NEW_STARTUP_AT - ready_at:.0f}s before this boot's appeared",
        )

    def test_the_first_window_is_bounded_by_the_containers_start(self):
        daemon = SimulatedDaemon(restarted=True)
        self._wait(daemon)
        first = daemon.logs_calls[0]
        self.assertIn("--since", first)
        self.assertEqual(first[first.index("--since") + 1], _rfc3339_nano(THIS_START))

    def test_a_fresh_container_is_still_detected(self):
        daemon = SimulatedDaemon(restarted=False)
        ready_at = self._wait(daemon)
        self.assertGreaterEqual(ready_at, NEW_STARTUP_AT)
        # ...and not long after: the log shortcut fires within a poll or two.
        self.assertLess(ready_at, NEW_STARTUP_AT + 30)

    def test_a_failed_inspect_falls_back_and_still_completes(self):
        """'Could not ask' must not break readiness -- it reverts to the
        unbounded backlog, so a fresh container still goes ready."""
        daemon = SimulatedDaemon(restarted=False, inspect_fails=True)
        ready_at = self._wait(daemon)
        self.assertGreaterEqual(ready_at, NEW_STARTUP_AT)
        self.assertEqual(daemon.logs_calls[0][-3:-1], ["--tail", "2000"])
        self.assertNotIn("--since", daemon.logs_calls[0])


class TestStartedAtParsing(unittest.TestCase):
    def test_the_daemons_nanosecond_format_is_accepted(self):
        value = "2026-11-11T10:20:30.123456789Z"
        self.assertEqual(parse_started_at(value + "\n"), value)

    def test_an_offset_is_accepted(self):
        self.assertIsNotNone(parse_started_at("2026-11-11T10:20:30+01:00"))

    def test_garbage_and_the_zero_time_are_refused(self):
        for raw in (
            None,
            "",
            "healthy",
            "Error: No such object",
            "0001-01-01T00:00:00Z",
        ):
            self.assertIsNone(parse_started_at(raw), raw)

    def test_later_polls_keep_the_sliding_window(self):
        """LDM-#2169's incremental window is unchanged after the first poll."""
        self.assertEqual(
            log_window_args(100.0, 145.0, "2026-11-11T10:20:30Z"), ["--since", "50s"]
        )
