import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.runtime import RuntimeService
from ldm_core.tests.tmproot import TEST_TMP_ROOT


class MockRuntime(BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.args.tag_latest = False
        self.args.tag_prefix = None
        self.args.timeout = 900
        self.verbose = False
        self.non_interactive = True
        self.dry_run = False

        # Self-referential manager for service compatibility
        from typing import Any, cast

        self.manager = cast(Any, self)

        self.assets = MagicMock()
        self.infra = MagicMock()
        self.snapshot = MagicMock()
        self.share = MagicMock()
        self.license = MagicMock()
        self.diagnostics = MagicMock()
        self.share.resolve_share_config.return_value = ("lfr-tunnel", "lfr-demo.online")
        from ldm_core.defaults import DefaultsManager
        from ldm_core.handlers.composer import ComposerService
        from ldm_core.handlers.config import ConfigService

        self.defaults = DefaultsManager()
        self.config = ConfigService(self)
        self.config.update_portal_ext = MagicMock()  # type: ignore[method-assign]
        self.composer = ComposerService(self)
        self.handler = RuntimeService(self)
        self.runtime = self.handler
        self.verify_runtime_environment = MagicMock()  # type: ignore[method-assign]

    def cmd_run(self, *args, **kwargs):
        return self.handler.cmd_run(*args, **kwargs)

    def cmd_stop(self, *args, **kwargs):
        return self.handler.cmd_stop(*args, **kwargs)

    def cmd_restart(self, *args, **kwargs):
        return self.handler.cmd_restart(*args, **kwargs)

    def cmd_down(self, *args, **kwargs):
        return self.handler.cmd_down(*args, **kwargs)

    def cmd_logs(self, *args, **kwargs):
        return self.handler.cmd_logs(*args, **kwargs)

    def cmd_wait(self, *args, **kwargs):
        return self.handler.cmd_wait(*args, **kwargs)

    def _wait_for_ready(self, *args, **kwargs):
        return self.handler._wait_for_ready(*args, **kwargs)

    def get_resource_path(self, name):
        return Path("/tmp/res") / name

    def get_config(self, key, default=None):
        return default

    def read_meta(self, *args, **kwargs):
        return {"container_name": "test-runtime", "host_name": "localhost"}

    def setup_paths(self, root):
        return super().setup_paths(root)

    def _ensure_seeded(self, *args, **kwargs):
        return False

    def write_meta(self, *args, **kwargs):
        pass

    def _is_ssl_active(self, *args, **kwargs):
        return False

    def _ensure_network(self, *args, **kwargs):
        pass

    def setup_infrastructure(self, *args, **kwargs):
        pass

    def write_docker_compose(self, *args, **kwargs):
        pass


class TestOrchestration(unittest.TestCase):
    def setUp(self):
        from unittest.mock import MagicMock, patch

        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self.tmp_dir_obj.name)
        self.handler = MockRuntime()
        self.handler.detect_project_path = MagicMock(return_value=self.tmp_dir)  # type: ignore[method-assign]

        # Globally mock requests.get for _wait_for_ready tests to prevent hanging/failing
        self.req_patcher = patch("requests.get")
        self.mock_req = self.req_patcher.start()
        self.mock_req.return_value = MagicMock(status_code=200)

        self.update_patcher = patch(
            "ldm_core.diagnostics.doctor.check_for_updates", return_value=(None, None)
        )
        self.update_patcher.start()

    def tearDown(self):
        self.req_patcher.stop()
        self.update_patcher.stop()

    def test_resolve_container_label_discovery(self):
        """Verify that resolve_container uses Docker labels for discovery."""
        with patch.object(BaseHandler, "run_command") as mock_run:
            # Mock 'docker ps' returning a renamed container
            mock_run.return_value = "a8cf79c6a3b2_my-project-liferay-1"

            res = self.handler.resolve_container("my-project", "liferay")

            # Verify the call used labels
            mock_run.assert_called()
            args = mock_run.call_args[0][0]
            self.assertIn("label=com.liferay.ldm.project=my-project", args)
            self.assertIn("label=com.docker.compose.service=liferay", args)

            # Verify it returned the discovered name
            self.assertEqual(res, "a8cf79c6a3b2_my-project-liferay-1")

    def test_resolve_container_fallback(self):
        """Verify that resolve_container falls back to standard name if labels fail."""
        with patch.object(BaseHandler, "run_command") as mock_run:
            mock_run.return_value = ""

            res = self.handler.resolve_container("my-project", "db")

            self.assertEqual(res, "my-project-db-1")

    @patch("ldm_core.docker_service.get_active_target")
    def test_cmd_stop_basic(self, mock_target):
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )
        with patch.object(BaseHandler, "run_command") as mock_run:
            self.handler.cmd_stop("test")
            # Verify stop command was issued
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            self.assertIn("stop", call_args)

    @patch("ldm_core.docker_service.get_active_target")
    def test_cmd_stop_remote_target(self, mock_target):
        """LDM-#1090/#1133: cmd_stop must resolve the project's own target
        (via meta["target"]), not always the local daemon -- previously it
        used the non-target-aware get_compose_cmd(), which always
        hardcoded a local prefix regardless of where the project actually
        runs."""
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
        with (
            patch.object(
                self.handler.manager, "read_meta", return_value={"target": "aws-1"}
            ),
            patch.object(BaseHandler, "run_command") as mock_run,
        ):
            self.handler.cmd_stop("test")
            call_args = mock_run.call_args[0][0]
            self.assertIn("--context", call_args)
            self.assertIn("aws-1", call_args)
            self.assertIn("stop", call_args)

    @patch("ldm_core.docker_service.get_active_target")
    def test_cmd_restart_basic(self, mock_target):
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )
        with patch.object(BaseHandler, "run_command") as mock_run:
            self.handler.cmd_restart("test")
            mock_run.assert_called()
            call_args = mock_run.call_args[0][0]
            self.assertIn("restart", call_args)

    @patch("ldm_core.docker_service.get_active_target")
    def test_cmd_restart_remote_target(self, mock_target):
        """LDM-#1090/#1133: same issue/fix as test_cmd_stop_remote_target."""
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(name="aws-2", host="5.6.7.8")
        with (
            patch.object(
                self.handler.manager, "read_meta", return_value={"target": "aws-2"}
            ),
            patch.object(BaseHandler, "run_command") as mock_run,
        ):
            self.handler.cmd_restart("test")
            call_args = mock_run.call_args[0][0]
            self.assertIn("--context", call_args)
            self.assertIn("aws-2", call_args)
            self.assertIn("restart", call_args)

    @patch("ldm_core.docker_service.get_active_target")
    @patch("ldm_core.runtime.orchestration.shutil.rmtree")
    def test_cmd_down_with_delete(self, mock_rmtree, mock_target):
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )
        with (
            patch.object(BaseHandler, "run_command"),
            patch.object(Path, "exists", return_value=True),
        ):
            self.handler.cmd_down("test", delete=True)
            # Verify down command AND directory deletion
            self.assertTrue(mock_rmtree.called)

    @patch("ldm_core.docker_service.get_active_target")
    def test_cmd_down_remote_target(self, mock_target):
        """LDM-#1090/#1133: cmd_down's compose-down and orphan-sweep docker
        ps/rm calls must all target the project's own resolved target, not
        always the local daemon."""
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
        with (
            patch.object(
                self.handler.manager, "read_meta", return_value={"target": "aws-1"}
            ),
            patch.object(BaseHandler, "run_command") as mock_run,
            patch.object(Path, "exists", return_value=True),
        ):
            mock_run.return_value = ""
            self.handler.cmd_down("test")
            calls_with_context = [
                c for c in mock_run.call_args_list if "--context" in c.args[0]
            ]
            self.assertTrue(
                calls_with_context, "No run_command call used --context aws-1"
            )
            for c in calls_with_context:
                self.assertIn("aws-1", c.args[0])

    @patch("ldm_core.docker_service.get_active_target")
    @patch("ldm_core.runtime.orchestration.subprocess.run")
    @patch("ldm_core.runtime.orchestration.shutil.rmtree")
    def test_cmd_down_delete_drops_shared_db_via_remote_target(
        self, mock_rmtree, mock_sub_run, mock_target
    ):
        """cmd_down(delete=True)'s shared-DB-drop docker exec must honor the
        project's resolved target (#1133) -- previously hardcoded "docker",
        silently dropping the schema on the LOCAL daemon's shared container
        instead of the remote one actually hosting it."""
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
        with (
            patch.object(
                self.handler.manager,
                "read_meta",
                return_value={
                    "target": "aws-1",
                    "database_mode": "shared",
                    "db_type": "postgresql",
                    "project_name": "test",
                },
            ),
            patch.object(BaseHandler, "run_command", return_value=""),
            patch.object(Path, "exists", return_value=True),
        ):
            self.handler.cmd_down("test", delete=True)

        self.assertTrue(mock_sub_run.called)
        drop_cmd = mock_sub_run.call_args[0][0]
        self.assertIn("--context", drop_cmd)
        self.assertIn("aws-1", drop_cmd)
        self.assertIn("dropdb", drop_cmd)

    @patch("ldm_core.docker_service.get_active_target")
    def test_cmd_shell_uses_remote_target_context(self, mock_target):
        """cmd_shell's resolve_container()/exec must honor the project's
        resolved target (#1133)."""
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
        with (
            patch.object(
                self.handler.manager, "read_meta", return_value={"target": "aws-1"}
            ),
            patch.object(BaseHandler, "run_command", return_value="proj-liferay-1"),
            patch("ldm_core.runtime.orchestration.subprocess.run") as mock_sub_run,
        ):
            self.handler.handler.orchestration.cmd_shell("test")

        self.assertTrue(mock_sub_run.called)
        exec_cmd = mock_sub_run.call_args[0][0]
        self.assertIn("--context", exec_cmd)
        self.assertIn("aws-1", exec_cmd)

    @patch("ldm_core.docker_service.get_active_target")
    @patch("ldm_core.runtime.orchestration.shutil.rmtree")
    def test_cmd_down_dry_run(self, mock_rmtree, mock_target):
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )
        self.handler.dry_run = True

    def test_cmd_start_remote_target(self) -> None:
        """Test cmd_start triggers auto-sync and uses target context prefix."""
        with (
            patch.object(
                self.handler.manager, "read_meta", return_value={"target": "aws-1"}
            ),
            patch("ldm_core.config.sync_project_to_target") as mock_sync,
            patch.object(BaseHandler, "run_command") as mock_run,
            patch("ldm_core.docker_service.get_active_target") as mock_target,
        ):
            from ldm_core.config import TargetNode

            mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
            self.handler.handler.orchestration.cmd_start("test")
            mock_sync.assert_called_once()
            called_cmd = mock_run.call_args[0][0]
            self.assertIn("--context", called_cmd)
            self.assertIn("aws-1", called_cmd)

    @patch("ldm_core.ui.UI.die")
    @patch("ldm_core.config.get_active_target")
    def test_cmd_deploy_single_artifact_rejects_remote_target(
        self, mock_target, mock_die
    ):
        """LDM-#1090/#1133: a single-artifact deploy (jar/war/zip) only
        copies into the LOCAL project directory -- silently doing that for
        a project on a remote target would update a copy the running
        remote container never sees. Must fail loudly instead."""
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
        mock_die.side_effect = SystemExit

        with tempfile.TemporaryDirectory() as tmpdir:
            jar_path = Path(tmpdir) / "custom.jar"
            jar_path.write_text("fake jar")

            with (
                patch.object(
                    self.handler.manager,
                    "read_meta",
                    return_value={"target": "aws-1"},
                ),
                patch("ldm_core.utils.atomic_copy") as mock_copy,
            ):
                with self.assertRaises(SystemExit):
                    self.handler.handler.orchestration.cmd_deploy(
                        "test", targets=[str(jar_path)]
                    )

        mock_die.assert_called_once()
        self.assertIn("aws-1", mock_die.call_args[0][0])
        mock_copy.assert_not_called()

    @patch("ldm_core.config.get_active_target")
    def test_cmd_deploy_service_uses_remote_context(self, mock_target):
        """A named-service deploy (already exists wherever the project
        runs, from a prior `ldm run`) is safely redirectable -- unlike a
        single-artifact copy."""
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")

        with (
            patch.object(
                self.handler.manager, "read_meta", return_value={"target": "aws-1"}
            ),
            patch.object(BaseHandler, "run_command") as mock_run,
        ):
            self.handler.handler.orchestration.cmd_deploy("test", targets=["liferay"])

        call_args = mock_run.call_args[0][0]
        self.assertIn("--context", call_args)
        self.assertIn("aws-1", call_args)
        self.assertIn("up", call_args)

    @patch("ldm_core.ui.UI.die")
    def test_cmd_reseed_no_tag_dies(self, mock_die):
        mock_die.side_effect = SystemExit
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(self.handler, "read_meta", return_value={}),
        ):
            with self.assertRaises(SystemExit):
                self.handler.handler.cmd_reseed("test")
            mock_die.assert_called_with("Project missing tag metadata. Cannot reseed.")

    @patch("ldm_core.ui.UI.success")
    @patch("ldm_core.ui.UI.confirm", return_value=True)
    def test_cmd_reseed_success(self, mock_confirm, mock_success):
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "read_meta",
                return_value={"tag": "2026.q1", "db_type": "mysql"},
            ),
            patch.object(self.handler.handler.orchestration, "cmd_reset"),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={
                    "root": self.tmp_dir,
                    "data": self.tmp_dir / "data",
                    "state": self.tmp_dir / "osgi" / "state",
                },
            ),
            patch.object(self.handler.assets, "_fetch_seed", return_value=True),
            patch.object(self.handler, "verify_runtime_environment"),
            patch.object(self.handler.handler.orchestration, "cmd_run"),
        ):
            self.handler.handler.cmd_reseed("test")
            mock_success.assert_called_with("Reseed complete.")

    def test_cmd_reseed_dry_run(self):
        self.handler.dry_run = True
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "read_meta",
                return_value={"tag": "2026.q1", "db_type": "mysql"},
            ),
            patch.object(self.handler.handler.orchestration, "cmd_reset") as mock_reset,
            patch.object(self.handler.assets, "_fetch_seed") as mock_fetch,
        ):
            res = self.handler.handler.cmd_reseed("test")
            self.assertTrue(res)
            self.assertFalse(mock_reset.called)
            self.assertFalse(mock_fetch.called)

    @patch("ldm_core.runtime.orchestration.shutil.rmtree")
    def test_cmd_reset_dry_run(self, mock_rmtree):
        self.handler.dry_run = True
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={"data": self.tmp_dir / "data"},
            ),
            patch.object(self.handler.handler.orchestration, "cmd_down") as mock_down,
        ):
            # Create data folder to simulate existence
            data_dir = self.tmp_dir / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            res = self.handler.handler.orchestration.cmd_reset("test", target="data")
            self.assertTrue(res)
            self.assertFalse(mock_rmtree.called)
            self.assertFalse(mock_down.called)

    @patch("ldm_core.docker_service.get_active_target")
    def test_cmd_reset_remote_target(self, mock_target) -> None:
        """LDM-#1090/#1133: cmd_reset's is_running check and volume ls/rm
        calls must resolve the project's own target, not always the local
        daemon."""
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(name="aws-1", host="34.1.1.1")
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler.manager,
                "read_meta",
                return_value={"target": "aws-1", "container_name": "test-runtime"},
            ),
            patch.object(
                self.handler,
                "setup_paths",
                return_value={"data": self.tmp_dir / "nonexistent"},
            ),
            patch.object(BaseHandler, "run_command") as mock_run,
        ):
            mock_run.return_value = ""
            self.handler.handler.orchestration.cmd_reset("test", target="data")

            calls_with_context = [
                c for c in mock_run.call_args_list if "--context" in c.args[0]
            ]
            self.assertTrue(
                calls_with_context, "No run_command call used --context aws-1"
            )
            for c in calls_with_context:
                self.assertIn("aws-1", c.args[0])

    @patch("ldm_core.ui.UI.error")
    @patch("ldm_core.ui.UI.confirm", return_value=True)
    def test_cmd_reseed_fail(self, mock_confirm, mock_error):
        with (
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(
                self.handler,
                "read_meta",
                return_value={"tag": "2026.q1", "db_type": "mysql"},
            ),
            patch.object(self.handler.handler.orchestration, "cmd_reset"),
            patch.object(self.handler, "setup_paths", return_value={}),
            patch.object(self.handler.assets, "_fetch_seed", return_value=False),
        ):
            self.handler.handler.cmd_reseed("test")
            mock_error.assert_called_with("Reseed failed.")

    @patch("ldm_core.pipelines.run.Pipeline.run", return_value=True)
    def test_cmd_run_invokes_pipeline(self, mock_run):
        with patch.object(
            self.handler, "detect_project_path", return_value=self.tmp_dir
        ):
            result = self.handler.cmd_run("test_proj")
            self.assertTrue(result)
            mock_run.assert_called_once()

    def test_sync_stack_runs_compose(self):
        with (
            # LDM-#1409: on Linux (and so on CI, but never on a macOS dev
            # machine) pipelines/run.py takes the
            # `elif platform.system() == "linux"` branch and calls
            # reclaim_volume_permissions, which runs
            # `docker run --rm -v <path> alpine chown -R ...`. Patching it
            # matches what test_sidecar.py already does.
            patch("ldm_core.utils.reclaim_volume_permissions"),
            patch.object(
                self.handler, "detect_project_path", return_value=self.tmp_dir
            ),
            patch.object(self.handler.config, "sync_common_assets"),
            patch.object(BaseHandler, "get_container_status", return_value="running"),
            patch.object(BaseHandler, "run_command") as mock_run_cmd,
            patch.object(BaseHandler, "check_port", return_value=True),
            patch(
                "ldm_core.pipelines.run.ConfigResolutionStage._resolve_tag",
                return_value=("2024.q1.1", False),
            ),
        ):
            result = self.handler.cmd_run(
                "test",
                no_wait=True,
                paths=self.tmp_dir,
                project_meta={"container_name": "test"},
            )
            self.assertTrue(result)
            self.assertTrue(mock_run_cmd.called)


