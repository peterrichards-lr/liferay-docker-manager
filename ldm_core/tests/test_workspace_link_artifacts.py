"""What `ldm link` / `ldm import` of a workspace puts in the project.

Found by the first consumer of stateless packages, ldm-cx-samples, whose
package carried files no importer could use:

* fragment collections from the workspace's `fragments/` sat flat in the
  project's `client-extensions/` as well as in `deploy/`, where nothing deploys
  them;
* a `client-extensions/site-initializer.zip` -- the intermediate Gradle output
  every site initializer leaves under `build/`, all with the same name, ~58 MB
  in that package;
* the workspace's `scripts/` was copied in silently, and Liferay's entrypoint
  executes it (LDM-#2533);
* `--db` was not recorded without a boot, so `ldm package --stateless` wrote
  `"db_type": null`;
* `--build` ran after the sync, so a clean checkout linked nothing it built.

The sync is driven through the real `VolumeSyncStage` and the real
`_hydrate_from_workspace`, on a workspace laid out as a Gradle build leaves it.
"""

import json
import shutil
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ldm_core.pipelines.base import PipelineContext
from ldm_core.pipelines.import_pipeline import (
    BuildWorkspaceStage,
    ImportPipeline,
    ImportPipelineContext,
    ProjectSetupStage,
    VolumeSyncStage,
)
from ldm_core.workspace import hydration


def _zip(path, members):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)


def _make_workspace(root):
    """A built workspace, shaped as ldm-cx-samples' is after `gradlew build`."""
    root.mkdir(parents=True)
    # Two fragment collections, descriptor at the zip root as Liferay's own.
    for name in ("supplemental-fragments", "responsive-menus"):
        _zip(
            root / "fragments" / f"{name}.zip",
            {
                "liferay-deploy-fragments.json": '{"fragments": []}',
                f"{name}/collection.json": "{}",
            },
        )
    # A static client extension: the real build puts its zip in dist/.
    _zip(
        root / "client-extensions" / "eco-theme" / "dist" / "eco-theme.zip",
        {
            "client-extension.yaml": "eco-theme:\n  type: themeCSS\n",
            "LCP.json": '{"id": "ecotheme", "kind": "Deployment"}',
            "static/css/main.css": "body{}",
        },
    )
    # Two site initializers. Each build also leaves the SAME-NAMED
    # intermediate under build/, which is not a client extension at all.
    for name in ("eco-site-initializer", "veridian-site-initializer"):
        cx = root / "client-extensions" / name
        _zip(
            cx / "dist" / f"{name}.zip",
            {
                "client-extension.yaml": f"{name}:\n  type: siteInitializer\n",
                "LCP.json": json.dumps({"id": name.replace("-", ""), "kind": "Job"}),
                # The header the extender keys on, as Liferay's build writes it.
                "WEB-INF/liferay-plugin-package.properties": (
                    f"Liferay-Client-Extension-Site-Initializer={name}\n"
                ),
            },
        )
        _zip(
            cx
            / "build"
            / "liferay-client-extension-build"
            / "site-initializer"
            / "site-initializer.zip",
            {"site-initializer/layouts/home.json": name},
        )
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


class _RealHydration:
    """The workspace service's `_hydrate_from_workspace`, unmodified."""

    def _hydrate_from_workspace(self, workspace_root, paths, overwrite=True):
        return hydration._hydrate_from_workspace(self, workspace_root, paths, overwrite)


class _Manager:
    def __init__(self):
        self.run_command = MagicMock(return_value="")
        self._check_gradle_java_version = MagicMock(return_value=True)
        self.workspace = _RealHydration()
        self.config = MagicMock()
        self.non_interactive = True
        self.args = type("Args", (), {"target_env": "local"})()


class _Context(PipelineContext):
    def __init__(self, manager, **data):
        super().__init__(**data)
        self.manager = manager


def _link(tmp_path, monkeypatch, workspace):
    monkeypatch.chdir(tmp_path)
    paths = _project_paths(tmp_path / "project")
    context = _Context(
        _Manager(),
        paths=paths,
        extracted_source=workspace,
        workspace_root=workspace,
        overwrite=True,
    )
    context.set("is_brand_new", True)
    VolumeSyncStage().execute(context)
    return paths


@pytest.fixture
def linked(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path / "ws")
    return _link(tmp_path, monkeypatch, workspace)


# --- fragments and build intermediates (consumer's B and C) ----------------


def test_fragment_collections_are_staged_for_deploy(linked):
    assert sorted(p.name for p in linked["deploy"].glob("*.zip")) == [
        "responsive-menus.zip",
        "supplemental-fragments.zip",
    ]


def test_no_zip_is_left_flat_in_client_extensions(linked):
    """Neither a fragment collection nor a Gradle intermediate: nothing
    deploys a zip from there that carries no client-extension descriptor."""
    assert sorted(p.name for p in linked["ce_dir"].glob("*.zip")) == []


