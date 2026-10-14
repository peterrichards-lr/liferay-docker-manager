"""A finished boot must not still say it is starting (LDM-#2187).

`"starting"` was the only value ever written, and only from inside the poll
loop. So a boot that succeeded left a file indistinguishable from one that
stalled. Captured on Windows against v2.26.6-pre.3, from a portal that had
been serving for minutes:

    {
      "status": "starting",
      "latest_milestone": "Site Initializer Running",
      "elapsed_seconds": 174
    }

The file is not debug output: `ldm dashboard` and the MCP tools read it to
answer "what is this project doing", and an agent has no containers to look
at -- the file is the answer. A failed boot read identically to a finished one.

Same family as LDM-#2155, LDM-#2171 and LDM-#2188: the verdict is computed --
the loop knows it succeeded, it prints `Liferay ready` on the next line -- and
never recorded.

These assert the writer and the exit paths separately, because the writer
being correct proves nothing about whether anything calls it on the path that
matters. That distinction is what LDM-#2188's probe exposed.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ldm_core.runtime.readiness import write_startup_status

PHASES = {"Tomcat Initializing": 1.0, "Tomcat Server Ready": 105.0}


class TestTheWriterRecordsATerminalState(unittest.TestCase):
    def _write(self, status, **kw):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / ".liferay-docker" / "startup-status.json"
            write_startup_status(f, status, PHASES, 107.0, **kw)
            return json.loads(f.read_text(encoding="utf-8"))

    def test_ready_is_recorded_as_ready(self):
        self.assertEqual(self._write("ready")["status"], "ready")

    def test_timeout_and_failure_are_distinguishable(self):
        """A stalled boot and a crashed one need different answers -- the whole
        point is that a reader can tell what happened."""
        self.assertNotEqual(
            self._write("timeout")["status"], self._write("failed")["status"]
        )

    def test_the_phases_survive_into_the_terminal_write(self):
        """A terminal state that discarded the timings would lose the only
        record of where a slow boot spent itself."""
        written = self._write("ready")
        self.assertEqual(written["milestone_seconds"]["Tomcat Server Ready"], 105.0)

    def test_it_records_when_it_was_written(self):
        """`elapsed_seconds` cannot distinguish a file written a minute ago
        from one written last week."""
        self.assertIn("updated_at", self._write("ready"))

    def test_it_never_raises(self):
        """Diagnostics must not fail a boot. An unwritable path is ignored."""
        write_startup_status(
            Path("/proc/nonexistent/startup-status.json"), "ready", PHASES, 1.0
        )

    def test_no_status_file_is_a_no_op(self):
        write_startup_status(None, "ready", PHASES, 1.0)


class TestEveryExitPathWritesOne(unittest.TestCase):
    """The writer being correct proves nothing about whether the success path
    calls it. LDM-#2188's neuter probe passed against broken code for exactly
    this reason -- every test built the value by hand.

    A source check, deliberately: reaching these paths needs Docker and a real
    boot. What it catches is an exit that forgets to record its verdict, which
    is the defect.
    """

    def _wait_source(self):
        import inspect

        from ldm_core.runtime import readiness

        src = inspect.getsource(readiness)
        start = src.index("def _wait_for_ready")
        return src[start:]

    def test_the_success_path_records_ready(self):
        src = self._wait_source()
        before_return = src[: src.index("                    return True")]
        self.assertIn(
            'write_startup_status(\n                        status_file, "ready"',
            before_return,
            "the success path returns without recording that the boot finished",
        )

    def test_the_timeout_path_records_a_terminal_state(self):
        src = self._wait_source()
        tail = src[src.index("Timed out waiting for Liferay") - 400 :]
        self.assertIn('"timeout"', tail)

    def test_the_container_exited_path_records_failure(self):
        src = self._wait_source()
        window = src[src.index('if container_state == "exited":') :][:400]
        self.assertIn('"failed"', window)

    def test_starting_is_no_longer_the_only_value_written(self):
        """The defect in one line: grep the module and there was exactly one
        status, and it was never terminal."""
        src = self._wait_source()
        for terminal in ('"ready"', '"timeout"', '"failed"'):
            with self.subTest(status=terminal):
                self.assertIn(terminal, src)


if __name__ == "__main__":
    unittest.main()
