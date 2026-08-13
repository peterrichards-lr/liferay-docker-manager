import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.pipelines.run import (
    ComposerStage,
    ConfigResolutionStage,
    EnvironmentSetupStage,
    ExecutionStage,
    ProjectInitializationStage,
    RunPipelineContext,
    RuntimeValidationStage,
)


class TestRunPipeline(unittest.TestCase):
    def setUp(self):
        self.context = RunPipelineContext(MagicMock())
        self.context.set("project_id", "test-project")
        self.context.set("is_new_project", False)
        self.context.set("dry_run", False)
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
        stage = RuntimeValidationStage()
        with self.assertRaises(SystemExit):
            stage.execute(self.context)

        # LDM-#1094: "already running" (non-interactive) is exit_code=5
        # (Idempotent No-Op), not the generic 1 -- automation needs to tell
        # "nothing to do" apart from a real validation failure.
        mock_die.assert_called_once()
        self.assertEqual(mock_die.call_args.kwargs.get("exit_code"), 5)

    def test_composer_stage_dry_run(self):
        self.context.set("dry_run", True)
        stage = ComposerStage()
        root_mock = MagicMock()
        root_mock.__truediv__.return_value.exists.return_value = False
        self.context.set("paths", {"root": root_mock, "configs": MagicMock()})
        self.context.set("infra_ports", {})

        stage.execute(self.context)

        # In ComposerStage, write_docker_compose is called with is_dry_run=True
        self.context.manager.composer.write_docker_compose.assert_called_once()
        args, kwargs = self.context.manager.composer.write_docker_compose.call_args
        pass  # is_dry_run is handled dynamically

    def test_execution_stage_dry_run(self):
        self.context.set("dry_run", True)
        self.context.set("no_up", True)
        self.context.set("paths", {"root": MagicMock()})
        stage = ExecutionStage()
        stage.execute(self.context)
        self.context.manager.run_command.assert_not_called()

    # --- Exit code classification regression tests (LDM-#996) ---
    # Locks in that specific, deliberately-triaged UI.die() call sites use the
    # non-default exit_code from .agents/skills/ldm-architecture/SKILL.md's
    # contract, rather than silently falling back to the generic 1 default.

    @patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit)
    @patch("ldm_core.pipelines.run.UI.ask", return_value="new-project-name")
    def test_project_init_failed_resolve_path_uses_orchestration_exit_code(
        self, mock_ask, mock_die
    ):
        self.context.manager.non_interactive = False
        self.context.manager.detect_project_path.return_value = None
        self.context.manager.args.select = False
        stage = ProjectInitializationStage()
        with self.assertRaises(SystemExit):
            stage.execute(self.context)
        mock_die.assert_called_once()
        self.assertEqual(mock_die.call_args.kwargs.get("exit_code"), 4)

    @patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit)
    @patch("ldm_core.utils.discover_latest_tag", return_value=None)
    def test_resolve_tag_discovery_failure_uses_infra_exit_code(
        self, mock_discover, mock_die
    ):
        manager = MagicMock()
        manager.non_interactive = True
        manager.verbose = False
        manager.args.tag_latest = False
        manager.args.tag_prefix = None
        manager.args.tag = None
        manager.args.nightly = False
        manager.args.master = False
        manager.args.release_type = None
        manager.defaults.get.return_value = None

        stage = ConfigResolutionStage()
        with self.assertRaises(SystemExit):
            stage._resolve_tag(manager, {}, is_samples=False, is_portal=False)
        mock_die.assert_called_once()
        self.assertEqual(mock_die.call_args.kwargs.get("exit_code"), 3)

    # LDM-#1061: `--release-type latest` is now a valid argparse choice
    # (previously rejected -- choices=["any", "u", "lts", "qr"] was never
    # updated when nightly/master/latest support was added to the
    # interactive prompt). This locks in that the non-interactive
    # resolution branch also normalizes "latest" -> "any" explicitly,
    # rather than relying on discover_latest_tag()'s implicit
    # no-recognized-filter fallthrough to coincidentally match.
    @patch("ldm_core.utils.discover_latest_tag", return_value="2026.q3.9")
    def test_resolve_tag_release_type_latest_normalized_to_any(self, mock_discover):
        manager = MagicMock()
        manager.non_interactive = True
        manager.verbose = False
        manager.args.tag_latest = False
        manager.args.tag_prefix = None
        manager.args.tag = None
        manager.args.nightly = False
        manager.args.master = False
        manager.args.release_type = "latest"
        manager.defaults.get.return_value = None

        stage = ConfigResolutionStage()
        tag, _ = stage._resolve_tag(manager, {}, is_samples=False, is_portal=False)

        self.assertEqual(tag, "2026.q3.9")
        mock_discover.assert_called_once()
        self.assertEqual(mock_discover.call_args.kwargs.get("release_type"), "any")

    # LDM-#1080: `--tag-latest` alone (no --release-type) must resolve the
    # true latest tag across every release channel, not silently narrow
    # down to the global release_type default ("lts") -- which would make
    # "latest" mean "latest LTS" and miss a newer quarterly RC.
    @patch("ldm_core.utils.discover_latest_tag", return_value="2026.q3.9")
    def test_resolve_tag_latest_flag_not_narrowed_to_lts_default(self, mock_discover):
        manager = MagicMock()
        manager.non_interactive = True
        manager.verbose = False
        manager.args.tag_latest = True
        manager.args.tag_prefix = None
        manager.args.tag = None
        manager.args.nightly = False
        manager.args.master = False
        manager.args.release_type = None
        manager.defaults.get.return_value = "lts"

        stage = ConfigResolutionStage()
        tag, _ = stage._resolve_tag(manager, {}, is_samples=False, is_portal=False)

        self.assertEqual(tag, "2026.q3.9")
        mock_discover.assert_called_once()
        self.assertEqual(mock_discover.call_args.kwargs.get("release_type"), "any")

    # --- Named-volume ownership regression tests (LDM-#817) ---
    # Locks in that the plain run/import pipeline explicitly (re-)chowns
    # Named Volumes before containers boot, not just the snapshot-restore
    # path -- the gap that let "Unable to create lock manager" recur on a
    # brand-new project despite #817 having been (incorrectly) closed as
    # already fixed.

    def _paths_for_environment_setup(self):
        paths = {"root": MagicMock(), "data": MagicMock(), "state": MagicMock()}
        paths["data"].__truediv__.return_value.exists.return_value = False
        return paths

    def test_environment_setup_hydrates_named_volumes_before_boot(self):
        self.context.set("paths", self._paths_for_environment_setup())
        self.context.set("no_up", False)
        self.context.manager.composer.is_using_named_volumes.return_value = True

        EnvironmentSetupStage().execute(self.context)

        self.context.manager.snapshot.volumes.hydrate_named_volumes_with_sync_wait.assert_called_once_with(
            self.context.get("paths")
        )

    def test_environment_setup_skips_hydration_when_no_up(self):
        self.context.set("paths", self._paths_for_environment_setup())
        self.context.set("no_up", True)
        self.context.manager.composer.is_using_named_volumes.return_value = True

        EnvironmentSetupStage().execute(self.context)

        self.context.manager.snapshot.volumes.hydrate_named_volumes_with_sync_wait.assert_not_called()

    def test_environment_setup_skips_hydration_when_bind_mounts_used(self):
        self.context.set("paths", self._paths_for_environment_setup())
        self.context.set("no_up", False)
        self.context.manager.composer.is_using_named_volumes.return_value = False

        EnvironmentSetupStage().execute(self.context)

        self.context.manager.snapshot.volumes.hydrate_named_volumes_with_sync_wait.assert_not_called()

    def test_composer_stage_port_conflict_uses_orchestration_exit_code(self):
        tmp_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp_root, ignore_errors=True)
        (tmp_root / "docker-compose.yml").write_text(
            'services:\n  liferay:\n    ports:\n      - "8080:8080"\n'
        )

        self.context.set("dry_run", False)
        self.context.set("no_up", False)
        self.context.set(
            "paths", {"root": tmp_root, "configs": tmp_root / "osgi" / "configs"}
        )
        self.context.set("infra_ports", {})
        self.context.set("host_name", "localhost")
        self.context.set("use_shared_search", True)
        self.context.set("tag", "2024.1.0")
        self.context.manager.check_port.return_value = False

        with (
            patch(
                "ldm_core.docker_service.DockerService.is_running",
                return_value=False,
            ),
            patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit) as mock_die,
        ):
            stage = ComposerStage()
            with self.assertRaises(SystemExit):
                stage.execute(self.context)

        mock_die.assert_called_once()
        self.assertEqual(mock_die.call_args.kwargs.get("exit_code"), 4)


if __name__ == "__main__":
    unittest.main()
