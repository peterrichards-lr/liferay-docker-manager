import abc
import logging
from typing import Any


class PipelineContext:
    """Shared state container for pipeline execution."""

    def __init__(self, **kwargs):
        self.data: dict[str, Any] = dict(kwargs)
        self.errors: list[Exception] = []
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
        """Optional rollback logic if a subsequent stage fails."""
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
            except Exception as e:
                self.logger.error(
                    f"Error during rollback of stage {stage.name}: {e}", exc_info=True
                )
                # Keep rolling back other stages even if one fails
