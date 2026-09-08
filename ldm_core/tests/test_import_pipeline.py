from unittest.mock import MagicMock, patch

import pytest

from ldm_core.pipelines.import_pipeline import (
    ImportPipeline,
    ImportPipelineContext,
    ImportValidationStage,
    ProjectSetupStage,
)


def test_import_pipeline_initialization():
    pipeline = ImportPipeline()
    # LDM-#1635: was 10 until BackupStateStage was dissolved. Its `execute` was
    # an empty `pass`; it existed only to host cleanup for state that other
    # stages created, which is the arrangement LDM-#1630 traced the scratch-dir
    # leak to.
    assert len(pipeline.stages) == 9
    from ldm_core.pipelines.validation import ValidationStage as SharedValidationStage

    assert isinstance(pipeline.stages[0], SharedValidationStage)
    assert isinstance(pipeline.stages[1], ImportValidationStage)


def test_package_verification_runs_before_anything_is_written():
    """LDM-#1621: the manifest controls are cheapest if nothing exists yet.

    ProjectSetupStage creates the project directory and writes its meta, so a
    refusal after that point has something to undo. LDM-#1630 made
    `Pipeline.run` roll back on the `SystemExit` that `UI.die` raises, so such
    a refusal is now cleaned up rather than leaked -- but refusing before
    anything is written is still strictly better than refusing and unwinding,
    and this pins that ordering. The behavioural half is asserted in
    test_local_package_verification.py.
    """
    from ldm_core.pipelines.import_pipeline import (
        ExtractionStage,
        PackageVerificationStage,
        ProjectSetupStage,
    )

    names = [type(s).__name__ for s in ImportPipeline().stages]
    assert names.index(ExtractionStage.__name__) < names.index(
        PackageVerificationStage.__name__
    )
    assert names.index(PackageVerificationStage.__name__) < names.index(
        ProjectSetupStage.__name__
    )


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
def test_project_setup_stage_rollback_removes_the_project_it_created(mock_ui, tmp_path):
    """LDM-#1635: the project directory is undone by the stage that creates it.

    `ProjectSetupStage` is where `project_path` and `is_brand_new` are set, so
    it is where the undo belongs. This assertion previously named
    `BackupStateStage`, whose `execute` did nothing at all.
    """
    stage = ProjectSetupStage()
    manager = MagicMock()
    context = ImportPipelineContext(manager=manager)

    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    context.set("project_path", project_dir)
    context.set("is_brand_new", True)

    stage.rollback(context)

    manager.safe_rmtree.assert_called_once_with(project_dir)


@patch("ldm_core.pipelines.import_pipeline.UI")
def test_project_setup_rollback_spares_a_project_the_user_already_had(
    mock_ui, tmp_path
):
    """`is_brand_new` false means this run did not create it -- leave it alone."""
    stage = ProjectSetupStage()
    manager = MagicMock()
    context = ImportPipelineContext(manager=manager)

    project_dir = tmp_path / "preexisting"
    project_dir.mkdir()
    context.set("project_path", project_dir)
    context.set("is_brand_new", False)

    stage.rollback(context)

    manager.safe_rmtree.assert_not_called()


@patch("ldm_core.pipelines.import_pipeline.UI")
def test_project_setup_rollback_honours_the_commit_point(mock_ui, tmp_path):
    """LDM-#1630's guard has to survive the move, or a finished import is lost.

    `FinalizationStage` calls the whole run pipeline, prompts included. Once
    `import_committed` is set, nothing below is allowed to undo the import.
    """
    stage = ProjectSetupStage()
    manager = MagicMock()
    context = ImportPipelineContext(manager=manager)

    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    context.set("project_path", project_dir)
    context.set("is_brand_new", True)
    context.set("import_committed", True)

    stage.rollback(context)

    manager.safe_rmtree.assert_not_called()


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
