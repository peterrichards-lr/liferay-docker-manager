"""Importing a Liferay Cloud workspace must restore its code and services (LDM-#1681).

PR #497 (`ba24c012`) disabled cloud import in three linked ways, all silent:

* `is_cloud` was read from `manager.workspace._is_lcp_workspace`, a method
  defined at no commit, behind a `hasattr` guard -- so it was permanently
  `False` and the standalone-service copy never ran;
* the refactor stopped descending into `<repo>/liferay`, so every path that
  syncs code pointed at the repository root, where an LCP workspace keeps
  none of it;
* consequently the service walk, written as `workspace_root.parent`, now
  resolved one level ABOVE the repository root -- the dormant block was not
  merely switched off, it was also wrong, and repointing detection at the
  real function without fixing it would have found nothing.

`--cloud-project` was the fourth: declared on four commands, read by none,
with `handlers/cloud.py` silently falling back to the project directory name.

Every test here fails against the pre-fix code. The layout assertions matter
more than they look: they are the only thing standing between "detection is
fixed" and "the import actually copies something".
"""

import json
import shutil

import pytest

from ldm_core.pipelines.base import PipelineContext
from ldm_core.pipelines.import_pipeline import ExtractionStage, VolumeSyncStage


class _Workspace:
    """Records what `_hydrate_from_workspace` was handed."""

    def __init__(self):
        self.hydrated = []

    def _hydrate_from_workspace(self, workspace_root, paths, overwrite=True):
        self.hydrated.append(workspace_root)


class _Config:
    """LDM-#1692: the sync now merges `configs/<env>/portal-ext.properties`
    through the config service, so the stub manager needs that surface."""

    @staticmethod
    def _get_properties(content):
        props = {}
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "!")) and "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
        return props

    @staticmethod
    def update_portal_ext(paths, updates):
        target = paths["files"] / "portal-ext.properties"
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text() if target.exists() else ""
        with target.open("a") as fh:
            for k, v in updates.items():
                if f"{k}=" not in existing:
                    fh.write(f"{k}={v}\n")


class _Manager:
    def __init__(self, non_interactive=True, cloud_project=None):
        self.workspace = _Workspace()
        self.config = _Config()
        self.non_interactive = non_interactive
        self.args = type(
            "Args", (), {"cloud_project": cloud_project, "target_env": "local"}
        )()

    @staticmethod
    def safe_rmtree(path):
        shutil.rmtree(path, ignore_errors=True)


class _Context(PipelineContext):
    """Minimal stand-in for ImportPipelineContext.

    Subclasses the real context rather than duck-typing it so the stages are
    called through their declared signature -- `ImportPipelineContext` itself
    needs a whole `LiferayManager`, which none of these assertions touch.
    """

    def __init__(self, manager=None, **data):
        super().__init__(**data)
        self.manager = manager or _Manager()


def _make_lcp_repo(root, project_id="lctintranet", services=("webcrawler",)):
    """A Liferay Cloud repository: the workspace under `liferay/`, services beside it."""
    workspace = root / "liferay"
    (workspace / "configs" / "local").mkdir(parents=True)
    (workspace / "configs" / "local" / "portal-ext.properties").write_text("a=1\n")
    (workspace / "LCP.json").write_text(json.dumps({"id": "liferay"}))
    (workspace / "gradle.properties").write_text(
        "liferay.workspace.product=dxp-2026.q1.7\n"
    )
    if project_id is not None:
        (root / "LCP.json").write_text(json.dumps({"id": project_id}))

    # Infrastructure directories the scan must skip even though several of
    # them carry an LCP.json of their own.
    for infra in ("backup", "ci", "database", "search", "webserver"):
        (root / infra).mkdir()
        (root / infra / "LCP.json").write_text(json.dumps({"id": infra}))
        (root / infra / "Dockerfile").write_text("FROM alpine\n")

    for name in services:
        svc = root / name
        svc.mkdir()
        (svc / "LCP.json").write_text(json.dumps({"id": name}))
        (svc / "Dockerfile").write_text("FROM alpine\n")
    return workspace


