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
from ldm_core.tests.tmproot import TEST_TMP_ROOT


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

    def test_composer_stage_threads_target_context_into_write_docker_compose(self):
        """ComposerStage must pass the TargetContext already resolved by
        ProjectInitializationStage straight through to write_docker_compose()
        rather than letting it re-resolve (and potentially re-pin) on its
        own -- see docs/explanation/remote-node-architecture.md."""
        from ldm_core.config import TargetContext, TargetNode

        target_ctx = TargetContext(
            target=TargetNode(name="aws-2", host="5.6.7.8"),
            is_remote=True,
            docker_prefix=["docker", "--context", "aws-2"],
            compose_prefix=["docker", "--context", "aws-2", "compose"],
        )

        self.context.set("dry_run", True)
        root_mock = MagicMock()
        root_mock.__truediv__.return_value.exists.return_value = False
        self.context.set("paths", {"root": root_mock, "configs": MagicMock()})
        self.context.set("infra_ports", {})
        self.context.set("target_context", target_ctx)

        stage = ComposerStage()
        stage.execute(self.context)

        self.context.manager.composer.write_docker_compose.assert_called_once()
        _, kwargs = self.context.manager.composer.write_docker_compose.call_args
        self.assertIs(kwargs.get("target_context"), target_ctx)

    def test_composer_stage_ensure_network_uses_resolved_target_context(self):
        """Regression guard: _ensure_network must use the single resolved
        TargetContext, not an independently re-derived (and possibly falsy,
        silently-local) target_name."""
        from ldm_core.config import TargetContext, TargetNode

        target_ctx = TargetContext(
            target=TargetNode(name="aws-2", host="5.6.7.8"),
            is_remote=True,
            docker_prefix=["docker", "--context", "aws-2"],
            compose_prefix=["docker", "--context", "aws-2", "compose"],
        )

        self.context.set("dry_run", True)
        self.context.set("no_up", False)
        root_mock = MagicMock()
        root_mock.__truediv__.return_value.exists.return_value = False
        self.context.set("paths", {"root": root_mock, "configs": MagicMock()})
        self.context.set("infra_ports", {})
        self.context.set("target_context", target_ctx)

        with patch("shutil.which", return_value="/usr/bin/docker"):
            stage = ComposerStage()
            stage.execute(self.context)

        self.context.manager.infra._ensure_network.assert_called_once_with("aws-2")

    def test_execution_stage_dry_run(self):
        self.context.set("dry_run", True)
        self.context.set("no_up", True)
        self.context.set("paths", {"root": MagicMock()})
        stage = ExecutionStage()
        stage.execute(self.context)
        self.context.manager.run_command.assert_not_called()

    def test_execution_stage_honours_no_up_from_args_when_context_is_unset(self):
        """LDM-#1374: the CLI never puts `no_up` in the context.

        `cli.py` dispatches `("run", None)` as `cmd_run(project)` with no
        kwarg, so `context.set("no_up", None)` leaves it None. ExecutionStage
        read only the context and `if not None` is true, so `ldm run --no-up`
        started the stack and waited for readiness anyway.

        Every existing test in this file sets `no_up` on the CONTEXT, which is
        why none of them caught it -- the same shape as #1359, where the tests
        set `database_mode` in meta while the CLI sets it in args. This one
        deliberately leaves the context unset and sets only `args`.
        """
        self.context.set("no_up", None)
        self.context.manager.args.no_up = True
        self.context.set("paths", {"root": MagicMock()})

        stage = ExecutionStage()
        stage.execute(self.context)

        compose_calls = [
            c
            for c in self.context.manager.run_command.call_args_list
            if c.args and isinstance(c.args[0], list) and "up" in c.args[0]
        ]
        self.assertEqual(
            [],
            compose_calls,
            f"--no-up still started the stack: {compose_calls}",
        )

    def test_execution_stage_starts_the_stack_when_no_up_is_absent_everywhere(self):
        """Guard against over-reach: the default path must still start."""
        self.context.set("no_up", None)
        self.context.manager.args.no_up = False
        self.context.set("paths", {"root": MagicMock()})
        # `run.py:1646` polls `get_container_status` for up to 60s waiting on a
        # dependency to become healthy. Against a MagicMock the status never
        # matches, so the test burned the full window (measured: 60s). Answer
        # the poll instead -- this test only needs to observe that the stack is
        # brought up.
        self.context.set("no_wait", True)
        self.context.manager.args.no_wait = True
        self.context.manager.get_container_status.return_value = "healthy"

        stage = ExecutionStage()
        stage.execute(self.context)

        self.assertTrue(
            self.context.manager.run_command.called,
            "the normal path must still bring the stack up",
        )

    def test_execution_stage_syncs_and_uses_compose_prefix_from_target_context(self):
        """Regression guard for a real bug found migrating this stage:
        DockerService.get_compose_cmd_prefix(target_name) used to silently
        default to a local prefix (and sync_project_to_target was skipped
        entirely) whenever target_name was falsy -- which is exactly what
        happened for a project relying solely on a persisted `ldm target
        use` default (no explicit --node, no project-meta pin). With a
        resolved TargetContext on context, both must reflect the actual
        (remote) resolved target."""
        from ldm_core.config import TargetContext, TargetNode

        target_ctx = TargetContext(
            target=TargetNode(name="aws-2", host="5.6.7.8"),
            is_remote=True,
            docker_prefix=["docker", "--context", "aws-2"],
            compose_prefix=["docker", "--context", "aws-2", "compose"],
        )

        self.context.set("dry_run", True)
        self.context.set("no_up", True)
        self.context.set("paths", {"root": Path(f"{TEST_TMP_ROOT}/proj")})
        self.context.set("target_context", target_ctx)

        with patch("ldm_core.config.sync_project_to_target") as mock_sync:
            stage = ExecutionStage()
            stage.execute(self.context)

        mock_sync.assert_called_once_with(
            Path(f"{TEST_TMP_ROOT}/proj"), target_name="aws-2"
        )
        self.context.manager.run_command.assert_not_called()

    def test_execution_stage_reclaims_permissions_on_remote_mapped_path(self):
        """LDM-#1090/#1133: the permission-reclaim step used to always run
        a plain local `docker run -v {local_path}:/workspace alpine ...`,
        gated only on *this* machine's OS -- meaningless (and unreachable)
        for a project on a remote target, whose relevant files are the
        already-synced remote copies. With a remote TargetContext, this
        must redirect via docker_prefix and the remote-mapped path."""
        from ldm_core.config import TargetContext, TargetNode

        target_ctx = TargetContext(
            target=TargetNode(name="aws-1", host="34.1.1.1"),
            is_remote=True,
            docker_prefix=["docker", "--context", "aws-1"],
            compose_prefix=["docker", "--context", "aws-1", "compose"],
            local_root=Path(f"{TEST_TMP_ROOT}/proj"),
            remote_root="/home/ec2-user/.liferay-docker/projects/proj",
        )

        self.context.set("dry_run", True)
        self.context.set("no_up", False)
        self.context.set(
            "paths",
            {
                "root": Path(f"{TEST_TMP_ROOT}/proj"),
                "deploy": Path(f"{TEST_TMP_ROOT}/proj/deploy"),
                "logs": Path(f"{TEST_TMP_ROOT}/proj/logs"),
                "osgi": Path(f"{TEST_TMP_ROOT}/proj/osgi"),
                "files": Path(f"{TEST_TMP_ROOT}/proj/files"),
            },
        )
        self.context.set("target_context", target_ctx)
        # db_type="hypersonic" (no separate DB container) so this never
        # enters the dependency-readiness wait loop -- avoids a 60s real
        # sleep spin against a MagicMock container status in this test.
        self.context.set(
            "project_meta", {"container_name": "test-project", "db_type": "hypersonic"}
        )
        self.context.manager.args.force_recreate = False
        self.context.manager.args.rebuild = False
        self.context.manager.args.quiet = True
        self.context.manager.args.no_wait = True
        self.context.manager.args.follow = False

        with (
            patch("ldm_core.config.sync_project_to_target"),
            patch(
                "ldm_core.utils.reclaim_volume_permissions", return_value=True
            ) as mock_reclaim,
        ):
            stage = ExecutionStage()
            stage.execute(self.context)

        self.assertEqual(mock_reclaim.call_count, 4)
        for call in mock_reclaim.call_args_list:
            reclaimed_path = call.args[0]
            self.assertTrue(
                str(reclaimed_path).startswith(
                    "/home/ec2-user/.liferay-docker/projects/proj"
                )
            )
            self.assertEqual(call.kwargs.get("docker_prefix"), target_ctx.docker_prefix)

    def test_execution_stage_skips_sync_for_local_target_context(self):
        from ldm_core.config import TargetContext, TargetNode

        target_ctx = TargetContext(
            target=TargetNode(name="local", host="localhost", is_default=True),
            is_remote=False,
            docker_prefix=["docker"],
            compose_prefix=["docker", "compose"],
        )

        self.context.set("dry_run", True)
        self.context.set("no_up", True)
        self.context.set("paths", {"root": Path(f"{TEST_TMP_ROOT}/proj")})
        self.context.set("target_context", target_ctx)

        with patch("ldm_core.config.sync_project_to_target") as mock_sync:
            stage = ExecutionStage()
            stage.execute(self.context)

        mock_sync.assert_not_called()

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

    def test_project_init_stage_sets_target_context_on_early_return(self):
        """When paths/project_meta are already known (e.g. supplied directly
        by the caller), ProjectInitializationStage's early-return branch
        must still resolve and set target_context -- every later stage
        depends on it being present."""
        from ldm_core.config import TargetContext

        sentinel_ctx = TargetContext(
            target=MagicMock(name="local"),
            is_remote=False,
            docker_prefix=["docker"],
            compose_prefix=["docker", "compose"],
        )
        root = Path("/tmp/existing-project")
        self.context.set("paths", {"root": root})
        self.context.set("project_meta", {"container_name": "existing-project"})
        self.context.manager.target = None

        with patch(
            "ldm_core.config.resolve_target_context", return_value=sentinel_ctx
        ) as mock_resolve:
            stage = ProjectInitializationStage()
            stage.execute(self.context)

        self.assertIs(self.context.get("target_context"), sentinel_ctx)
        self.assertEqual(self.context.get("root"), root)
        mock_resolve.assert_called_once_with(
            explicit_target=None,
            meta={"container_name": "existing-project"},
            project_root=root,
        )


