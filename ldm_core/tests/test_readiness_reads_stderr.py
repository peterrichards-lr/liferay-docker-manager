"""The readiness scan must read both streams (LDM-#2188).

Tomcat writes its own startup lines through juli to **stderr**. `run_command`
returns stdout only, so the scan could not see them at all:

    Catalina.load  Server initialization in [428] ms      stderr  -> missed
    Catalina.start Server startup in [116043] ms          stderr  -> missed
    DBInitUtil / ModuleFrameworkImpl / BundleSiteInitializer
                                                          stdout  -> found

Measured on Windows against v2.26.6-pre.3, on a 223-line container log -- far
inside any tail window, with both lines present in `docker logs ... 2>&1` and
neither recorded by LDM.

The second string is also what sets `ready_by_logs` (LDM-385), added on the
grounds it is "often faster/more reliable than the Docker healthcheck in CI".
It is on stderr, so that branch has never been reachable. Nothing failed --
the healthcheck is a correct fallback -- which is why it went unnoticed.

These assert the *plumbing*, not the matcher. A test that hands the lines to
`find_milestones` directly passes today and proves nothing; that is exactly the
gap that let this ship.
"""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from ldm_core.utils import run_command

TOMCAT_READY = (
    "14-Oct-2026 12:31:24.375 INFO [main] "
    "org.apache.catalina.startup.Catalina.start Server startup in [116043] milliseconds"
)


class TestMergeStderrReachesTheSubprocess(unittest.TestCase):
    """The flag has to arrive at `subprocess.run` as STDOUT redirection; a
    parameter accepted and dropped is the defect family this fixes."""

    def _stderr_dest(self, **kwargs):
        captured = {}

        def fake_run(*args, **kw):
            captured.update(kw)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch("ldm_core.utils.subprocess.run", side_effect=fake_run):
            run_command(["docker", "logs", "x"], check=False, **kwargs)
        return captured.get("stderr")

    def test_merge_stderr_redirects_stderr_into_stdout(self):
        self.assertEqual(self._stderr_dest(merge_stderr=True), subprocess.STDOUT)

    def test_the_default_is_unchanged(self):
        """Hundreds of callers parse the return value. Folding diagnostics in
        by default would corrupt every one that expects clean stdout."""
        self.assertEqual(self._stderr_dest(), subprocess.PIPE)

    def test_a_tomcat_line_on_stderr_comes_back_when_merged(self):
        """The whole point: the line LDM could not see."""

        def fake_run(*args, **kw):
            result = MagicMock()
            result.returncode = 0
            # STDOUT redirection means the child's stderr arrives ON stdout.
            result.stdout = (
                TOMCAT_READY if kw.get("stderr") is subprocess.STDOUT else ""
            )
            result.stderr = (
                "" if kw.get("stderr") is subprocess.STDOUT else TOMCAT_READY
            )
            return result

        with patch("ldm_core.utils.subprocess.run", side_effect=fake_run):
            merged = run_command(
                ["docker", "logs", "x"], check=False, merge_stderr=True
            )
            plain = run_command(["docker", "logs", "x"], check=False)

        self.assertIn("Server startup in", merged or "")
        self.assertNotIn(
            "Server startup in",
            plain or "",
            "stdout-only must still drop it -- otherwise this test cannot "
            "distinguish the fix from the defect",
        )


class TestTheReadinessScanAsksForBothStreams(unittest.TestCase):
    """A source-text check, deliberately narrow: driving the whole readiness
    loop needs Docker and minutes of runtime. What this catches is the flag
    being dropped from the one call site that needs it."""

    def test_the_log_read_passes_merge_stderr(self):
        import inspect

        from ldm_core.runtime import readiness

        source = inspect.getsource(readiness)
        call = source[source.index('"logs", *log_window') :][:400]
        self.assertIn(
            "merge_stderr=True",
            call,
            "the readiness log scan reads stdout only, so every Tomcat line "
            "-- including the one that sets ready_by_logs -- is invisible "
            "(LDM-#2188)",
        )


if __name__ == "__main__":
    unittest.main()
