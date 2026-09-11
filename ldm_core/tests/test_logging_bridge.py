"""`logging` records belong in the trace log, not on the console (LDM-#1669).

`docs/reference/logging-strategy.md` defines a silent file tier --
`UI.trace()` -> `~/.ldm/last-command.log`. The `logging` calls in
`ldm_core/pipelines/base.py` were never connected to it, so their records were
discarded (`debug`) or handled by `logging.lastResort`: a bare stderr handler
fixed at WARNING with no formatter and no off switch. That is what printed a
traceback over a controlled `UI.die` (LDM-#1668), and what left the demoted
record with nowhere to go afterwards.

Isolation: every test points `LDM_HOME` at a temporary directory. Per LDM-#1349
that is the only primitive that redirects LDM's state dir from outside the
process -- setting `HOME` alone is ignored, because `get_actual_home()`
reconstructs the path from `SUDO_USER`/`USER` on macOS. A test that got this
wrong would truncate the developer's real `~/.ldm/last-command.log`.
"""

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.ui import UI


class TestLoggingBridge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(UI.reset)

        patcher = patch.dict(os.environ, {"LDM_HOME": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

        UI.init_trace_log(["ldm", "test"])
        self.log = Path(self._tmp.name) / ".ldm" / "last-command.log"

    def _read(self):
        return self.log.read_text(encoding="utf-8")

    def test_the_trace_log_was_actually_created(self):
        """Guards every assertion below from passing vacuously."""
        self.assertTrue(self.log.exists(), "no trace log; LDM_HOME isolation failed")
        self.assertIn("LDM Trace Log Started", self._read())

    def test_a_debug_record_reaches_the_trace_log(self):
        logging.getLogger("pipeline.demo").debug("a breadcrumb")
        self.assertIn("a breadcrumb", self._read())

    def test_the_record_is_formatted_with_level_and_logger_name(self):
        logging.getLogger("pipeline.demo").debug("formatted")
        written = self._read()
        self.assertIn("DEBUG", written)
        self.assertIn("pipeline.demo", written)

    def test_a_traceback_is_kept(self):
        """The whole point: LDM-#1668's demoted exc_info must land somewhere."""
        try:
            raise SystemExit(1)
        except SystemExit:
            logging.getLogger("pipeline.demo").debug("aborted", exc_info=True)

        written = self._read()
        self.assertIn("Traceback (most recent call last)", written)
        self.assertIn("SystemExit", written)

    def test_a_handler_is_attached_so_lastresort_cannot_fire(self):
        """The mechanism that takes records off the console.

        `logging.lastResort` -- the bare stderr handler that dumped LDM-#1668's
        traceback -- is used ONLY when a record finds no handler anywhere in the
        hierarchy. Attaching any handler retires it.

        Asserted on the handler list rather than by capturing stderr: under
        pytest the logging plugin attaches its own root handlers, so a
        stderr-capture test passes whether or not this bridge exists. It could
        never fail, which is worse than not having it (LDM-#1529).
        """
        self.assertIsNotNone(UI._log_bridge, "the bridge was never installed")
        self.assertIn(
            UI._log_bridge,
            logging.getLogger().handlers,
            "the bridge is not on the root logger, so lastResort still handles "
            "records that find no other handler (LDM-#1668)",
        )

    def test_installing_twice_does_not_duplicate_records(self):
        UI._install_log_bridge()
        UI._install_log_bridge()
        logging.getLogger("pipeline.demo").debug("once please")
        self.assertEqual(self._read().count("once please"), 1)

    def test_third_party_debug_does_not_flood_the_log(self):
        """Root stays at WARNING; only LDM's own logger drops to DEBUG.

        The LDM record is emitted alongside deliberately: without it this is a
        bare `assertNotIn` that would also pass against an empty log, i.e. when
        the bridge is absent entirely.
        """
        logging.getLogger("urllib3.connectionpool").debug("chatty internals")
        logging.getLogger("pipeline.demo").debug("ldm record")

        written = self._read()
        self.assertIn("ldm record", written, "the log is empty; nothing was proved")
        self.assertNotIn("chatty internals", written)

    def test_third_party_warnings_are_captured(self):
        """They used to hit raw stderr; the file tier takes them now."""
        logging.getLogger("urllib3.connectionpool").warning("something odd")
        self.assertIn("something odd", self._read())

    def test_reset_detaches_the_handler(self):
        """Test isolation: a stale handler would write into the next test's log.

        Installs explicitly first, so this asserts that `reset()` *removes*
        something rather than passing because nothing was ever attached.
        """
        UI._install_log_bridge()
        self.assertIsNotNone(UI._log_bridge)

        UI.reset()
        self.assertIsNone(UI._log_bridge)
        self.assertNotIn(
            "_TraceLogHandler",
            repr(logging.getLogger().handlers),
            "the bridge is still attached to the root logger after reset()",
        )


class TestTheRealPipelineUsesIt(unittest.TestCase):
    """End to end: a controlled refusal logs to file and stays off the console."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(UI.reset)
        patcher = patch.dict(os.environ, {"LDM_HOME": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        UI.init_trace_log(["ldm", "import", "demo"])
        self.log = Path(self._tmp.name) / ".ldm" / "last-command.log"

    def test_a_dying_stage_is_recorded_in_the_trace_log(self):
        import io
        from contextlib import redirect_stderr

        from ldm_core.pipelines.base import Pipeline, PipelineContext, PipelineStage

        class _Dying(PipelineStage):
            @property
            def name(self):
                return "ImportValidationStage"

            def execute(self, context):
                raise SystemExit(1)

        pipeline = Pipeline("import")
        pipeline.add_stage(_Dying())

        buf = io.StringIO()
        with redirect_stderr(buf), self.assertRaises(SystemExit):
            pipeline.run(PipelineContext())

        written = self.log.read_text(encoding="utf-8")
        self.assertIn("ImportValidationStage aborted", written)
        self.assertIn("Traceback (most recent call last)", written)
        self.assertEqual(buf.getvalue(), "", "the traceback reached the console again")


if __name__ == "__main__":
    unittest.main()
