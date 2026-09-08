"""A refusal inside a pipeline stage must roll back, and keep its exit code.

LDM-#1630. `Pipeline.run` documented rollback-on-failure and implemented it
with `except Exception`. `UI.die` is `UI.error(...)` followed by `sys.exit()`,
which raises `SystemExit` -- a `BaseException`, not an `Exception` -- so no
`UI.die` in any stage of any pipeline ever triggered a rollback, and the
process exited carrying whatever partial state the earlier stages had created.
`KeyboardInterrupt` is a `BaseException` too, so Ctrl-C leaked identically.

The observable outcome asserted here is threefold, and all three matter:

* the rollback runs, so the partial state is gone
* the original exit code reaches the caller unchanged -- LDM's contract is 0
  success, 1 validation, 2 auth, 3 infrastructure/data, 4 orchestration, 5
  idempotent no-op, 126 invocation, 130 interrupt, and `scripts/
  verify_e2e_refactor.{sh,ps1}` asserts on it. A fix that rolled back but
  renumbered the status would be worse than the leak
* a completed import is NOT rolled back when its post-import `ldm run` aborts

The import tests drive the real `ImportPipeline` over a real package file.
Only the boundaries that would reach Docker, Java or the network are stubbed;
`read_meta`/`write_meta` are the real implementations against a temp
directory, so the project metadata asserted on is genuinely written.
"""

import json
import os
import shutil
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ldm_core.pipelines.base import Pipeline, PipelineContext, PipelineStage

# --- The shared machinery ---------------------------------------------------


class _RecordingStage(PipelineStage):
    """Succeeds, and records whether it was asked to roll back."""

    def __init__(self, name: str = "Recording"):
        self._name = name
        self.executed = False
        self.rolled_back = False

    @property
    def name(self) -> str:
        return self._name

    def execute(self, context: PipelineContext) -> None:
        self.executed = True

    def rollback(self, context: PipelineContext) -> None:
        self.rolled_back = True


class _DyingStage(_RecordingStage):
    """Refuses the way LDM stages actually refuse.

    Raises `SystemExit` directly rather than calling `UI.die`, so the test does
    not depend on UI plumbing -- `UI.die` is a `UI.error` call and a
    `sys.exit(exit_code)`, and it is the second half that matters here.
    """

    def __init__(self, name: str = "Dying", exit_code: object = 1):
        super().__init__(name)
        self.exit_code = exit_code

    def execute(self, context: PipelineContext) -> None:
        self.executed = True
        raise SystemExit(self.exit_code)


class _InterruptedStage(_RecordingStage):
    """Ctrl-C, which is also a BaseException and also leaked."""

    def execute(self, context: PipelineContext) -> None:
        self.executed = True
        raise KeyboardInterrupt


def test_a_dying_stage_rolls_back_the_stages_that_succeeded():
    context = PipelineContext()
    pipeline = Pipeline("die")

    first = _RecordingStage("First")
    refuser = _DyingStage("Refuser")
    later = _RecordingStage("Later")
    for stage in (first, refuser, later):
        pipeline.add_stage(stage)

    with pytest.raises(SystemExit):
        pipeline.run(context)

    assert first.rolled_back is True, "the stage that succeeded was not rolled back"
    assert refuser.rolled_back is False, "the failing stage must not be rolled back"
    assert later.executed is False


@pytest.mark.parametrize("code", [1, 2, 3, 4, 126])
def test_the_exit_code_survives_the_rollback(code):
    """Every failing code in LDM's contract must reach the caller unchanged.

    5 is absent on purpose: it is the idempotent no-op and does not roll back
    at all (LDM-#1636). Its exit code is asserted separately, below.
    """
    context = PipelineContext()
    pipeline = Pipeline(f"exit-{code}")
    first = _RecordingStage("First")
    pipeline.add_stage(first)
    pipeline.add_stage(_DyingStage("Refuser", exit_code=code))

    with pytest.raises(SystemExit) as excinfo:
        pipeline.run(context)

    assert excinfo.value.code == code, f"exit code {code} was not preserved"
    assert first.rolled_back is True


