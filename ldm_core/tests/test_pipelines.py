from ldm_core.pipelines.base import Pipeline, PipelineContext, PipelineStage


class MockSuccessStage(PipelineStage):
    def __init__(self, name: str = "MockSuccessStage"):
        self._name = name
        self.executed = False
        self.rolled_back = False

    @property
    def name(self) -> str:
        return self._name

    def execute(self, context: PipelineContext) -> None:
        self.executed = True
        context.set(f"{self.name}_executed", True)

    def rollback(self, context: PipelineContext) -> None:
        self.rolled_back = True
        context.set(f"{self.name}_rolled_back", True)


class MockFailStage(PipelineStage):
    def __init__(self, name: str = "MockFailStage", fail_on_rollback: bool = False):
        self._name = name
        self.fail_on_rollback = fail_on_rollback
        self.executed = False
        self.rolled_back = False

    @property
    def name(self) -> str:
        return self._name

    def execute(self, context: PipelineContext) -> None:
        self.executed = True
        raise ValueError(f"Forced failure in {self.name}")

    def rollback(self, context: PipelineContext) -> None:
        self.rolled_back = True
        if self.fail_on_rollback:
            raise RuntimeError(f"Forced rollback failure in {self.name}")


def test_pipeline_context_operations():
    context = PipelineContext(initial="value")
    assert context.get("initial") == "value"

    context.set("key", "test")
    assert context.get("key") == "test"
    assert context.get("missing", "default") == "default"

    context.update({"a": 1, "b": 2})
    assert context.get("a") == 1
    assert context.get("b") == 2

    assert not context.stopped
    assert len(context.errors) == 0


def test_pipeline_successful_execution():
    context = PipelineContext()
    pipeline = Pipeline("test_success")

    stage1 = MockSuccessStage("Stage1")
    stage2 = MockSuccessStage("Stage2")

    pipeline.add_stage(stage1)
    pipeline.add_stage(stage2)

    result = pipeline.run(context)

    assert result is True
    assert stage1.executed is True
    assert stage2.executed is True
    assert context.get("Stage1_executed") is True
    assert context.get("Stage2_executed") is True
    assert len(context.errors) == 0

    # Should not rollback on success
    assert stage1.rolled_back is False
    assert stage2.rolled_back is False


def test_pipeline_failure_triggers_rollback():
    context = PipelineContext()
    pipeline = Pipeline("test_failure")

    stage1 = MockSuccessStage("Stage1")
    stage2 = MockFailStage("Stage2")
    stage3 = MockSuccessStage("Stage3")

    pipeline.add_stage(stage1)
    pipeline.add_stage(stage2)
    pipeline.add_stage(stage3)

    result = pipeline.run(context)

    assert result is False
    assert stage1.executed is True
    assert stage2.executed is True
    assert stage3.executed is False  # Should not reach stage 3

    assert len(context.errors) == 1
    assert isinstance(context.errors[0], ValueError)

    # Rollback should be called on stage1 (which succeeded) but NOT stage2 (which failed)
    assert stage2.rolled_back is False
    assert stage1.rolled_back is True
    assert stage3.rolled_back is False


def test_pipeline_rollback_failure_continues_rollback():
    context = PipelineContext()
    pipeline = Pipeline("test_rollback_failure")

    stage1 = MockSuccessStage("Stage1")

    class MockRollbackFailStage(MockSuccessStage):
        def rollback(self, context: PipelineContext) -> None:
            raise RuntimeError("Rollback error")

    stage2 = MockRollbackFailStage("Stage2")
    stage3 = MockFailStage("Stage3")

    pipeline.add_stage(stage1)
    pipeline.add_stage(stage2)
    pipeline.add_stage(stage3)

    result = pipeline.run(context)

    assert result is False
    assert stage1.executed is True
    assert stage2.executed is True
    assert stage3.executed is True

    # Even though stage2 raised an error during rollback, stage1 should still be rolled back
    assert stage1.rolled_back is True


def test_pipeline_early_stop():
    context = PipelineContext()
    pipeline = Pipeline("test_early_stop")

    class StopStage(PipelineStage):
        def execute(self, context: PipelineContext) -> None:
            context.stopped = True

    stage1 = MockSuccessStage("Stage1")
    stage2 = StopStage()
    stage3 = MockSuccessStage("Stage3")

    pipeline.add_stage(stage1)
    pipeline.add_stage(stage2)
    pipeline.add_stage(stage3)

    result = pipeline.run(context)

    assert result is True
    assert stage1.executed is True
    assert stage3.executed is False  # Should not execute because context was stopped
    assert len(context.errors) == 0
