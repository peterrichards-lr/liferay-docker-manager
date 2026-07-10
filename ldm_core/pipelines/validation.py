from ldm_core.pipelines.base import PipelineContext, PipelineStage
from ldm_core.utils import UI


class ValidationStage(PipelineStage):
    """Shared stage to validate environment health before pipeline execution."""

    def execute(self, context: PipelineContext) -> None:
        manager = getattr(context, "manager", None)
        if not manager:
            manager = context.get("manager")
        if not manager:
            return

        # Hook DiagnosticsService preflight checks
        from ldm_core.diagnostics.doctor import DoctorRunner

        project_id = context.get("project_id")

        handler = getattr(manager, "handler", None)
        if not handler:
            return

        # Run silent checks
        runner = DoctorRunner(handler, project_id=project_id)

        # We can check docker runtime or tooling
        runner._check_tooling_and_integrity()

        # Check for errors
        errors = [msg for status, msg, _ in runner.results if status == "error"]

        if errors:
            for err in errors:
                UI.error(f"Preflight Check Failed: {err}")
            UI.die("Validation failed. Run 'ldm doctor' for more details.")
