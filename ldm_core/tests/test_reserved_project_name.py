"""LDM-#2457: no project may share the infra stack's Compose project.

The global infra stack runs `infra-compose.yml` with no `-p`, so Compose names
it after the directory holding the file: `resources`. A project's compose file
sets `name:` to its sanitized project name, so a project called `resources`
shares that Compose project, and its `up --remove-orphans` removes Traefik.

Every test here asserts the refusal happens before any command is run: a
refusal that arrives after `docker compose up` has already removed Traefik is
no refusal at all.
"""

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.utils import reserved_compose_project_name

# `résources` and `resources!` both sanitize to `resources` (`sanitize_id`
# strips diacritics and characters Docker refuses); `Resources` differs only
# in case.
REFUSED = ["resources", "Resources", "RESOURCES", "résources", "resources!"]
ACCEPTED = ["my-resources", "resource", "resources-dev", "myproject"]


class TestReservedName(unittest.TestCase):
    def test_reserved_names_and_their_sanitized_equivalents(self):
        for name in REFUSED:
            with self.subTest(name=name):
                self.assertEqual(reserved_compose_project_name(name), "resources")

    def test_ordinary_names_are_not_reserved(self):
        for name in [*ACCEPTED, None, ""]:
            with self.subTest(name=name):
                self.assertIsNone(reserved_compose_project_name(name))


class _RealManagerCase(unittest.TestCase):
    """A real LiferayManager, so the real `detect_project_path` runs.

    LDM_HOME and the cwd are both temp directories, so nothing resolves from,
    or is written to, the developer's own state.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self._env = patch.dict(os.environ, {"LDM_HOME": str(self.tmp / "home")})
        self._env.start()
        self._cwd = os.getcwd()
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir()
        os.chdir(self.workspace)

        from ldm_core.manager import LiferayManager

        args = argparse.Namespace(
            verbose=False, info=False, yes=True, project=None, project_flag=None
        )
        self.manager = LiferayManager(args)
        self.manager.non_interactive = True
        self.run_command = MagicMock(return_value="")
        setattr(self.manager, "run_command", self.run_command)  # noqa: B010

    def tearDown(self):
        os.chdir(self._cwd)
        self._env.stop()
        self._tmp.cleanup()

    def assert_refused(self, fn):
        with self.assertRaises(SystemExit) as cm:
            fn()
        self.assertEqual(cm.exception.code, 1)
        self.run_command.assert_not_called()


class TestProjectResolutionRefuses(_RealManagerCase):
    """`detect_project_path(..., for_init=True)` -- the one place `run`,
    `init`, every `import` variant, `quickstart`, hydration and snapshot
    restore resolve the project they create or start."""

    def test_new_project_named_resources_is_refused(self):
        for name in REFUSED:
            with self.subTest(name=name):
                self.assert_refused(
                    lambda name=name: self.manager.detect_project_path(
                        name, for_init=True
                    )
                )

    def test_existing_project_whose_meta_names_it_resources_is_refused(self):
        """The compose `name:` comes from meta `container_name` first, so a
        project in an innocuous directory can still collide."""
        root = self.workspace / "innocuous"
        root.mkdir()
        (root / "meta").write_text("container_name=resources\n")

        self.assert_refused(
            lambda: self.manager.detect_project_path(str(root), for_init=True)
        )

    def test_an_ordinary_name_is_accepted(self):
        for name in ACCEPTED:
            with self.subTest(name=name):
                path = self.manager.detect_project_path(name, for_init=True)
                self.assertEqual(Path(path).name, name)
        self.run_command.assert_not_called()


class TestRunPipelineRefuses(_RealManagerCase):
    """`ldm run resources` / `ldm init resources`, new or existing."""

    def _stage(self, project_id):
        from ldm_core.pipelines.run import (
            ProjectInitializationStage,
            RunPipelineContext,
        )

        context = RunPipelineContext(manager=self.manager, project_id=project_id)
        return lambda: ProjectInitializationStage().execute(context)

    def test_new_project(self):
        self.assert_refused(self._stage("Resources"))
        self.assertFalse((self.workspace / "Resources").exists())

    def test_existing_project(self):
        root = self.workspace / "resources"
        root.mkdir()
        (root / "meta").write_text("container_name=resources\n")

        self.assert_refused(self._stage("resources"))

    def test_ordinary_name_passes_the_stage(self):
        stage = self._stage("myproject")
        with patch("ldm_core.pipelines.run.UI.print_banner"):
            stage()
        self.run_command.assert_not_called()


class TestImportAndLinkRefuse(_RealManagerCase):
    def test_link_of_a_workspace_directory_named_resources(self):
        """`ldm link ./resources` derives the project name from the directory
        (`ProjectSetupStage`), so no name is ever passed explicitly."""
        from ldm_core.pipelines.import_pipeline import (
            ImportPipelineContext,
            ProjectSetupStage,
        )

        source = self.tmp / "src" / "resources"
        source.mkdir(parents=True)
        context = ImportPipelineContext(
            manager=self.manager,
            source_path=str(source),
            project_name=None,
            is_init_from=True,
            no_run=True,
        )
        context.set("source_resolved", source)

        self.assert_refused(lambda: ProjectSetupStage().execute(context))

    def test_import_with_an_explicit_reserved_name_downloads_nothing(self):
        from ldm_core.workspace import importer

        fake = MagicMock()
        fake.manager.args = argparse.Namespace(project=None, project_flag=None)
        with patch.object(importer, "_download_remote_archive") as download:
            with self.assertRaises(SystemExit) as cm:
                importer.cmd_import(
                    fake, "https://example.com/pkg.ldmp", project_id="Resources"
                )
        self.assertEqual(cm.exception.code, 1)
        download.assert_not_called()

    def test_import_with_an_ordinary_name_proceeds(self):
        from ldm_core.workspace import importer

        fake = MagicMock()
        fake.manager.args = argparse.Namespace(project=None, project_flag=None)
        with patch.object(importer, "_download_remote_archive") as download:
            importer.cmd_import(
                fake, "https://example.com/pkg.ldmp", project_id="myproject"
            )
        download.assert_called_once()


class TestForkRefuses(unittest.TestCase):
    def test_fork_target_named_resources(self):
        from ldm_core.workspace.fork import cmd_fork

        fake = MagicMock()
        with self.assertRaises(SystemExit) as cm:
            cmd_fork(fake, "source", "Resources")
        self.assertEqual(cm.exception.code, 1)
        # Refused before the source is even resolved, let alone snapshotted.
        fake.manager.detect_project_path.assert_not_called()
        fake.manager.run_command.assert_not_called()


class TestComposerBackstop(unittest.TestCase):
    def test_compose_generation_refuses_a_reserved_name(self):
        from ldm_core.handlers.composer import ComposerService

        manager = MagicMock()
        root = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(SystemExit) as cm:
                ComposerService(manager).write_docker_compose(
                    {"root": root}, {"container_name": "resources"}
                )
            self.assertEqual(cm.exception.code, 1)
            manager.run_command.assert_not_called()
            self.assertFalse((root / "docker-compose.yml").exists())
        finally:
            root.rmdir()


if __name__ == "__main__":
    unittest.main()