def test_a_keyboard_interrupt_rolls_back_and_still_terminates():
    """Ctrl-C must remain Ctrl-C: it propagates, and now also cleans up.

    `cli.py`'s top-level handler turns a propagated `KeyboardInterrupt` into
    exit 130, so re-raising the original is what keeps that contract. Turning
    it into a normal error return would silently make Ctrl-C exit 1.
    """
    context = PipelineContext()
    pipeline = Pipeline("interrupt")
    first = _RecordingStage("First")
    pipeline.add_stage(first)
    pipeline.add_stage(_InterruptedStage("Interrupted"))

    with pytest.raises(KeyboardInterrupt):
        pipeline.run(context)

    assert first.rolled_back is True


def test_a_rollback_that_dies_does_not_replace_the_original_exit_code():
    """A rollback is now reachable on the refusal path, so it can hijack it.

    If a rollback calls `UI.die` -- or reaches anything that does -- its
    `SystemExit` would escape `_rollback` and become the code the caller sees,
    losing the refusal that actually happened.
    """
    context = PipelineContext()
    pipeline = Pipeline("rollback-dies")

    class _DyingRollbackStage(_RecordingStage):
        def rollback(self, context: PipelineContext) -> None:
            super().rollback(context)
            raise SystemExit(99)

    outer = _RecordingStage("Outer")
    pipeline.add_stage(outer)
    pipeline.add_stage(_DyingRollbackStage("Inner"))
    pipeline.add_stage(_DyingStage("Refuser", exit_code=3))

    with pytest.raises(SystemExit) as excinfo:
        pipeline.run(context)

    assert excinfo.value.code == 3, "a dying rollback replaced the original exit code"
    assert outer.rolled_back is True, (
        "a dying rollback stopped the remaining stages from rolling back"
    )


def test_a_die_is_recorded_on_the_context():
    context = PipelineContext()
    pipeline = Pipeline("recorded")
    pipeline.add_stage(_DyingStage("Refuser", exit_code=4))

    with pytest.raises(SystemExit):
        pipeline.run(context)

    assert len(context.errors) == 1
    assert isinstance(context.errors[0], SystemExit)


def test_an_ordinary_exception_still_returns_false_rather_than_raising():
    """The pre-existing contract for `Exception` is unchanged."""
    context = PipelineContext()
    pipeline = Pipeline("ordinary")

    class _RaisingStage(_RecordingStage):
        def execute(self, context: PipelineContext) -> None:
            self.executed = True
            raise ValueError("boom")

    first = _RecordingStage("First")
    pipeline.add_stage(first)
    pipeline.add_stage(_RaisingStage("Raiser"))

    assert pipeline.run(context) is False
    assert first.rolled_back is True
    assert isinstance(context.errors[0], ValueError)


# --- The import pipeline, end to end ---------------------------------------


class _StubImportManager:
    """The manager surface the import pipeline touches, and nothing else."""

    def __init__(self, project_path):
        self.project_path = Path(project_path)
        self.non_interactive = True
        self.args = MagicMock()
        # MagicMock attributes are truthy and several of these are read with
        # `or` fallbacks. Pin them to the real "absent" value.
        self.args.project = None
        self.args.project_flag = None
        self.args.host_name = None
        self.args.ssl = None
        self.args.port = None
        self.args.build = False
        self.args.no_run = False
        # The checksum path has its own coverage in test_import_pipeline.py.
        self.args.verify = False
        self.snapshot = MagicMock()
        self.runtime = MagicMock()
        self.workspace = MagicMock()
        self.verify_runtime_environment = MagicMock()
        self.check_uncommitted_changes = MagicMock()
        self.removed: list[Path] = []

    def _check_java_version(self, _expected):
        return True

    def detect_project_path(self, _project_name, for_init=False):
        return self.project_path

    def setup_paths(self, root):
        root = Path(root)
        return {
            "root": root,
            "cx": root / "osgi" / "client-extensions",
            "configs": root / "configs",
            "deploy": root / "deploy",
            "files": root / "files",
            "scripts": root / "scripts",
            "modules": root / "modules",
            "ce_dir": root / "client-extensions",
        }

    def read_meta(self, path, strict=False):
        """Mirrors LiferayDockerManager.read_meta's directory handling."""
        from ldm_core.utils import MetaReadError, read_meta

        p = Path(path)
        if p.is_dir():
            manifest = p / "meta"
            if not manifest.exists():
                if strict:
                    raise MetaReadError(f"No metadata file found in {p}")
                return {}
            return read_meta(manifest, strict=strict)
        return read_meta(p, strict=strict)

    def write_meta(self, path, meta):
        from ldm_core.utils import resolve_meta_file_path, write_meta

        write_meta(resolve_meta_file_path(path), meta)

    def safe_rmtree(self, path):
        """Records the deletion request, and performs it.

        The real `utils.safe_rmtree` runs `verify_safe_to_delete` first, which
        is tested where it lives. What matters here is *whether* a rollback
        asks for the deletion at all.
        """
        self.removed.append(Path(path))
        shutil.rmtree(path, ignore_errors=True)


