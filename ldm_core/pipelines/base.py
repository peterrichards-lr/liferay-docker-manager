import abc
import logging
from typing import Any


class PipelineContext:
    """Shared state container for pipeline execution."""

    def __init__(self, **kwargs):
        self.data: dict[str, Any] = dict(kwargs)
        # BaseException, not Exception: a stage that refuses via `UI.die`
        # raises SystemExit, and that is the most common failure recorded here
        # (LDM-#1630).
        self.errors: list[BaseException] = []
        self.stopped: bool = False

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def update(self, items: dict[str, Any]) -> None:
        self.data.update(items)


class PipelineStage(abc.ABC):
    """Abstract base class for a single stage in a pipeline."""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abc.abstractmethod
    def execute(self, context: PipelineContext) -> None:
        """Execute the primary logic of the stage."""
        pass

    def rollback(self, context: PipelineContext) -> None:
        """Optional rollback logic if a subsequent stage fails.

        Two rules, both learned the hard way (LDM-#1630):

        * **Undo what THIS stage created, on this stage.** Rollback only runs
          for stages that already executed, so cleanup parked on a later stage
          never fires for an earlier failure. The import pipeline's scratch
          directory leaked for exactly that reason: it was created by
          ExtractionStage and removed by BackupStateStage, three stages later.
        * **Delete only what this run brought into existence.** Rollback fires
          on ordinary validation refusals now, not just on unexpected
          exceptions, so a rollback that removes a directory the user supplied
          is data loss rather than cleanup. Record the fact at creation time
          (see ``root_existed`` in ``pipelines/run.py``) rather than inferring
          it from the absence of LDM metadata.

        A rollback must not raise ``SystemExit`` -- ``Pipeline.run`` swallows
        it to protect the original exit code, so calling ``UI.die`` here means
        the rest of the rollback silently does not happen.
        """
        return


class Pipeline:
    """Orchestrates the sequential execution of multiple PipelineStage instances."""

    def __init__(self, name: str, stages: list[PipelineStage] | None = None):
        self.name = name
        self.stages: list[PipelineStage] = stages or []
        self.executed_stages: list[PipelineStage] = []
        self.logger = logging.getLogger(f"pipeline.{name}")

    def add_stage(self, stage: PipelineStage) -> None:
        self.stages.append(stage)

    def run(self, context: PipelineContext) -> bool:
        """
        Executes all stages sequentially.
        If an exception is raised by any stage, the pipeline stops and
        triggers rollback on all successfully executed stages in reverse order.

        A stage can fail in two shapes, and both roll back:

        * an ``Exception`` -- rollback runs and ``run`` returns ``False``, so
          the caller decides what the failure means.
        * a ``BaseException`` that means "stop now": ``SystemExit``, which is
          what every ``UI.die`` raises, and ``KeyboardInterrupt``. Rollback
          runs and the original is **re-raised unchanged**.

        The re-raise is the whole point (LDM-#1630). ``UI.die`` is
        ``UI.error(...)`` followed by ``sys.exit(exit_code)``, and this method
        used to catch only ``Exception`` -- which ``SystemExit`` is not -- so
        no ``UI.die`` in any stage of any pipeline had ever triggered a
        rollback, and the process exited carrying whatever partial state the
        earlier stages had created. Converting these into a ``False`` return
        would fix the leak and break something worse: LDM's exit-code contract
        (0 success, 1 validation, 2 auth, 3 infrastructure/data, 4
        orchestration, 5 idempotent no-op, 126 invocation, 130 interrupt) is
        asserted on by callers and by
        ``scripts/verify_e2e_refactor.{sh,ps1}``. Roll back, then let the
        original exception carry its own code to the top level.
        """
        self.logger.debug(
            f"Starting pipeline: {self.name} with {len(self.stages)} stages"
        )

        for stage in self.stages:
            if context.stopped:
                self.logger.debug(
                    f"Pipeline {self.name} was stopped before stage: {stage.name}"
                )
                break

            try:
                self.logger.debug(f"Executing stage: {stage.name}")
                stage.execute(context)
                self.executed_stages.append(stage)
            except (SystemExit, KeyboardInterrupt) as e:
                # LDM-#1630. Roll back, then re-raise so the exit code (or the
                # interrupt) reaches the top level exactly as the stage meant
                # it. Deliberately narrower than `BaseException`: only these
                # two mean "the process is stopping", and catching the rest
                # (GeneratorExit, a bare BaseException from a C extension)
                # would be interfering rather than cleaning up.
                self.logger.error(f"Stage {stage.name} aborted: {e!r}", exc_info=True)
                context.errors.append(e)
                self._rollback(context)
                raise
            except Exception as e:
                self.logger.error(f"Stage {stage.name} failed: {e}", exc_info=True)
                context.errors.append(e)
                self._rollback(context)
                return False

        self.logger.debug(f"Pipeline {self.name} completed successfully")
        return True

    def _rollback(self, context: PipelineContext) -> None:
        """Triggers the rollback logic of executed stages in reverse order."""
        self.logger.debug(f"Triggering rollback for pipeline {self.name}")
        for stage in reversed(self.executed_stages):
            try:
                self.logger.debug(f"Rolling back stage: {stage.name}")
                stage.rollback(context)
            except (Exception, SystemExit) as e:
                # SystemExit is caught here too, and swallowed on purpose
                # (LDM-#1630). Rollbacks now run on the refusal path, and
                # several of them reach code that calls `UI.die`; letting that
                # escape would silently replace the exit code the caller is
                # about to see with the rollback's own. KeyboardInterrupt is
                # deliberately NOT caught, so a second Ctrl-C can still
                # abandon a rollback that is taking too long.
                self.logger.error(
                    f"Error during rollback of stage {stage.name}: {e}", exc_info=True
                )
                # Keep rolling back other stages even if one fails