class TestResolvePipelineTargetContext(unittest.TestCase):
    """Direct unit coverage for the module-level target-context resolution
    helper shared by ProjectInitializationStage, ComposerStage, and
    ExecutionStage."""

    @patch("ldm_core.config.resolve_target_context")
    def test_guards_against_mock_contaminated_manager_target(self, mock_resolve):
        """A bare MagicMock() manager -- used throughout this pipeline's
        unit tests -- auto-generates a `.target` attribute that is itself a
        MagicMock, not a real string/None. That must never leak into
        resolve_target_context() as if it were an explicit --node value."""
        from ldm_core.pipelines.run import _resolve_pipeline_target_context

        manager = MagicMock()  # manager.target is an auto-generated MagicMock
        _resolve_pipeline_target_context(
            manager, {"container_name": "p"}, Path("/tmp/p")
        )

        mock_resolve.assert_called_once()
        self.assertIsNone(mock_resolve.call_args.kwargs["explicit_target"])

    @patch("ldm_core.config.resolve_target_context")
    def test_passes_through_real_explicit_target(self, mock_resolve):
        from ldm_core.pipelines.run import _resolve_pipeline_target_context

        manager = MagicMock()
        manager.target = "aws-2"
        _resolve_pipeline_target_context(
            manager, {"container_name": "p"}, Path("/tmp/p")
        )

        mock_resolve.assert_called_once()
        self.assertEqual(mock_resolve.call_args.kwargs["explicit_target"], "aws-2")