_MANIFEST = {
    "github_repository": "acme/widget",
    "tag": "7.4.13-u108",
    "db_type": "mysql",
    "includes_database": "true",
}


def _build_ldmp(root, manifest):
    """A package file as `ldm package` writes one: meta + files.tar.gz, tarred."""
    inner = root / "inner"
    inner.mkdir(parents=True, exist_ok=True)
    (inner / "meta").write_text(json.dumps(manifest), encoding="utf-8")

    payload = root / "staging" / "data" / "dump.sql"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"-- dump")
    with tarfile.open(inner / "files.tar.gz", "w:gz") as tar:
        tar.add(payload, arcname="data/dump.sql")

    package = root / "widget.ldmp"
    with tarfile.open(package, "w:gz") as tar:
        for item in sorted(inner.iterdir()):
            tar.add(item, arcname=item.name)
    return package


def _run_import(root, manifest, sabotage=None):
    """Run the real ImportPipeline with `root` as the working directory.

    Returns (manager, raised) where `raised` is the BaseException that escaped
    the pipeline, or None.
    """
    from ldm_core.pipelines.import_pipeline import (
        ImportPipeline,
        ImportPipelineContext,
    )
    from ldm_core.pipelines.validation import ValidationStage as SharedValidationStage

    root = Path(root)
    package = _build_ldmp(root, manifest)
    manager = _StubImportManager(root / "projects" / "widget")

    cwd = Path.cwd()
    os.chdir(root)
    try:
        with (
            # LDM_HOME so nothing can reach the developer's real ~/.ldm.
            # LDM_DRY_RUN cleared because write_meta diverts to an in-memory
            # VFS when it is set, which would make the project-directory
            # assertions below silently vacuous.
            patch.dict(
                os.environ,
                {"LDM_HOME": str(root / "ldm-home"), "LDM_DRY_RUN": ""},
            ),
            # The shared preflight runs the doctor's tooling checks, which
            # reach real binaries and Docker.
            patch.object(SharedValidationStage, "execute", lambda *_a, **_k: None),
            patch("ldm_core.ui.UI._print", lambda *_a, **_k: None),
        ):
            context = ImportPipelineContext(
                manager=manager,
                source_path=str(package),
                project_name=None,
                no_run=False,
            )
            pipeline = ImportPipeline()
            if sabotage:
                sabotage(manager)
            try:
                pipeline.run(context)
            except BaseException as exc:
                return manager, exc
    finally:
        os.chdir(cwd)
    return manager, None


def _scratch_left_behind(root):
    scratch = Path(root) / ".ldm_temp"
    if not scratch.exists():
        return []
    return sorted(p.name for p in scratch.iterdir())


def test_a_db_type_refusal_removes_its_extraction_directory(tmp_path):
    """The leak reported in LDM-#1630, as an observable outcome.

    `ProjectSetupStage` refuses an unknown engine with `UI.die`, which escaped
    `Pipeline.run` uncaught -- leaving `.ldm_temp/import_<timestamp>/`, and the
    whole extracted package inside it, in the user's working directory.
    """
    _manager, raised = _run_import(tmp_path, dict(_MANIFEST, db_type="oracle"))

    assert isinstance(raised, SystemExit), f"the unknown engine was accepted: {raised}"
    assert _scratch_left_behind(tmp_path) == [], (
        "the refusal left its extraction directory behind"
    )


def test_a_db_type_refusal_still_exits_1(tmp_path):
    """Cleaning up must not renumber the status. 1 is validation."""
    _manager, raised = _run_import(tmp_path, dict(_MANIFEST, db_type="oracle"))

    assert isinstance(raised, SystemExit)
    assert raised.code == 1


