"""`ldm wait` refuses a project whose container is not running (LDM-#2429).

On an exited container it waited ~4 minutes for page specifications, then
printed "Liferay ready" read from the dead run's logs: an exited container
keeps its last `StartedAt`, so the LDM-#2346 log window still covered that
run's ready markers. No container was running at any point.
"""

import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.runtime.readiness import ReadinessService


class _Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = MagicMock()
        self.manager.detect_project_path.return_value = pathlib.Path(self.tmp.name)
        self.manager.read_meta.return_value = {
            "host_name": "aica-e2e.demo",
            "liferay_container_name": "aica-q4",
        }
        self.svc = ReadinessService(self.manager)

    def wait(self, state):
        """Runs the real cmd_wait with the container in `state`. Returns the
        exit code it died with (None if it did not), and whether readiness,
        log streaming or the HTTP probe were reached."""
        self.manager.get_container_state.return_value = state
        with (
            patch.object(
                ReadinessService, "_wait_for_ready", return_value=True
            ) as ready,
            patch("subprocess.Popen") as streamed,
            # the happy path ends in the timeout's log dump; keep it off Docker
            patch("subprocess.run"),
            patch("requests.get", side_effect=RuntimeError("probe reached")),
            patch("ldm_core.runtime.readiness.UI.die", side_effect=SystemExit) as died,
        ):
            try:
                self.svc.cmd_wait("aica-q4", timeout=1, stream_logs=True)
            except (SystemExit, RuntimeError):
                pass
        code = died.call_args.kwargs.get("exit_code", 1) if died.called else None
        return code, died, ready.called, streamed.called


class AStoppedContainerIsRefused(_Harness):
    def test_each_non_running_state_fails_before_any_wait(self):
        for state in ("exited", "created", "dead", "unknown"):
            with self.subTest(state=state):
                code, died, ready, streamed = self.wait(state)
                self.assertEqual(code, 1)
                self.assertIn("is not running", died.call_args.args[0])
                self.assertFalse(ready, "readiness scanned a dead run's logs")
                self.assertFalse(streamed, "log streaming started on a dead run")

    def test_the_message_names_the_state_and_how_to_start_it(self):
        _, died, _, _ = self.wait("exited")
        self.assertIn("container 'aica-q4' is exited", died.call_args.args[0])
        self.assertIn("ldm start", died.call_args.kwargs["tip"])

    def test_an_absent_container_is_called_absent(self):
        _, died, _, _ = self.wait("unknown")
        self.assertIn("does not exist", died.call_args.args[0])


class ARunningContainerStillWaits(_Harness):
    def test_running_and_restarting_reach_readiness(self):
        """A guard that fires on the happy path gets disabled within a week."""
        for state in ("running", "restarting"):
            with self.subTest(state=state):
                _, _, ready, _ = self.wait(state)
                self.assertTrue(ready)


class TheStateIsNotTheHealth(unittest.TestCase):
    def test_an_exited_container_with_stale_health_reads_exited(self):
        """`get_container_status` returns health first, and an exited
        container can keep a stale 'healthy'; the guard must not use it."""
        handler = BaseHandler(MagicMock())
        with (
            patch(
                "ldm_core.docker_service.DockerService.get_health",
                return_value="healthy",
            ),
            patch(
                "ldm_core.docker_service.DockerService.get_status",
                return_value="exited",
            ),
        ):
            self.assertEqual(handler.get_container_state("aica-q4"), "exited")
            self.assertEqual(handler.get_container_status("aica-q4"), "healthy")


if __name__ == "__main__":
    unittest.main()
