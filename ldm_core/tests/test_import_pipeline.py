from unittest.mock import MagicMock, patch

import pytest

from ldm_core.pipelines.import_pipeline import (
    BackupStateStage,
    ImportPipeline,
    ImportPipelineContext,
    ValidationStage,
)


def test_import_pipeline_initialization():
    pipeline = ImportPipeline()
    assert len(pipeline.stages) == 8
    assert isinstance(pipeline.stages[0], ValidationStage)


@patch("ldm_core.pipelines.import_pipeline.UI")
@patch("ldm_core.pipelines.import_pipeline.calculate_sha256")
def test_validation_stage_file_not_found(mock_sha, mock_ui, tmp_path):
    stage = ValidationStage()
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
