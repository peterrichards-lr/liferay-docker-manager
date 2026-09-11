"""A controlled refusal must not look like a crash (LDM-#1668).

LDM-#1630 taught `Pipeline.run` to catch `SystemExit` so a `UI.die` inside a
stage rolls back. The handler it added logged at `error` with `exc_info=True`,
which printed a full Python traceback on top of the message `UI.die` had
already written. Observed on a real host: a missing JDK, and separately a port
conflict on Linux, each presented to the user as a stack trace.

It could not be turned off. LDM configures no logging handlers, so
`logging.lastResort` -- a bare stderr `StreamHandler` fixed at WARNING --
handles anything at that level or above, and `-v/--verbose` sets `UI.VERBOSE`,
which has nothing to do with the logging module (LDM-#1669).

These tests assert on the *log records* rather than on captured stderr. The
record's level is the thing that decides whether the user sees it, and asserting
on it directly does not depend on how the root logger happens to be configured
while the suite runs.
"""

import logging

import pytest

from ldm_core.pipelines.base import Pipeline, PipelineContext, PipelineStage

CONSOLE_LEVEL = logging.WARNING
"""What `logging.lastResort` emits to stderr, which is what the user sees."""


class _Stage(PipelineStage):
    def __init__(self, name, boom):
        self._name = name
        self._boom = boom
        self.rolled_back = False

    @property
    def name(self):
        return self._name

    def execute(self, context):
        if self._boom is not None:
            raise self._boom

    def rollback(self, context):
        self.rolled_back = True


def _run_and_capture(caplog, stage, pipeline_name):
    pipeline = Pipeline(pipeline_name)
    pipeline.add_stage(stage)
    # Capture at DEBUG so the demoted record is still visible to the test even
    # though it is now below the console threshold.
    with caplog.at_level(logging.DEBUG, logger=f"pipeline.{pipeline_name}"):
        with pytest.raises(BaseException):  # noqa: B017 - SystemExit is the point
            pipeline.run(PipelineContext())
    return caplog.records


@pytest.mark.parametrize("code", [1, 2, 3, 4, 126])
def test_a_die_is_not_logged_loudly_enough_to_reach_the_console(caplog, code):
    """The regression itself: no record at or above the console threshold."""
    records = _run_and_capture(
        caplog, _Stage("Refuser", SystemExit(code)), f"die-{code}"
    )

    loud = [r for r in records if r.levelno >= CONSOLE_LEVEL]
    assert loud == [], (
        "A controlled UI.die produced a log record at "
        f"{logging.getLevelName(CONSOLE_LEVEL)} or above: "
        f"{[(r.levelname, r.getMessage()) for r in loud]}. "
        "logging.lastResort prints those straight to the user's terminal, so "
        "the refusal reads as a crash (LDM-#1668)."
    )


def test_ctrl_c_is_not_logged_loudly_either(caplog):
    """KeyboardInterrupt goes through the same handler."""
    records = _run_and_capture(
        caplog, _Stage("Interrupted", KeyboardInterrupt()), "ctrlc"
    )
    assert [r for r in records if r.levelno >= CONSOLE_LEVEL] == []


def test_the_diagnostic_is_kept_not_discarded(caplog):
    """Demoted, not deleted -- the traceback must survive for LDM-#1669."""
    records = _run_and_capture(caplog, _Stage("Refuser", SystemExit(1)), "kept")

    aborted = [r for r in records if "aborted" in r.getMessage()]
    assert aborted, (
        "the abort is no longer recorded at all; it was demoted, not removed"
    )
    assert all(r.exc_info is not None for r in aborted), (
        "the record lost its exc_info, so the traceback is gone rather than "
        "merely quiet -- a log destination (LDM-#1669) would have nothing to show"
    )


def test_an_unexpected_exception_still_logs_loudly_with_a_traceback(caplog):
    """The control. A real crash is not a controlled refusal and must stay loud.

    Without this, 'never log loudly' would pass by silencing genuine failures
    too, which is a worse bug than the one being fixed.
    """
    pipeline = Pipeline("boom")
    stage = _Stage("Exploder", ValueError("genuinely unexpected"))
    pipeline.add_stage(stage)

    with caplog.at_level(logging.DEBUG, logger="pipeline.boom"):
        assert pipeline.run(PipelineContext()) is False

    loud = [r for r in caplog.records if r.levelno >= CONSOLE_LEVEL]
    assert loud, "an unexpected exception must still be reported loudly"
    assert any(r.exc_info is not None for r in loud), (
        "an unexpected exception must still carry its traceback"
    )


def test_the_rollback_contract_from_ldm_1630_is_unchanged(caplog):
    """Guards against 'fixing' the noise by removing the handler."""
    survivor = _Stage("Earlier", None)
    stage = _Stage("Refuser", SystemExit(3))

    pipeline = Pipeline("contract")
    pipeline.add_stage(survivor)
    pipeline.add_stage(stage)

    context = PipelineContext()
    with pytest.raises(SystemExit) as excinfo:
        pipeline.run(context)

    assert excinfo.value.code == 3, "LDM-#1630: the exit code must survive"
    assert survivor.rolled_back is True, "LDM-#1630: earlier stages must roll back"
    assert context.errors, "LDM-#1630: the refusal must be recorded in context.errors"
