from unittest.mock import MagicMock, patch

import pytest

from ldm_core.pipelines.import_pipeline import (
    BackupStateStage,
    ImportPipeline,
    ImportPipelineContext,
    ImportValidationStage,
)


def test_import_pipeline_initialization():
    pipeline = ImportPipeline()
    assert len(pipeline.stages) == 9
    from ldm_core.pipelines.validation import ValidationStage as SharedValidationStage

    assert isinstance(pipeline.stages[0], SharedValidationStage)
    assert isinstance(pipeline.stages[1], ImportValidationStage)


@patch("ldm_core.pipelines.import_pipeline.UI")
@patch("ldm_core.pipelines.import_pipeline.calculate_sha256")
def test_validation_stage_file_not_found(mock_sha, mock_ui, tmp_path):
    stage = ImportValidationStage()
    manager = MagicMock()
    context = ImportPipelineContext(
        manager=manager, source_path=str(tmp_path / "nonexistent.zip")
    )

    mock_ui.die.side_effect = SystemExit(1)

    with pytest.raises(SystemExit):
        stage.execute(context)

    mock_ui.die.assert_called_once()


@patch("ldm_core.pipelines.import_pipeline.UI")
@patch("ldm_core.pipelines.import_pipeline.shutil.rmtree")
def test_backup_state_stage_rollback(mock_rmtree, mock_ui, tmp_path):
    stage = BackupStateStage()
    manager = MagicMock()
    context = ImportPipelineContext(manager=manager)

    temp_dir = tmp_path / ".ldm_temp"
    temp_dir.mkdir()
    context.set("temp_dirs", [temp_dir])

    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    context.set("project_path", project_dir)
    context.set("is_brand_new", True)

    stage.rollback(context)

    # Assert cleanup was called
    mock_rmtree.assert_any_call(temp_dir, ignore_errors=True)
    manager.safe_rmtree.assert_called_once_with(project_dir)


def test_project_setup_stage_loads_workspace_ldmrc(tmp_path):
    import json

    from ldm_core.pipelines.import_pipeline import ProjectSetupStage

    stage = ProjectSetupStage()
    manager = MagicMock()

    manager.setup_paths.return_value = {
        "root": tmp_path / "project_root",
    }
    manager.read_meta.return_value = {}
    manager.args = MagicMock()
    manager.args.project = "test_project"
    manager.args.host_name = None
    manager.args.ssl = None
    manager.args.port = None
    manager.non_interactive = True

    context = ImportPipelineContext(manager=manager, source_path=str(tmp_path))
    context.set("source_resolved", tmp_path)
    context.set("backup_dir", None)

    ldmrc_file = tmp_path / ".ldmrc"
    ldmrc_file.write_text(
        json.dumps(
            {
                "defaults": {
                    "host_name": "my-committed-domain.demo",
                    "ssl": True,
                    "tag": "2026.q1.4",
                }
            }
        )
    )

    stage.execute(context)

    # Assert that the written project meta inherited defaults from .ldmrc
    manager.write_meta.assert_called_once()
    written_meta = manager.write_meta.call_args[0][1]
    assert written_meta["host_name"] == "my-committed-domain.demo"
    assert written_meta["ssl"] == "true"
    assert written_meta["tag"] == "2026.q1.4"