class TestBatchResilience(unittest.TestCase):
    """LDM-#1343: one unreachable node must not abandon the remaining projects.

    `run_command` defaults to `check=True` and, on failure, calls `sys.exit()`
    rather than raising -- so a single failing project ended the whole batch and
    the projects after it were never attempted, with nothing said about them.
    Observed with `ldm rm --all`: one project removed, the next failed against a
    sleeping compute node, the two after it silently skipped.

    A try/except cannot fix that, so the batch loops pass `check=False` and
    inspect the return, which is `None` only on failure (success returns a
    string, possibly empty).
    """

    def setUp(self):
        self.manager = MockRuntime()
        from ldm_core.runtime.orchestration import OrchestrationService

        self.orch = OrchestrationService(self.manager)
        self.tmp = tempfile.TemporaryDirectory()
        self.roots = []
        for name in ("alpha", "bravo", "charlie"):
            d = Path(self.tmp.name) / name
            d.mkdir()
            (d / "docker-compose.yml").write_text("services: {}\n")
            self.roots.append(d)

    def tearDown(self):
        self.tmp.cleanup()

    def _fail_on(self, failing_name):
        """A run_command stub that mimics the REAL failure semantics.

        This matters: `utils.run_command` does not raise on failure when
        `check=True` -- it calls `sys.exit()`. A stub that merely returned None
        would let the unfixed loop carry on regardless, so the test would pass
        against the bug and only ever exercise the reporting. Honouring `check`
        is what makes it reproduce the abandonment.
        """

        calls = []

        def stub(cmd, *args, **kwargs):
            cwd = str(kwargs.get("cwd", ""))
            calls.append(cwd)
            if failing_name in cwd:
                if kwargs.get("check", True):
                    raise SystemExit(1)  # what check=True really does
                return None  # what check=False returns on failure
            return ""

        return stub, calls

    def _patch_roots(self):
        return patch.object(
            self.manager,
            "find_dxp_roots",
            return_value=[{"path": r} for r in self.roots],
        )

    def test_stop_continues_past_a_failing_project(self):
        stub, calls = self._fail_on("bravo")
        with self._patch_roots(), patch.object(self.manager, "run_command", stub):
            with self.assertRaises(SystemExit) as ctx:
                self.orch.cmd_stop(all_projects=True)

        # charlie MUST have been attempted after bravo failed.
        self.assertTrue(any("charlie" in c for c in calls), calls)
        self.assertEqual(3, len(calls))
        # and the batch still reports failure to automation
        self.assertEqual(1, ctx.exception.code)

    def test_restart_continues_past_a_failing_project(self):
        stub, calls = self._fail_on("alpha")
        with self._patch_roots(), patch.object(self.manager, "run_command", stub):
            with self.assertRaises(SystemExit):
                self.orch.cmd_restart(all_projects=True)

        self.assertEqual(3, len(calls))
        self.assertTrue(any("charlie" in c for c in calls), calls)

    def test_a_fully_successful_batch_does_not_exit(self):
        """Guard against over-reach: no failures means no SystemExit."""
        stub, calls = self._fail_on("nothing-matches-this")
        with self._patch_roots(), patch.object(self.manager, "run_command", stub):
            self.orch.cmd_stop(all_projects=True)
        self.assertEqual(3, len(calls))

    def test_a_single_project_still_fails_fast(self):
        """`check=True` is preserved off the batch path, so behaviour is unchanged."""
        seen = {}

        def stub(cmd, *args, **kwargs):
            seen.update(kwargs)
            return ""

        with (
            patch.object(
                self.manager, "detect_project_path", return_value=self.roots[0]
            ),
            patch.object(self.manager, "run_command", stub),
        ):
            self.orch.cmd_stop(project_id="alpha")

        self.assertTrue(seen.get("check"), "single-project stop must keep check=True")

    def test_the_failure_summary_names_every_failed_project(self):
        from ldm_core.runtime.orchestration import OrchestrationService

        with patch("ldm_core.runtime.orchestration.UI") as mock_ui:
            with self.assertRaises(SystemExit):
                OrchestrationService._report_batch_failures(
                    ["alpha", "charlie"], "stop"
                )

        reported = " ".join(str(c) for c in mock_ui.error.call_args_list)
        self.assertIn("alpha", reported)
        self.assertIn("charlie", reported)
        self.assertIn("stop", reported)

    def test_no_failures_means_no_report_and_no_exit(self):
        from ldm_core.runtime.orchestration import OrchestrationService

        with patch("ldm_core.runtime.orchestration.UI") as mock_ui:
            OrchestrationService._report_batch_failures([], "stop")
        mock_ui.error.assert_not_called()