def test_a_ctrl_c_mid_pipeline_removes_its_extraction_directory(tmp_path):
    """Ctrl-C is a BaseException too, and leaked the same directory."""
    from ldm_core.pipelines.import_pipeline import ProjectSetupStage

    with patch.object(ProjectSetupStage, "execute", side_effect=KeyboardInterrupt):
        _manager, raised = _run_import(tmp_path, dict(_MANIFEST))

    assert isinstance(raised, KeyboardInterrupt), (
        f"Ctrl-C did not terminate the pipeline: {raised!r}"
    )
    assert _scratch_left_behind(tmp_path) == [], (
        "the interrupt left its extraction directory behind"
    )


def test_a_completed_import_survives_an_aborted_post_import_run(tmp_path):
    """FinalizationStage runs `ldm run`, and that must not be able to undo it.

    `ProjectSetupStage.rollback` deletes a brand-new project directory
    (LDM-#1635 moved it there from the dissolved BackupStateStage), and
    `FinalizationStage` calls `manager.runtime.cmd_run` -- the entire run
    pipeline, prompts included. Rollback now fires on `SystemExit`, so without
    a commit point a refusal or a Ctrl-C inside that post-import run would
    delete the project the import had already finished creating.
    """

    def refuse_in_cmd_run(manager):
        manager.runtime.cmd_run.side_effect = SystemExit(4)

    manager, raised = _run_import(tmp_path, dict(_MANIFEST), sabotage=refuse_in_cmd_run)

    assert isinstance(raised, SystemExit)
    assert raised.code == 4, "the post-import run's exit code was not preserved"
    assert manager.project_path.exists(), (
        "an aborted post-import run deleted the imported project"
    )
    assert manager.project_path not in manager.removed, (
        "rollback asked to delete a project the import had already completed"
    )


def test_an_interrupted_post_import_run_also_leaves_the_project(tmp_path):
    """Same commit point, reached by Ctrl-C rather than a refusal."""

    def interrupt_cmd_run(manager):
        manager.runtime.cmd_run.side_effect = KeyboardInterrupt

    manager, raised = _run_import(tmp_path, dict(_MANIFEST), sabotage=interrupt_cmd_run)

    assert isinstance(raised, KeyboardInterrupt)
    assert manager.project_path.exists(), (
        "Ctrl-C during the post-import run deleted the imported project"
    )


# --- The run pipeline's rollback is destructive; keep it that way -----------


def test_run_rollback_never_deletes_a_directory_it_did_not_create(tmp_path):
    """LDM-#1630 blast radius: `ldm run` adopts existing directories.

    `ProjectInitializationStage.rollback` removes `root` when the project is
    new and initialization did not complete. "New" means only that the
    directory holds no LDM `meta` -- a pre-existing folder of the user's own
    files qualifies. Refusals in `RuntimeValidationStage` and
    `ConfigResolutionStage` (`--samples requires a custom hostname`, an unknown
    `--archetype`, a failed tag lookup) all sit after that stage, so making
    them roll back is what turns a leak into data loss.

    `utils.verify_safe_to_delete` already refuses a git repository and the
    working directory; this covers the folder that is neither.
    """
    from ldm_core.pipelines.run import ProjectInitializationStage, RunPipelineContext

    adopted = tmp_path / "existing-work"
    adopted.mkdir()
    (adopted / "notes.txt").write_text("the user's own file", encoding="utf-8")

    manager = MagicMock()
    manager.safe_rmtree.side_effect = lambda p: shutil.rmtree(p, ignore_errors=True)
    context = RunPipelineContext(manager=manager)
    context.set("root", adopted)
    context.set("root_existed", True)
    context.set("is_new_project", True)
    context.set("init_success", False)
    context.set("project_id", "existing-work")

    ProjectInitializationStage().rollback(context)

    assert adopted.exists(), "rollback deleted a directory LDM did not create"
    assert (adopted / "notes.txt").exists()
    manager.safe_rmtree.assert_not_called()


def test_run_rollback_still_removes_a_project_it_did_create(tmp_path):
    """The guard above must not disable the cleanup it is narrowing."""
    from ldm_core.pipelines.run import ProjectInitializationStage, RunPipelineContext

    created = tmp_path / "half-made"
    created.mkdir()

    manager = MagicMock()
    manager.safe_rmtree.side_effect = lambda p: shutil.rmtree(p, ignore_errors=True)
    context = RunPipelineContext(manager=manager)
    context.set("root", created)
    context.set("root_existed", False)
    context.set("is_new_project", True)
    context.set("init_success", False)
    context.set("project_id", "half-made")

    ProjectInitializationStage().rollback(context)

    manager.safe_rmtree.assert_called_once_with(created)
    assert not created.exists()
    manager.unregister_project.assert_called_once_with("half-made")