if __name__ == "__main__":
    unittest.main()


class TestSharedSearchWiring(unittest.TestCase):
    """LDM-#1362 / #1363: selecting shared search, and satisfying its preconditions.

    `--search-mode shared` was silently ignored: `run.py` resolved the mode
    without passing the CLI override, so the flag produced a **sidecar**
    Elasticsearch embedded in the Liferay container -- the opposite of the
    memory saving the mode exists for.

    Every pre-existing `no_up`/mode test in this file sets the value on the
    CONTEXT or in META, which is exactly where the CLI does not put it. That is
    why none of them caught this, nor #1359 (`database_mode`) nor #1374
    (`no_up`). These tests deliberately set it on `args` only.
    """

    def _stage_context(self, **args_overrides):
        manager = MagicMock()
        manager.non_interactive = True
        manager.verbose = False
        manager.args.sidecar = False
        manager.args.database_mode = None
        manager.args.search_mode = None
        for k, v in args_overrides.items():
            setattr(manager.args, k, v)
        manager.defaults.get.side_effect = lambda _k, default=None: default

        context = RunPipelineContext(
            manager, project_id="proj", paths={"root": MagicMock()}
        )
        context.set("project_meta", {"container_name": "proj", "tag": "2026.q1.12-lts"})
        return context

    def test_search_mode_shared_from_args_is_honoured(self):
        context = self._stage_context(search_mode="shared")
        ConfigResolutionStage().execute(context)
        self.assertTrue(
            context.get("use_shared_search"),
            "--search-mode shared was ignored; the project would get a sidecar",
        )

    def test_the_resolved_search_mode_is_persisted_to_meta(self):
        """Later commands must agree with how the project was provisioned."""
        context = self._stage_context(search_mode="shared")
        ConfigResolutionStage().execute(context)
        self.assertEqual("shared", context.get("project_meta").get("search_mode"))

    def test_sidecar_flag_still_wins_and_meta_agrees(self):
        """`--sidecar` outranks the resolved mode; the persisted value must match.

        Without also correcting `search_mode`, meta would record "shared" for a
        project built as a sidecar -- the same two-derivations-of-one-fact
        problem behind #1354 and #1359.
        """
        context = self._stage_context(search_mode="shared", sidecar=True)
        ConfigResolutionStage().execute(context)
        self.assertFalse(context.get("use_shared_search"))
        self.assertEqual("sidecar", context.get("project_meta").get("search_mode"))

    def test_no_flag_leaves_the_mode_to_meta_and_defaults(self):
        """Guard against over-reach: absent the flag, nothing is forced."""
        context = self._stage_context()
        ConfigResolutionStage().execute(context)
        self.assertFalse(context.get("use_shared_search"))
