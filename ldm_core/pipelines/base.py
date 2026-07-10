from typing import Any

from ldm_core.ui import UI


class PipelineContext:
    def __init__(self, manager, **kwargs):
        self.manager = manager
        self._data: dict[str, Any] = kwargs
        self.aborted: bool = False
        self.error: Exception | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value


class PipelineStage:
    def execute(self, context: PipelineContext) -> bool:
        """Executes the pipeline stage. Returns True if successful, False if pipeline should abort."""
        raise NotImplementedError

    def rollback(self, context: PipelineContext) -> None:
        """Optional rollback logic if a subsequent stage fails."""
        pass


class Pipeline:
    def __init__(self, name: str = "Pipeline"):
        self.name = name
        self.stages: list[PipelineStage] = []

    def add_stage(self, stage: PipelineStage) -> "Pipeline":
        self.stages.append(stage)
        return self

    def execute(self, context: PipelineContext) -> bool:
        executed_stages = []
        for stage in self.stages:
            if context.aborted:
                break
            try:
                success = stage.execute(context)
                executed_stages.append(stage)
                if not success:
                    UI.error(
                        f"Pipeline {self.name} aborted at stage {stage.__class__.__name__}"
                    )
                    self._rollback(executed_stages, context)
                    return False
            except Exception as e:
                UI.error(f"Error in {self.name} stage {stage.__class__.__name__}: {e}")
                context.error = e
                self._rollback(executed_stages, context)
                return False
        return True

    def _rollback(
        self, executed_stages: list[PipelineStage], context: PipelineContext
    ) -> None:
        for stage in reversed(executed_stages):
            try:
                stage.rollback(context)
            except Exception as e:
                UI.error(f"Error during rollback in {stage.__class__.__name__}: {e}")