# --- The idempotent no-op, which is not a failure (LDM-#1636) --------------


def test_an_idempotent_no_op_does_not_roll_anything_back():
    """Exit 5 means "already in the requested state", so there is nothing to undo.

    LDM-#1636. LDM-#1630 made `Pipeline.run` roll back on `SystemExit`, which
    is right for a refusal and wrong for this one code: per LDM-#1094, exit 5
    is `ldm run` finding the project already up. Rolling back would undo work
    that legitimately exists -- and the stage that would run it,
    `ProjectInitializationStage`, sits two stages before the refusal.
    """
    context = PipelineContext()
    pipeline = Pipeline("no-op")

    first = _RecordingStage("First")
    second = _RecordingStage("Second")
    for stage in (first, second, _DyingStage("NoOp", exit_code=5)):
        pipeline.add_stage(stage)

    with pytest.raises(SystemExit) as excinfo:
        pipeline.run(context)

    assert excinfo.value.code == 5, "the no-op exit code was not preserved"
    assert first.rolled_back is False, "an idempotent no-op rolled back a stage"
    assert second.rolled_back is False, "an idempotent no-op rolled back a stage"


def test_an_idempotent_no_op_is_not_recorded_as_an_error():
    """`context.errors` is where a caller looks to find out what went wrong.

    Nothing went wrong, so nothing belongs there.
    """
    context = PipelineContext()
    pipeline = Pipeline("no-op-errors")
    pipeline.add_stage(_RecordingStage("First"))
    pipeline.add_stage(_DyingStage("NoOp", exit_code=5))

    with pytest.raises(SystemExit):
        pipeline.run(context)

    assert context.errors == []


def test_the_no_op_exemption_is_keyed_on_the_code_not_on_being_a_systemexit():
    """Guards against the fix being written too broadly.

    Every other `SystemExit` must still roll back. A fix that skipped rollback
    for `SystemExit` generally would pass the two tests above and silently
    reinstate the whole of LDM-#1630.
    """
    # 0 and a bare `sys.exit()` are deliberately absent: no stage raises
    # either, and pinning what they should do here would decide a question
    # LDM-#1636 did not ask.
    for code in (1, 2, 3, 4, 6, 50, 126):
        context = PipelineContext()
        pipeline = Pipeline(f"not-a-no-op-{code}")
        first = _RecordingStage("First")
        pipeline.add_stage(first)
        pipeline.add_stage(_DyingStage("Refuser", exit_code=code))

        with pytest.raises(SystemExit):
            pipeline.run(context)

        assert first.rolled_back is True, (
            f"exit {code!r} was wrongly treated as an idempotent no-op"
        )


def test_the_real_already_running_refusal_rolls_nothing_back():
    """The mechanism at its real site, not a stand-in for it.

    Drives the actual `RuntimeValidationStage` through an actual `Pipeline`
    with the actual `UI.die`, so what is asserted is the refusal LDM-#1094
    added rather than a `SystemExit(5)` this test invented. Only
    `DockerService.is_running` is stubbed -- that is the boundary that would
    otherwise need a live container.
    """
    from ldm_core.pipelines.run import RunPipelineContext, RuntimeValidationStage

    manager = MagicMock()
    manager.non_interactive = True
    manager.args.force = False
    manager.args.no_up = False

    context = RunPipelineContext(manager)
    context.set("project_id", "already-up")
    context.set("is_new_project", False)
    context.set("is_restart", False)
    context.set("no_up", False)
    context.set("project_meta", {"container_name": "already-up"})

    initialization = _RecordingStage("ProjectInitializationStand-In")
    pipeline = Pipeline("run")
    pipeline.add_stage(initialization)
    pipeline.add_stage(RuntimeValidationStage())

    with patch("ldm_core.docker_service.DockerService.is_running", return_value=True):
        with pytest.raises(SystemExit) as excinfo:
            pipeline.run(context)

    assert excinfo.value.code == 5, (
        "the already-running refusal no longer carries LDM-#1094's exit 5"
    )
    assert initialization.rolled_back is False, (
        "the already-running no-op rolled back the initialization stage"
    )