def test_no_build_intermediate_reaches_the_project(linked):
    every_name = {p.name for p in linked["root"].rglob("*.zip")}
    assert "site-initializer.zip" not in every_name


def test_real_client_extensions_still_reach_liferay(linked):
    assert [p.name for p in linked["cx"].glob("*.zip")] == ["eco-theme.zip"]
    deferred = linked["root"] / ".ldm" / "deferred-client-extensions"
    assert sorted(p.name for p in deferred.glob("*.zip")) == [
        "eco-site-initializer.zip",
        "veridian-site-initializer.zip",
    ]


# --- scripts/ (LDM-#2533) --------------------------------------------------


def test_a_workspace_scripts_dir_is_named_in_a_warning(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path / "ws")
    (workspace / "scripts" / "tools").mkdir(parents=True)
    (workspace / "scripts" / "build.sh").write_text("./gradlew build\n")
    (workspace / "scripts" / "tools" / "release.sh").write_text("echo\n")

    with patch("ldm_core.pipelines.import_pipeline.UI.warning") as warned:
        paths = _link(tmp_path, monkeypatch, workspace)

    said = "\n".join(str(c.args[0]) for c in warned.call_args_list)
    assert "build.sh" in said
    assert "tools/release.sh" in said
    assert "EXECUTES" in said
    # Warned, not refused: the copy still happens.
    assert (paths["scripts"] / "build.sh").is_file()


def test_no_scripts_dir_no_warning(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path / "ws")
    with patch("ldm_core.pipelines.import_pipeline.UI.warning") as warned:
        _link(tmp_path, monkeypatch, workspace)
    said = "\n".join(str(c.args[0]) for c in warned.call_args_list)
    assert "scripts/" not in said


# --- --db without a boot ---------------------------------------------------


def _setup_stage(tmp_path, db=None, manifest=None):
    source = tmp_path / "ws"
    source.mkdir(exist_ok=True)
    project_path = tmp_path / "proj"

    manager = MagicMock()
    manager.non_interactive = True
    manager.args = MagicMock(
        project="proj",
        project_flag=None,
        host_name=None,
        ssl=None,
        port=None,
        db=db,
        tag=None,
    )
    manager.detect_project_path.return_value = project_path
    manager.setup_paths.return_value = {"root": project_path}
    manager.read_meta.side_effect = lambda _p: dict(manifest or {})

    context = ImportPipelineContext(manager=manager)
    context.set("source_resolved", source)
    if manifest is not None:
        backup = tmp_path / "extract"
        backup.mkdir()
        (backup / "meta").write_text("{}")
        context.set("backup_dir", backup)
    with (
        patch("ldm_core.pipelines.import_pipeline.UI"),
        patch.object(ProjectSetupStage, "_record_linked_workspace"),
        patch.object(ProjectSetupStage, "_resolve_cloud_project_id"),
    ):
        ProjectSetupStage().execute(context)
    return manager.write_meta.call_args.args[1]


def test_link_db_is_recorded_without_a_run(tmp_path):
    assert _setup_stage(tmp_path, db="postgresql")["db_type"] == "postgresql"


def test_no_db_flag_records_no_engine(tmp_path):
    assert "db_type" not in _setup_stage(tmp_path)


def test_a_package_manifest_keeps_its_own_engine(tmp_path):
    meta = _setup_stage(tmp_path, db="mysql", manifest={"db_type": "postgresql"})
    assert meta["db_type"] == "postgresql"


# --- --build before the sync -----------------------------------------------


def test_the_workspace_is_built_before_it_is_synced():
    order = [type(s) for s in ImportPipeline().stages]
    assert order.index(BuildWorkspaceStage) < order.index(VolumeSyncStage)


def test_link_build_links_what_the_build_produced(tmp_path, monkeypatch):
    """Through the pipeline's own stage order: a clean checkout has no dist/
    until gradlew runs, so syncing first links nothing."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "gradlew").write_text("")
    built = _make_workspace(tmp_path / "built")

    manager = _Manager()
    manager.args.build = True

    def _gradle(cmd, **_kwargs):
        shutil.copytree(built, workspace, dirs_exist_ok=True)
        return ""

    manager.run_command.side_effect = _gradle

    monkeypatch.chdir(tmp_path)
    paths = _project_paths(tmp_path / "project")
    context = _Context(
        manager,
        paths=paths,
        extracted_source=workspace,
        workspace_root=workspace,
        overwrite=True,
    )
    context.set("is_brand_new", True)
    stages = [
        s
        for s in ImportPipeline().stages
        if isinstance(s, (BuildWorkspaceStage, VolumeSyncStage))
    ]
    with patch("ldm_core.pipelines.import_pipeline.os.chmod"):
        for stage in stages:
            stage.execute(context)

    assert [p.name for p in Path(paths["cx"]).glob("*.zip")] == ["eco-theme.zip"]
