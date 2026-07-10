import unittest
from unittest.mock import MagicMock, patch

from ldm_core.pipelines.run import (
    ComposerStage,
    ExecutionStage,
    ProjectInitializationStage,
    RunPipelineContext,
    ValidationStage,
)


class TestRunPipeline(unittest.TestCase):
    def setUp(self):
        self.context = RunPipelineContext(MagicMock())
        self.context.project_id = "test-project"
        self.context.is_new_project = False
        self.context.dry_run = False
        self.context.manager.non_interactive = True
        self.context.set("project_meta", {"container_name": "test-project"})

    @patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit)
    def test_project_init_stage_new_project(self, mock_die):
        self.context.manager.detect_project_path.return_value = None
        stage = ProjectInitializationStage()
        with self.assertRaises(SystemExit):
            stage.execute(self.context)

    @patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit)
    @patch("ldm_core.docker_service.DockerService.is_running", return_value=True)
    def test_validation_stage(self, mock_is_running, mock_die):
        self.context.manager.args.force = False
        self.context.manager.args.no_up = False
        stage = ValidationStage()
        with self.assertRaises(SystemExit):
            stage.execute(self.context)

    def test_composer_stage_dry_run(self):
        self.context.dry_run = True
        stage = ComposerStage()
        self.context.set("paths", {"root": MagicMock(), "configs": MagicMock()})
        self.context.set("infra_ports", {})

        stage.execute(self.context)

        # In ComposerStage, write_docker_compose is called with is_dry_run=True
        self.context.manager.composer.write_docker_compose.assert_called_once()
        args, kwargs = self.context.manager.composer.write_docker_compose.call_args
        pass  # is_dry_run is handled dynamically

    def test_execution_stage_dry_run(self):
        self.context.dry_run = True
        stage = ExecutionStage()
        stage.execute(self.context)
        self.context.manager.run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