class TestStopHintIsNotLeakedToInternalCallers(unittest.TestCase):
    """LDM-#1410: `cmd_stop` ends with a terminal next-step hint. The restore
    path calls it mid-flight, so a restore printed

        Next step: Run 'ldm run' to restart the container, ...

    in the middle of its own work. When the following Docker call then blocked,
    the last thing on screen said the command had finished. Three machines sat
    wedged behind that line, one for 84 minutes.
    """

    def _stop(self, **kwargs):
        manager = MagicMock()
        manager.find_dxp_roots.return_value = []
        manager.detect_project_path.return_value = Path(f"{TEST_TMP_ROOT}/proj")
        manager.read_meta.return_value = {"target": None}
        manager.run_command.return_value = ""
        from ldm_core.runtime.orchestration import OrchestrationService

        svc = OrchestrationService(manager)

        hints: list = []
        with (
            patch("ldm_core.ui.UI.hint", side_effect=hints.append),
            patch("ldm_core.ui.UI.detail"),
            patch("ldm_core.ui.UI.warning"),
            patch(
                "ldm_core.docker_service.DockerService.get_compose_cmd_prefix",
                return_value=["docker", "compose"],
            ),
        ):
            svc.cmd_stop("proj", **kwargs)
        return hints

    def test_a_user_facing_stop_still_gets_the_hint(self):
        """The hint is useful when `ldm stop` really is what the user ran."""
        self.assertTrue(self._stop(), "a direct stop should still suggest next steps")

    def test_an_internal_caller_can_suppress_it(self):
        self.assertEqual(
            [], self._stop(emit_hint=False), "a mid-operation stop must stay silent"
        )
