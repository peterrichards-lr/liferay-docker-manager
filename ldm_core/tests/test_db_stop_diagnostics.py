"""A failed `ldm db start`/`db stop` reports what docker said (LDM-#1603).

LDM-#1547 added guards that re-check `is_running` after the command, because
`DockerService.start`/`.stop` discard failure. The guards detect that something
went wrong; until now they discarded *why*.

That was not academic. `ldm db stop` failed once on debian during v2.21.0-pre.2
platform verification and passed on a re-run with no code change. The cause
could not be established afterwards, because docker's stderr had been thrown
away at the call site.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core.handlers.database import DatabaseService, _docker_failure_detail


class TestTheDetailMessage(unittest.TestCase):
    """_docker_failure_detail is module-level so the rule is called, not restated."""

    def _res(self, stderr="", stdout=""):
        res = MagicMock()
        res.stderr = stderr
        res.stdout = stdout
        return res

    def test_stderr_is_surfaced(self):
        detail = _docker_failure_detail(self._res(stderr="Error: No such container: x"))
        self.assertIn("No such container: x", detail)

    def test_none_is_distinguished_from_silence(self):
        """run_command returns None for a non-zero exit AND for a timeout.

        Reporting that narrows the cause; reporting 'no output' would not.
        """
        none_detail = _docker_failure_detail(None)
        silent_detail = _docker_failure_detail(self._res())
        self.assertNotEqual(none_detail, silent_detail)
        self.assertIn("timed out", none_detail)

    def test_stdout_is_used_when_stderr_is_empty(self):
        detail = _docker_failure_detail(self._res(stdout="only-on-stdout"))
        self.assertIn("only-on-stdout", detail)

    def test_stderr_wins_over_stdout(self):
        detail = _docker_failure_detail(
            self._res(stderr="the real error", stdout="noise")
        )
        self.assertIn("the real error", detail)
        self.assertNotIn("noise", detail)

    def test_it_never_returns_empty(self):
        """UI.die(details=...) with an empty string is worse than no details."""
        for res in (None, self._res(), self._res(stderr="   "), self._res(stdout="  ")):
            with self.subTest(res=res):
                self.assertTrue(_docker_failure_detail(res).strip())


class TestTheGuardsReportIt(unittest.TestCase):
    """The detail must actually reach UI.die, not merely be computable."""

    def setUp(self):
        self.manager = MagicMock()
        self.manager.target = None
        self.service = DatabaseService(self.manager)

    def _run(self, command, running_before, running_after, res):
        """Drive cmd_start/cmd_stop with a container that refuses to change state."""
        with patch(
            "ldm_core.handlers.database._shared_db_engines", return_value=["postgresql"]
        ):
            with patch("ldm_core.docker_service.DockerService") as docker:
                docker.exists.return_value = True
                docker.is_running.side_effect = [running_before, running_after]
                docker.start.return_value = res
                docker.stop.return_value = res
                with patch("ldm_core.handlers.database.UI") as ui:
                    ui.die.side_effect = SystemExit(3)
                    with self.assertRaises(SystemExit):
                        command()
                    return ui.die.call_args

    def test_stop_reports_dockers_error(self):
        res = MagicMock()
        res.stderr = "Error response from daemon: cannot stop container"
        res.stdout = ""
        call = self._run(self.service.cmd_stop, True, True, res)
        self.assertIn(
            "cannot stop container",
            call.kwargs.get("details", ""),
            "the stop guard fired without saying what docker reported",
        )
        self.assertEqual(call.kwargs.get("exit_code"), 3)

    def test_start_reports_dockers_error(self):
        res = MagicMock()
        res.stderr = "Error response from daemon: port is already allocated"
        res.stdout = ""
        call = self._run(self.service.cmd_start, False, False, res)
        self.assertIn(
            "port is already allocated",
            call.kwargs.get("details", ""),
            "the start guard fired without saying what docker reported",
        )

    def test_a_none_result_still_produces_a_detail(self):
        """The debian case: no captured output, but say so rather than nothing."""
        call = self._run(self.service.cmd_stop, True, True, None)
        self.assertTrue(call.kwargs.get("details", "").strip())


if __name__ == "__main__":
    unittest.main()