def _make_plain_workspace(root):
    """A standard Liferay Workspace: its own root, no `liferay/`, no LCP.json."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "gradle.properties").write_text("liferay.workspace.product=dxp-2026.q1.7\n")
    (root / "configs" / "local").mkdir(parents=True)
    return root


def _project_paths(project):
    paths = {
        "root": project,
        "deploy": project / "deploy",
        "files": project / "files",
        "scripts": project / "scripts",
        "configs": project / "osgi" / "configs",
        "modules": project / "osgi" / "modules",
        "cx": project / "osgi" / "client-extensions",
        "ce_dir": project / "client-extensions",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


# --------------------------------------------------------------------------
# Layout resolution
# --------------------------------------------------------------------------


def test_cloud_repository_resolves_to_the_nested_workspace(tmp_path):
    repo = tmp_path / "my-cloud-repo"
    workspace = _make_lcp_repo(repo)

    context = _Context()
    ExtractionStage._resolve_layout(context, repo)

    assert context.get("is_cloud") is True
    assert context.get("workspace_root") == workspace
    assert context.get("cloud_root") == repo


def test_workspace_named_directly_still_finds_the_repository_root(tmp_path):
    """`ldm import <repo>/liferay` must not lose sight of the sibling services."""
    repo = tmp_path / "my-cloud-repo"
    workspace = _make_lcp_repo(repo)

    context = _Context()
    ExtractionStage._resolve_layout(context, workspace)

    assert context.get("is_cloud") is True
    assert context.get("workspace_root") == workspace
    assert context.get("cloud_root") == repo


def test_plain_workspace_is_not_cloud_and_has_no_cloud_root(tmp_path):
    workspace = _make_plain_workspace(tmp_path / "my-workspace")

    context = _Context()
    ExtractionStage._resolve_layout(context, workspace)

    assert context.get("is_cloud") is False
    assert context.get("workspace_root") == workspace
    assert context.get("cloud_root") is None


# --------------------------------------------------------------------------
# The sync itself
# --------------------------------------------------------------------------


def _run_sync(tmp_path, monkeypatch, source_root):
    # `_snapshot_targets` writes under Path.cwd(); keep it out of the repo.
    monkeypatch.chdir(tmp_path)

    project = tmp_path / "project"
    paths = _project_paths(project)
    context = _Context(paths=paths, extracted_source=source_root, overwrite=True)
    ExtractionStage._resolve_layout(context, source_root)
    context.set("is_brand_new", True)
    VolumeSyncStage().execute(context)
    return context, paths


def test_standalone_services_are_copied_into_the_project(tmp_path, monkeypatch):
    repo = tmp_path / "my-cloud-repo"
    _make_lcp_repo(repo, services=("webcrawler", "custom-api"))

    _, paths = _run_sync(tmp_path, monkeypatch, repo)

    services = paths["root"] / "services"
    assert services.is_dir(), "the LCP service directories were never copied"
    assert sorted(p.name for p in services.iterdir()) == ["custom-api", "webcrawler"]
    assert (services / "webcrawler" / "Dockerfile").exists()


def test_the_scan_then_records_what_was_copied(tmp_path, monkeypatch):
    """`scan_standalone_services` reads the directory the sync writes."""
    from ldm_core.workspace.metadata import scan_standalone_services

    repo = tmp_path / "my-cloud-repo"
    _make_lcp_repo(repo, services=("webcrawler",))

    _, paths = _run_sync(tmp_path, monkeypatch, repo)

    from unittest.mock import MagicMock

    found = scan_standalone_services(MagicMock(), paths["root"])
    assert [s["name"] for s in found] == ["webcrawler"]
    assert found[0]["is_standalone"] is True


def test_infrastructure_directories_are_not_imported_as_services(tmp_path, monkeypatch):
    repo = tmp_path / "my-cloud-repo"
    _make_lcp_repo(repo, services=("webcrawler",))

    _, paths = _run_sync(tmp_path, monkeypatch, repo)

    copied = {p.name for p in (paths["root"] / "services").iterdir()}
    assert copied == {"webcrawler"}


def test_the_walk_does_not_escape_above_the_repository(tmp_path, monkeypatch):
    """The trap: the pre-fix walk resolved one level ABOVE the repository root.

    A directory that merely sits next to the repository is not part of it, so
    a service-shaped sibling must never be imported. Repointing detection
    without fixing the walk would have picked this up instead of the real
    services.
    """
    repo = tmp_path / "my-cloud-repo"
    _make_lcp_repo(repo, services=("webcrawler",))

    stranger = tmp_path / "not-my-repo"
    stranger.mkdir()
    (stranger / "LCP.json").write_text(json.dumps({"id": "not-my-repo"}))
    (stranger / "Dockerfile").write_text("FROM alpine\n")

    _, paths = _run_sync(tmp_path, monkeypatch, repo)

    copied = {p.name for p in (paths["root"] / "services").iterdir()}
    assert "not-my-repo" not in copied
    assert copied == {"webcrawler"}


def test_code_is_synced_from_the_nested_workspace_not_the_repository_root(
    tmp_path, monkeypatch
):
    repo = tmp_path / "my-cloud-repo"
    workspace = _make_lcp_repo(repo)

    context, paths = _run_sync(tmp_path, monkeypatch, repo)

    assert context.manager.workspace.hydrated == [workspace]
    # LDM-#1692: this used to assert `osgi/configs/local/portal-ext.properties`
    # -- the defective layout, where Liferay never reads it. That assertion
    # pinned the bug in place and broke the moment it was fixed. The workspace's
    # properties now merge into the project's own file, which proves the
    # `<repo>/liferay` descent just as well and does not depend on a defect.
    assert "a=1" in (paths["files"] / "portal-ext.properties").read_text()


def test_the_real_stages_copy_services_end_to_end(tmp_path, monkeypatch):
    """Drives `ExtractionStage.execute` rather than the layout helper.

    Deliberately not phrased against `_resolve_layout`: a test that only
    exercises the new helper would fail on the pre-fix code with an
    `AttributeError`, which proves the helper is new rather than that the
    import was broken. This one is written the way the pipeline runs, so
    against `ba24c012` it fails on the assertion -- no `services` directory.
    """
    monkeypatch.chdir(tmp_path)

    repo = tmp_path / "my-cloud-repo"
    _make_lcp_repo(repo, services=("webcrawler",))

    project = tmp_path / "project"
    paths = _project_paths(project)
    context = _Context(paths=paths, source_resolved=repo, overwrite=True)
    context.set("is_brand_new", True)

    ExtractionStage().execute(context)
    VolumeSyncStage().execute(context)

    assert (paths["root"] / "services" / "webcrawler" / "LCP.json").exists()


def test_a_plain_workspace_imports_no_services(tmp_path, monkeypatch):
    workspace = _make_plain_workspace(tmp_path / "my-workspace")

    _, paths = _run_sync(tmp_path, monkeypatch, workspace)

    assert not (paths["root"] / "services").exists()


# --------------------------------------------------------------------------
# --cloud-project
# --------------------------------------------------------------------------


def _cloud_context(tmp_path, project_id="lctintranet", **manager_kwargs):
    repo = tmp_path / "my-cloud-repo"
    _make_lcp_repo(repo, project_id=project_id)
    context = _Context(manager=_Manager(**manager_kwargs), extracted_source=repo)
    ExtractionStage._resolve_layout(context, repo)
    return context


def test_cloud_project_flag_is_recorded_in_the_project_meta(tmp_path):
    from ldm_core.pipelines.import_pipeline import ProjectSetupStage

    context = _cloud_context(tmp_path, cloud_project="lctexplicit")
    meta: dict = {}

    ProjectSetupStage._resolve_cloud_project_id(context, meta)

    assert meta["cloud_project_id"] == "lctexplicit"


def test_the_flag_beats_the_root_lcp_json(tmp_path):
    from ldm_core.pipelines.import_pipeline import ProjectSetupStage

    context = _cloud_context(
        tmp_path, project_id="lctdetected", cloud_project="lctexplicit"
    )
    meta: dict = {}

    ProjectSetupStage._resolve_cloud_project_id(context, meta)

    assert meta["cloud_project_id"] == "lctexplicit"


def test_the_root_lcp_json_id_is_used_when_no_flag_is_given(tmp_path):
    from ldm_core.pipelines.import_pipeline import ProjectSetupStage

    context = _cloud_context(tmp_path, project_id="lctdetected")
    meta: dict = {}

    ProjectSetupStage._resolve_cloud_project_id(context, meta)

    assert meta["cloud_project_id"] == "lctdetected"


def test_an_undeterminable_id_refuses_rather_than_guessing(tmp_path):
    """Exit 2, as before PR #497. The silent fallback ran `lcp` against a guess."""
    from ldm_core.pipelines.import_pipeline import ProjectSetupStage

    context = _cloud_context(tmp_path, project_id=None)
    meta: dict = {}

    with pytest.raises(SystemExit) as excinfo:
        ProjectSetupStage._resolve_cloud_project_id(context, meta)

    assert excinfo.value.code == 2
    assert "cloud_project_id" not in meta


def test_an_existing_project_id_is_neither_re_prompted_nor_refused(tmp_path):
    from ldm_core.pipelines.import_pipeline import ProjectSetupStage

    context = _cloud_context(tmp_path, project_id=None)
    meta = {"cloud_project_id": "lctalready"}

    ProjectSetupStage._resolve_cloud_project_id(context, meta)

    assert meta["cloud_project_id"] == "lctalready"


def test_a_non_cloud_source_records_nothing(tmp_path):
    from ldm_core.pipelines.import_pipeline import ProjectSetupStage

    workspace = _make_plain_workspace(tmp_path / "my-workspace")
    context = _Context(
        manager=_Manager(cloud_project="lctexplicit"), extracted_source=workspace
    )
    ExtractionStage._resolve_layout(context, workspace)
    meta: dict = {}

    ProjectSetupStage._resolve_cloud_project_id(context, meta)

    assert meta == {}
