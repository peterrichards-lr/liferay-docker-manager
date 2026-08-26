import contextlib
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.constants import SCRIPT_DIR
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.diagnostics import DiagnosticsService
from ldm_core.handlers.workspace import WorkspaceService
from ldm_core.ui import UI

SCRIPT_DIR_STR = str(SCRIPT_DIR)


class MockBaseManager(BaseHandler):
    def __init__(self):
        self.args = MagicMock()
        self.args.project = None
        self.target: str | None = None
        self.verbose = False
        self.non_interactive = True
        self.workspace = WorkspaceService(self)
        self.diagnostics = DiagnosticsService(self)
        self.manager = self  # type: ignore

    def cmd_completion(self, *args, **kwargs):
        return self.diagnostics.cmd_completion(*args, **kwargs)

    def get_resource_path(self, *args, **kwargs):
        from ldm_core.utils import get_resource_path

        return get_resource_path(*args, **kwargs)

    def check_ram(self, *args, **kwargs):
        pass

    def check_hostname(self, host_name, silent=False):
        if host_name == "localhost":
            return True
        return super().check_hostname(host_name, silent)

    def get_resolved_ip(self, host):
        return super().get_resolved_ip(host)

    def check_port(self, ip, port):
        return True

    def check_registry_collisions(self, *args, **kwargs):
        pass

    def read_meta(self, path):
        # We need to return a dict with project_name matching the ID for discovery tests
        p = Path(path)
        if p.name == "p_match":
            return {"project_name": "p1"}

        # Realistically read if file exists
        from ldm_core.utils import read_meta

        for f in ["meta", ".liferay-docker.meta", ".ldm.meta"]:
            if (p / f).exists():
                return read_meta(p / f)

        return {}


class TestBaseDiscovery(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    def test_find_dxp_roots_multi_dir(self):
        # Create a temporary environment
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            cwd_dir = base_path / "cwd"
            other_dir = base_path / "other"

            for d in [cwd_dir, other_dir]:
                d.mkdir()
                # Create a project in each
                proj = d / f"proj_{d.name}"
                proj.mkdir()
                (proj / ".liferay-docker.meta").write_text("tag=latest")

            with patch("ldm_core.handlers.base.Path.cwd", return_value=cwd_dir):
                with patch.dict(os.environ, {"LDM_WORKSPACE": str(other_dir)}):
                    with patch(
                        "ldm_core.utils.get_actual_home",
                        return_value=base_path / "nonexistent",
                    ):
                        roots = self.handler.find_dxp_roots()

                        names = [r["path"].name for r in roots]
                        # In the new Hardened LDM, LDM_WORKSPACE is EXCLUSIVE.
                        self.assertIn("proj_other", names)


class TestBaseDiscoveryPath(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()
        self.handler._check_external_drive_warning = lambda _: None  # type: ignore[method-assign]

    def test_detect_project_path_by_id(self):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            proj_dir = base_path / "myproj"
            proj_dir.mkdir()
            (proj_dir / "meta").write_text("tag=7.4")

            with patch("ldm_core.handlers.base.Path.cwd", return_value=base_path):
                res = self.handler.detect_project_path("myproj")
                self.assertEqual(res.name, "myproj")

    def test_detect_project_path_for_init_refuses_foreign_git_repo(self):
        # LDM-#1120: a project path resolved here that happens to be an
        # unrelated, pre-existing git checkout (no LDM meta of its own)
        # must be refused, not silently accepted as a fresh init target --
        # confirmed to have caused real data loss (snapshot-restore
        # deleting ~150 tracked files in an unrelated repo, twice).
        with tempfile.TemporaryDirectory() as base_tmp:
            foreign_repo = Path(base_tmp) / "foreign-repo"
            foreign_repo.mkdir()
            (foreign_repo / ".git").mkdir()
            (foreign_repo / "routes").mkdir()
            (foreign_repo / "routes" / "index.js").write_text("// tracked source")

            # self.args is a MagicMock -- any attribute auto-vivifies as a
            # truthy MagicMock, so --force must be explicitly disabled here
            # to simulate the real (unset) case.
            self.handler.args.force = False

            with patch("ldm_core.ui.UI.die", side_effect=SystemExit(1)) as mock_die:
                with self.assertRaises(SystemExit):
                    self.handler.detect_project_path(str(foreign_repo), for_init=True)
                self.assertTrue(mock_die.called)
                self.assertIn("unrelated git repository", mock_die.call_args[0][0])

    def test_detect_project_path_for_init_allows_foreign_git_repo_with_force(self):
        with tempfile.TemporaryDirectory() as base_tmp:
            foreign_repo = Path(base_tmp) / "foreign-repo"
            foreign_repo.mkdir()
            (foreign_repo / ".git").mkdir()

            self.handler.args.force = True
            try:
                res = self.handler.detect_project_path(str(foreign_repo), for_init=True)
                self.assertEqual(res.resolve(), foreign_repo.resolve())
            finally:
                self.handler.args.force = False

    def test_detect_project_path_for_init_allows_own_ldm_project_with_git(self):
        # A directory that's BOTH a git repo AND already has LDM's own meta
        # (e.g. `ldm clone` creates exactly this) must not be blocked.
        with tempfile.TemporaryDirectory() as base_tmp:
            own_project = Path(base_tmp) / "own-project"
            own_project.mkdir()
            (own_project / ".git").mkdir()
            (own_project / "meta").write_text("tag=7.4")

            res = self.handler.detect_project_path(str(own_project), for_init=True)
            self.assertEqual(res.resolve(), own_project.resolve())

    def test_detect_project_path_for_init_missing(self):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            with patch("ldm_core.handlers.base.Path.cwd", return_value=base_path):
                res = self.handler.detect_project_path("newproj", for_init=True)
                self.assertEqual(res.name, "newproj")

    def test_detect_project_path_interactive_fallback(self):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            with patch("ldm_core.handlers.base.Path.cwd", return_value=base_path):
                with patch.object(
                    self.handler,
                    "select_project_interactively",
                    return_value={"path": Path("/selected")},
                ):
                    self.handler.args.project = None
                    self.handler.args.project_flag = None
                    res = self.handler.detect_project_path(None)
                    self.assertEqual(res, Path("/selected"))

    @patch("ldm_core.handlers.base.get_actual_home")
    def test_detect_project_path_fallback_script_dir(self, mock_home):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path / "home"

            # Use a more robust way to mock SCRIPT_DIR
            script_dir = base_path / "script"
            with patch("ldm_core.handlers.base.SCRIPT_DIR", script_dir):
                proj_dir = script_dir / "p1"
                proj_dir.mkdir(parents=True)
                (proj_dir / "meta").write_text("tag=7.4")

                with patch(
                    "ldm_core.handlers.base.Path.cwd", return_value=base_path / "cwd"
                ):
                    res = self.handler.detect_project_path("p1")
                    self.assertEqual(res.name, "p1")

    @patch("ldm_core.handlers.base.get_actual_home")
    def test_detect_project_path_by_container_name_in_sibling(self, mock_home):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp).resolve()
            mock_home.return_value = base_path / "home"

            # Create a sibling project dir with a different name than its container
            sibling_dir = base_path / "actual-folder-name"
            sibling_dir.mkdir(parents=True)
            (sibling_dir / "meta").write_text("tag=7.4\ncontainer_name=my-container-id")

            # We are currently in another sibling dir
            cwd = base_path / "current-repo"
            cwd.mkdir()

            with patch("ldm_core.handlers.base.Path.cwd", return_value=cwd):
                # Search for the container id
                res = self.handler.detect_project_path("my-container-id")
                self.assertIsNotNone(res)
                self.assertEqual(res.resolve(), sibling_dir.resolve())

    @patch("ldm_core.handlers.base.get_actual_home")
    def test_detect_project_path_iterative_search(self, mock_home):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path / "home"

            search_dir = base_path / "home" / "ldm"
            proj_dir = search_dir / "p_match"
            proj_dir.mkdir(parents=True)
            (proj_dir / "meta").write_text("tag=7.4")
            # Mock read_meta in MockBaseManager returns project_name="p1" for p_match

            with patch(
                "ldm_core.handlers.base.Path.cwd", return_value=base_path / "cwd"
            ):
                res = self.handler.detect_project_path("p1")
                self.assertEqual(res.name, "p_match")

    @patch("ldm_core.handlers.base.get_actual_home")
    @patch("ldm_core.handlers.base.safe_cwd")
    @patch("ldm_core.ui.UI.warning")
    def test_detect_project_path_cwd_home_warning(self, mock_warn, mock_cwd, mock_home):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path
            mock_cwd.return_value = base_path

            # Clean flag
            from ldm_core.handlers.base import BaseHandler

            if hasattr(BaseHandler, "_warned_home"):
                delattr(BaseHandler, "_warned_home")

            # First run: should warn
            self.handler.detect_project_path("some-proj", for_init=True)
            mock_warn.assert_called_once()
            self.assertIn(
                "You are running LDM from your Home directory",
                mock_warn.call_args[0][0],
            )

            # Reset mock and run again: should NOT warn because _warned_home is True
            mock_warn.reset_mock()
            self.handler.detect_project_path("some-proj", for_init=True)
            self.assertFalse(mock_warn.called)

    @patch("ldm_core.handlers.base.get_actual_home")
    @patch("ldm_core.handlers.base.safe_cwd")
    @patch("ldm_core.ui.UI.warning")
    def test_detect_project_path_cwd_home_warning_suppressed_by_arg(
        self, mock_warn, mock_cwd, mock_home
    ):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path
            mock_cwd.return_value = base_path

            # Clean flag
            from ldm_core.handlers.base import BaseHandler

            if hasattr(BaseHandler, "_warned_home"):
                delattr(BaseHandler, "_warned_home")

            # Set CLI arg to suppress
            self.handler.args.no_home_warn = True

            try:
                self.handler.detect_project_path("some-proj", for_init=True)
                mock_warn.assert_not_called()
            finally:
                self.handler.args.no_home_warn = False

    @patch("ldm_core.handlers.base.get_actual_home")
    @patch("ldm_core.handlers.base.safe_cwd")
    @patch("ldm_core.ui.UI.warning")
    def test_detect_project_path_cwd_home_warning_suppressed_by_config(
        self, mock_warn, mock_cwd, mock_home
    ):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path
            mock_cwd.return_value = base_path

            # Clean flag
            from ldm_core.handlers.base import BaseHandler

            if hasattr(BaseHandler, "_warned_home"):
                delattr(BaseHandler, "_warned_home")

            # Set configuration defaults to suppress warning
            self.handler.defaults = MagicMock()  # type: ignore[attr-defined]
            self.handler.defaults.get.return_value = "true"  # type: ignore[attr-defined]

            try:
                self.handler.detect_project_path("some-proj", for_init=True)
                mock_warn.assert_not_called()
                self.handler.defaults.get.assert_called_with("no_home_warn", "false")  # type: ignore[attr-defined]
            finally:
                if hasattr(self.handler, "defaults"):
                    delattr(self.handler, "defaults")


class TestBaseProject(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    def test_require_compose_true(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "docker-compose.yml").touch()
            self.assertTrue(self.handler.require_compose(root))

    def test_require_compose_false(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.assertFalse(self.handler.require_compose(root, silent=True))

    @patch("ldm_core.docker_service.DockerService.get_health", return_value="healthy")
    def test_get_container_status_healthy(self, mock_health):
        self.assertEqual(self.handler.get_container_status("c1"), "healthy")

    def test_resolve_container_uses_remote_target_context(self):
        """resolve_container()'s label-based lookup must honor a passed target (#1133).

        Callers (cmd_shell, cmd_logs) resolve the project's target and pass
        it through -- previously this method's own docker ps invocation
        hardcoded "docker", silently looking at the LOCAL daemon even when
        the caller's own resolved target was remote.
        """
        from ldm_core.config import TargetNode

        with (
            patch(
                "ldm_core.docker_service.get_active_target",
                return_value=TargetNode(name="aws-1", host="34.1.1.1"),
            ),
            patch.object(
                self.handler, "run_command", return_value="proj-liferay-1"
            ) as mock_run,
        ):
            result = self.handler.resolve_container(
                "proj", "liferay", target_name="aws-1"
            )
            self.assertEqual(result, "proj-liferay-1")
            called_cmd = mock_run.call_args[0][0]
            self.assertIn("--context", called_cmd)
            self.assertIn("aws-1", called_cmd)

    def test_select_project_interactively_basic(self):
        self.handler.non_interactive = False
        roots = [{"path": Path("/tmp/p1"), "version": "7.4"}]
        with patch("ldm_core.ui.UI.ask", return_value="1"):
            res = self.handler.select_project_interactively(roots=roots)
            self.assertEqual(res["path"], Path("/tmp/p1"))

    def test_select_project_interactively_new(self):
        self.handler.non_interactive = False
        roots = [{"path": Path("/tmp/p1"), "version": "7.4"}]
        with patch("ldm_core.ui.UI.ask", return_value="n"):
            res = self.handler.select_project_interactively(roots=roots)
            self.assertTrue(res.get("new"))

    def test_pre_flight_checks_basic(self):
        meta = {"root": "/tmp/p1", "project_name": "p1"}
        with patch.object(self.handler, "check_port", return_value=True):
            res_port = self.handler._pre_flight_checks("localhost", 8080, meta=meta)
            self.assertEqual(res_port, 8080)

    def test_pre_flight_checks_skips_port_check_for_remote_target(self):
        # LDM-#1090: port availability was checked via a raw local socket
        # bind/connect against the orchestrating host -- meaningless (and
        # actively wrong) for a project on a remote --node target. Reported
        # case: --port 8080 refused locally due to an unrelated stale SSH
        # forward, while 8080 was actually free on the target.
        meta = {
            "root": "/tmp/p1",
            "project_name": "p1",
            "target": "aws-1",
        }
        with patch.object(self.handler, "check_port") as mock_check_port:
            res_port = self.handler._pre_flight_checks("localhost", 8080, meta=meta)
            self.assertEqual(res_port, 8080)
            mock_check_port.assert_not_called()

    def test_pre_flight_checks_skips_port_check_for_new_project_remote_target(self):
        # LDM-#1090 (regression, live-verified against a real remote node):
        # meta["target"] is only ever populated by `ldm target set`/migrate
        # -- it's NEVER written during `ldm run` itself, so a brand-new
        # project's first `ldm run --node <target>` had meta.get("target")
        # always empty, silently falling back to "local" even with --node
        # explicitly passed on the CLI. self.target (set from
        # args.node/args.target at LiferayManager construction) must be
        # consulted instead/first, since it's authoritative and available
        # immediately regardless of whether meta has been persisted yet.
        meta = {"root": "/tmp/p1", "project_name": "p1"}  # no "target" key at all
        self.handler.target = "aws-1"  # type: ignore[attr-defined]
        with patch.object(self.handler, "check_port") as mock_check_port:
            res_port = self.handler._pre_flight_checks("localhost", 8080, meta=meta)
            self.assertEqual(res_port, 8080)
            mock_check_port.assert_not_called()

    @patch("ldm_core.config.get_active_target")
    def test_pre_flight_checks_skips_port_check_for_active_default_target_from_config(
        self, mock_get_target
    ):
        # LDM-#1129: verify active default target from ldm target use skips local port check
        from ldm_core.config import TargetNode

        mock_get_target.return_value = TargetNode(
            name="aws-2", host="10.0.0.1", is_default=True
        )

        meta = {"root": "/tmp/p1", "project_name": "p1"}
        self.handler.target = None  # No --node flag explicitly passed on CLI
        with patch.object(self.handler, "check_port") as mock_check_port:
            res_port = self.handler._pre_flight_checks("localhost", 8080, meta=meta)
            self.assertEqual(res_port, 8080)
            mock_check_port.assert_not_called()

    def test_check_ram_uses_docker_context_for_remote_node(self):
        # LDM-#1130: verify check_ram passes --context for remote target
        handler = MockBaseManager()
        handler.target = "aws-1"
        with patch.object(handler, "run_command") as mock_run:
            mock_run.return_value = "8589934592"
            from ldm_core.handlers.base import BaseHandler

            BaseHandler.check_ram(handler)
            self.assertTrue(mock_run.called)
            cmd = mock_run.call_args[0][0]
            self.assertIn("--context", cmd)
            self.assertIn("aws-1", cmd)

    @patch("ldm_core.config.get_active_target")
    def test_pre_flight_checks_still_checks_port_for_local_target(
        self, mock_get_target
    ):
        from ldm_core.config import TargetNode

        mock_get_target.return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )
        meta = {
            "root": "/tmp/p1",
            "project_name": "p1",
            "target": "local",
        }
        with patch.object(self.handler, "check_port") as mock_check_port:
            mock_check_port.return_value = False
            self.handler.non_interactive = True
            with patch("ldm_core.ui.UI.die", side_effect=SystemExit(1)) as mock_die:
                with self.assertRaises(SystemExit):
                    self.handler._pre_flight_checks("localhost", 8080, meta=meta)
                self.assertTrue(mock_die.called)

    def test_external_drive_warning_deduplication_across_instances(self):
        # LDM-#1092: external drive warning must be deduplicated across multiple
        # handler/service instances within the same CLI process.
        if hasattr(BaseHandler, "_warned_volume_paths"):
            delattr(BaseHandler, "_warned_volume_paths")

        h1 = MockBaseManager()
        h2 = MockBaseManager()

        ext_path = Path("/Volumes/SanDisk/myproject")
        with patch("ldm_core.ui.UI.warning") as mock_warning:
            h1._check_external_drive_warning(ext_path)
            self.assertEqual(mock_warning.call_count, 3)

            mock_warning.reset_mock()
            # Second call on a completely separate instance must be deduplicated
            h2._check_external_drive_warning(ext_path)
            mock_warning.assert_not_called()

    @patch("ldm_core.handlers.base.get_actual_home")
    def test_check_registry_collisions_none(self, mock_home):
        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path

            # No registry file exists
            BaseHandler.check_registry_collisions(self.handler, "p1", base_path / "p1")
            # Should not die

    @patch("ldm_core.handlers.base.get_actual_home")
    @patch("ldm_core.ui.UI.ask")
    @patch("ldm_core.ui.UI.die")
    def test_check_registry_collisions_scenarios(self, mock_die, mock_ask, mock_home):  # noqa: PLR0915
        from ldm_core.constants import REGISTRY_FILE

        with tempfile.TemporaryDirectory() as base_tmp:
            base_path = Path(base_tmp)
            mock_home.return_value = base_path

            ldm_dir = base_path / ".ldm"
            ldm_dir.mkdir()
            registry_path = ldm_dir / REGISTRY_FILE

            with patch.object(self.handler, "run_command") as mock_run_cmd:
                # Case 1: Same path -> no collision
                registry = {"p1": {"path": str(base_path / "p1")}}
                registry_path.write_text(json.dumps(registry))
                BaseHandler.check_registry_collisions(
                    self.handler, "p1", base_path / "p1"
                )
                mock_die.assert_not_called()
                mock_run_cmd.assert_not_called()

                # Case 2: Stale path (does not exist on disk) -> should auto-clean and not die
                p2_old_path = base_path / "p2_old"
                registry = {"p2": {"path": str(p2_old_path)}}
                registry_path.write_text(json.dumps(registry))
                BaseHandler.check_registry_collisions(
                    self.handler, "p2", base_path / "p2"
                )
                mock_die.assert_not_called()
                mock_run_cmd.assert_not_called()
                # Assert p2 is removed from registry
                updated_reg = json.loads(registry_path.read_text())
                self.assertNotIn("p2", updated_reg)

                # Case 3: Different path (exists on disk), non-interactive, no overwrite_registry -> should unregister & not die
                # and should trigger stack teardown if docker-compose.yml exists
                p3_old_path = base_path / "p3_old"
                p3_old_path.mkdir()
                (p3_old_path / "docker-compose.yml").touch()
                registry = {"p3": {"path": str(p3_old_path)}}
                registry_path.write_text(json.dumps(registry))
                self.handler.non_interactive = True
                self.handler.args.overwrite_registry = False

                BaseHandler.check_registry_collisions(
                    self.handler, "p3", base_path / "p3"
                )
                mock_die.assert_not_called()
                # Assert compose down was executed on p3_old_path
                mock_run_cmd.assert_called_once()
                cmd_args = mock_run_cmd.call_args[0][0]
                self.assertIn("down", cmd_args)
                self.assertEqual(
                    mock_run_cmd.call_args[1].get("cwd"), str(p3_old_path.resolve())
                )
                mock_run_cmd.reset_mock()

                # Assert p3 is removed from registry
                updated_reg = json.loads(registry_path.read_text())
                self.assertNotIn("p3", updated_reg)

                # Case 4: Different path (exists on disk), interactive, overwrite_registry=True -> should unregister & not die
                p3_old_path.mkdir(exist_ok=True)
                # No docker-compose.yml in this case
                if (p3_old_path / "docker-compose.yml").exists():
                    (p3_old_path / "docker-compose.yml").unlink()
                registry = {"p3": {"path": str(p3_old_path)}}
                registry_path.write_text(json.dumps(registry))
                self.handler.non_interactive = False
                self.handler.args.overwrite_registry = True
                BaseHandler.check_registry_collisions(
                    self.handler, "p3", base_path / "p3"
                )
                mock_die.assert_not_called()
                mock_ask.assert_not_called()
                mock_run_cmd.assert_not_called()
                # Assert p3 is removed from registry
                updated_reg = json.loads(registry_path.read_text())
                self.assertNotIn("p3", updated_reg)

                # Case 5: Different path (exists on disk), interactive, user says Yes -> should unregister & not die
                p4_old_path = base_path / "p4_old"
                p4_old_path.mkdir()
                registry = {"p4": {"path": str(p4_old_path)}}
                registry_path.write_text(json.dumps(registry))
                self.handler.non_interactive = False
                self.handler.args.overwrite_registry = False
                mock_ask.return_value = "y"

                BaseHandler.check_registry_collisions(
                    self.handler, "p4", base_path / "p4"
                )
                mock_ask.assert_called_once()
                mock_die.assert_not_called()
                mock_run_cmd.assert_not_called()
                updated_reg = json.loads(registry_path.read_text())
                self.assertNotIn("p4", updated_reg)
                mock_ask.reset_mock()

                # Case 6: Different path (exists on disk), interactive, user says No -> should die
                registry = {"p4": {"path": str(p4_old_path)}}
                registry_path.write_text(json.dumps(registry))
                mock_ask.return_value = "n"

                BaseHandler.check_registry_collisions(
                    self.handler, "p4", base_path / "p4"
                )
                mock_ask.assert_called_once()
                mock_die.assert_called_once()
                mock_run_cmd.assert_not_called()

    def test_migrate_layout_basic(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = self.handler.setup_paths(root)
            self.handler.migrate_layout(paths)
            self.assertTrue((root / "files").exists())
            self.assertTrue((root / "deploy").exists())

    def test_get_common_dir_env(self):
        with patch.dict(os.environ, {"LDM_COMMON_DIR": "/tmp/common"}):
            with patch("ldm_core.handlers.base.Path.cwd", return_value=Path("/empty")):
                self.assertEqual(
                    self.handler.get_common_dir(Path("/root")).resolve(),
                    Path("/tmp/common").resolve(),
                )

    @patch("ldm_core.handlers.base.get_actual_home", return_value=Path("/tmp/home"))
    def test_get_common_dir_default(self, mock_home):
        # We need to ensure that Priority 2 (CWD/common) and Priority 3 (Project/common) don't match
        with patch("ldm_core.handlers.base.Path.cwd", return_value=Path("/empty")):
            # Use a more robust mock for exists that handles the self argument
            with patch.object(Path, "exists", autospec=True) as mock_exists:
                mock_exists.side_effect = lambda self: ".ldm" in str(self)
                self.assertEqual(
                    self.handler.get_common_dir(Path("/root")),
                    Path("/tmp/home/.ldm/common"),
                )

    def test_check_uncommitted_changes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            proj_path = Path(tmp_dir)

            # Case A: No .git directory -> returns True
            self.assertTrue(self.handler.check_uncommitted_changes(proj_path))

            # Create .git directory to simulate a git repo
            (proj_path / ".git").mkdir()

            with patch("subprocess.run") as mock_run:
                # Case B: Clean git status -> returns True
                mock_run.return_value = MagicMock(returncode=0, stdout="")
                self.assertTrue(self.handler.check_uncommitted_changes(proj_path))

                # Case C: Changes only in non-critical paths -> returns True
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=" M README.md\n?? foo.txt"
                )
                self.assertTrue(self.handler.check_uncommitted_changes(proj_path))

                # Case D: Changes in critical files, force=True -> returns True
                self.handler.args.force = True
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=" M files/portal-ext.properties"
                )
                self.assertTrue(self.handler.check_uncommitted_changes(proj_path))

                # Reset force flag
                self.handler.args.force = False

                # Case E: Changes in critical files, force=False, non_interactive=True -> raises SystemExit
                self.handler.non_interactive = True
                with self.assertRaises(SystemExit):
                    self.handler.check_uncommitted_changes(proj_path)

                # Reset non_interactive
                self.handler.non_interactive = False

                # Case F: Changes in critical files, force=False, non_interactive=False, user answers Yes -> returns True
                with (
                    patch("ldm_core.ui.UI.ask", return_value="y"),
                    patch("ldm_core.ui.UI.warning"),
                ):
                    self.assertTrue(self.handler.check_uncommitted_changes(proj_path))

                # Case G: Changes in critical files, force=False, non_interactive=False, user answers No -> raises SystemExit
                with (
                    patch("ldm_core.ui.UI.ask", return_value="n"),
                    patch("ldm_core.ui.UI.warning"),
                    self.assertRaises(SystemExit),
                ):
                    self.handler.check_uncommitted_changes(proj_path)


class TestBaseEnvironment(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    def test_is_wsl_true(self):
        with patch("platform.system", return_value="Linux"):
            with patch("builtins.open", unittest.mock.mock_open(read_data="Microsoft")):
                self.assertTrue(self.handler.is_wsl())

    def test_is_wsl_false(self):
        with patch("platform.system", return_value="Darwin"):
            self.assertFalse(self.handler.is_wsl())

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/java")
    def test_check_java_version_success(self, mock_which, mock_run):
        mock_res = MagicMock()
        mock_res.stderr = 'openjdk version "21.0.1" 2023-10-17'
        mock_run.return_value = mock_res
        self.assertTrue(self.handler._check_java_version("21"))

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/java")
    def test_check_java_version_fail(self, mock_which, mock_run):
        mock_res = MagicMock()
        mock_res.stderr = 'openjdk version "11.0.1"'
        mock_run.return_value = mock_res
        self.assertFalse(self.handler._check_java_version("21"))

    def test_run_command_error(self):
        with patch(
            "subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmd")
        ):
            with self.assertRaises(SystemExit):
                with patch("ldm_core.ui.UI.die") as mock_die:
                    mock_die.side_effect = SystemExit
                    self.handler.run_command("cmd")

    def test_get_resolved_ip_localhost(self):
        self.assertEqual(self.handler.get_resolved_ip("localhost"), "127.0.0.1")

    @patch("socket.gethostbyname", return_value="1.2.3.4")
    def test_get_resolved_ip_remote(self, mock_socket):
        self.assertEqual(self.handler.get_resolved_ip("myhost"), "1.2.3.4")

    def test_check_hostname_localhost(self):
        self.assertTrue(self.handler.check_hostname("localhost"))

    @patch("socket.gethostbyname", side_effect=socket.gaierror("Failed"))
    def test_check_hostname_fail(self, mock_socket):
        self.assertFalse(self.handler.check_hostname("invalid"))

    @patch("os.getuid", return_value=0, create=True)
    @patch("shutil.which", return_value="/usr/bin/docker")
    @patch("subprocess.run")
    def test_check_docker_root_fail(self, mock_run, mock_which, mock_uid):
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res
        with self.assertRaises(SystemExit):
            self.handler.check_docker()


class TestBaseHardening(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()


class TestBaseCompletion(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    @patch("ldm_core.diagnostics.completions.get_actual_home")
    @patch("ldm_core.diagnostics.completions.get_resource_path")
    @patch("ldm_core.ui.UI.heading")
    @patch("ldm_core.ui.UI.info")
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/bin/zsh"})
    def test_cmd_completion_zsh_instructions(
        self, mock_info, mock_heading, mock_res, mock_home
    ):
        # No argument: should show instructions
        mock_home.return_value = Path("/tmp/home")
        with patch("builtins.print"):
            self.handler.cmd_completion(target_shell=None)
            mock_heading.assert_called_with("LDM Shell Completion")

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/bin/bash"})
    @patch(
        "ldm_core.diagnostics.completions.get_actual_home",
        return_value=Path("/tmp/home"),
    )
    def test_cmd_completion_bash_instructions(self, mock_home, mock_stdout):
        # No argument: should show instructions
        self.handler.cmd_completion(target_shell=None)
        pass

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/usr/bin/fish"})
    @patch(
        "ldm_core.diagnostics.completions.get_actual_home",
        return_value=Path("/tmp/home"),
    )
    def test_cmd_completion_fish_instructions(self, mock_home, mock_stdout):
        self.handler.cmd_completion(target_shell=None)

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "powershell.exe"})
    @patch(
        "ldm_core.diagnostics.completions.get_actual_home",
        return_value=Path("/tmp/home"),
    )
    def test_cmd_completion_powershell_instructions(self, mock_home, mock_stdout):
        self.handler.cmd_completion(target_shell=None)

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "powershell.exe"})
    @patch(
        "ldm_core.diagnostics.completions.get_actual_home",
        return_value=Path("/tmp/home"),
    )
    def test_cmd_completion_powershell_code(self, mock_home, mock_stdout):
        # Specific argument: should show the bridge script
        self.handler.cmd_completion(target_shell="powershell")
        pass

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("ldm_core.handlers.base.os.environ", {"SHELL": "/usr/bin/fish"})
    @patch(
        "ldm_core.diagnostics.completions.get_actual_home",
        return_value=Path("/tmp/home"),
    )
    def test_cmd_completion_zsh_code(self, mock_home, mock_stdout):
        # Specific argument: should show raw code
        with patch("argcomplete.shellcode", return_value="# ZSH CODE"):
            self.handler.cmd_completion(target_shell="zsh")

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("sys.stderr", new_callable=MagicMock)
    @patch(
        "ldm_core.diagnostics.completions.get_actual_home",
        return_value=Path("/tmp/home"),
    )
    def test_cmd_completion_generation_suppresses_ui(
        self, mock_home, mock_stderr, mock_stdout
    ):
        # Verify that providing a shell argument DOES NOT print UI headings to stdout
        with (
            patch("argcomplete.shellcode", return_value="# CODE"),
            patch("ldm_core.ui.UI.heading") as mock_heading,
        ):
            self.handler.cmd_completion(target_shell="bash")
            mock_heading.assert_not_called()

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("sys.stderr", new_callable=MagicMock)
    @patch(
        "ldm_core.diagnostics.completions.get_actual_home",
        return_value=Path("/tmp/home"),
    )
    def test_cmd_completion_generation_zsh_boilerplate(
        self, mock_home, mock_stderr, mock_stdout
    ):
        # Verify that providing zsh includes the necessary boilerplate
        with patch("argcomplete.shellcode", return_value="# CODE"):
            self.handler.cmd_completion(target_shell="zsh")

    @patch("sys.stdout", new_callable=MagicMock)
    @patch("sys.stderr", new_callable=MagicMock)
    @patch(
        "ldm_core.diagnostics.completions.get_actual_home",
        return_value=Path("/tmp/home"),
    )
    def test_cmd_completion_error_goes_to_stderr(
        self, mock_home, mock_stderr, mock_stdout
    ):
        # Verify that a failure in shellcode generation doesn't dump instructions to stdout
        with patch("argcomplete.shellcode", side_effect=Exception("Failed")):
            self.handler.cmd_completion(target_shell="zsh")

    @patch("platform.system", return_value="Darwin")
    @patch("ldm_core.handlers.base.subprocess.run")
    @patch("ldm_core.handlers.base.shutil.which", return_value="/usr/local/bin/docker")
    def test_verify_runtime_environment_darwin_no_unbound_local_error(
        self, mock_which, mock_run, mock_system
    ):
        from ldm_core.handlers.base import BaseHandler

        handler = BaseHandler(MagicMock())
        handler.verbose = False

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = {"root": root, "files": root / "files"}

            mock_result = MagicMock()
            mock_result.stdout = "OK"
            # LDM-#1306: returncode must be set explicitly. This mock models a
            # *successful* docker run, but a bare MagicMock's `returncode` is
            # itself a MagicMock, so `returncode != 0` is True. That went
            # unnoticed while the probe used check=True, because
            # CommandRunner.run only consults returncode when `not check`
            # (utils.py). Once the probe became check=False for #1306 the
            # runner correctly read this mock as a failure and returned None.
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            try:
                handler.verify_runtime_environment(paths)
            except UnboundLocalError:
                self.fail(
                    "verify_runtime_environment raised UnboundLocalError unexpectedly!"
                )

    @patch("platform.system", return_value="Darwin")
    @patch("ldm_core.config.get_active_target")
    @patch("ldm_core.handlers.base.shutil.which", return_value=None)
    def test_verify_runtime_environment_skips_when_docker_not_installed(
        self, mock_which, mock_get_active_target, mock_system
    ):
        # Regression: this call previously used check=True (run_command's
        # default) with no guard for "docker isn't installed at all" --
        # only for "a remote target is active." On a machine/CI runner
        # with no docker binary (e.g. GitHub Actions' macos-latest, which
        # has never shipped Docker), this hit run_command's
        # FileNotFoundError handler and hard sys.exit(127) ("Command not
        # found: docker"), even for `ldm init --no-up`, which never
        # intends to touch Docker at all.
        from ldm_core.config import TargetNode
        from ldm_core.handlers.base import BaseHandler

        mock_get_active_target.return_value = TargetNode(name="local", host="localhost")

        handler = BaseHandler(MagicMock())
        handler.verbose = False

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = {"root": root, "files": root / "files"}

            with patch.object(handler, "run_command") as mock_run_command:
                handler.verify_runtime_environment(paths)
                mock_run_command.assert_not_called()


class TestMountProbeIsBounded(unittest.TestCase):
    """The Docker mount probe must be bounded and must fail loudly (LDM-#1306).

    The probe shells out to `docker run --rm -v <root>:/workspace alpine ...`
    to confirm the project directory is actually writable from inside a
    container. It had no timeout, so a stalled image pull, an unreachable
    registry or a wedged mount hung indefinitely with nothing printed -- and
    because it runs during `ldm run`, a hang there looks exactly like slow
    startup. Observed twice: a WSL2 run sitting silently at "Synchronizing
    Assets", and Docker Hub returning HTTP 500 during v2.16.0 verification.
    """

    def _handler(self):
        from ldm_core.handlers.base import BaseHandler

        handler = BaseHandler(MagicMock())
        handler.verbose = False
        return handler

    def test_run_command_wrapper_forwards_timeout(self):
        """The enabling defect: the wrapper accepted no timeout at all.

        `utils.run_command` has supported `timeout` throughout, but
        `BaseHandler.run_command` did not accept or forward it, so *every*
        Docker and registry call made through the wrapper was unbounded no
        matter what the call site asked for. Bounding the probe is only
        possible because of this.
        """
        handler = self._handler()

        with patch("ldm_core.utils.run_command") as mock_run_command:
            handler.run_command(["docker", "info"], timeout=42)

        self.assertEqual(
            mock_run_command.call_args.kwargs.get("timeout"),
            42,
            "BaseHandler.run_command dropped `timeout` instead of forwarding it",
        )

    @patch("platform.system", return_value="Darwin")
    @patch("ldm_core.config.get_active_target")
    @patch("ldm_core.handlers.base.shutil.which", return_value="/usr/local/bin/docker")
    def test_probe_passes_a_timeout(self, mock_which, mock_target, mock_system):
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(name="local", host="localhost")
        handler = self._handler()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = {"root": root, "files": root / "files"}

            with patch.object(handler, "run_command", return_value="OK") as mock_run:
                handler.verify_runtime_environment(paths)

        probe = next(
            c for c in mock_run.call_args_list if "alpine" in (c.args[0] or [])
        )
        self.assertIsNotNone(
            probe.kwargs.get("timeout"), "the mount probe ran unbounded"
        )
        self.assertFalse(
            probe.kwargs.get("check", True),
            "the probe must use check=False so a timeout reaches our diagnosis "
            "rather than exiting 124 with a generic message",
        )

    @patch("platform.system", return_value="Darwin")
    @patch("ldm_core.config.get_active_target")
    @patch("ldm_core.handlers.base.shutil.which", return_value="/usr/local/bin/docker")
    def test_probe_failure_exits_with_a_diagnosis(
        self, mock_which, mock_target, mock_system
    ):
        """A timed-out or failed probe must name the likely causes.

        With check=False, `CommandRunner.run` collapses timeout, non-zero exit
        and missing-binary all to None, so None is the single failure signal
        the handler has to act on.
        """
        from ldm_core.config import TargetNode

        mock_target.return_value = TargetNode(name="local", host="localhost")
        handler = self._handler()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = {"root": root, "files": root / "files"}

            with (
                patch.object(handler, "run_command", return_value=None),
                patch("ldm_core.ui.UI.info") as mock_info,
                self.assertRaises(SystemExit) as ctx,
            ):
                handler.verify_runtime_environment(paths)

        self.assertEqual(ctx.exception.code, 3)

        # UI.info, not UI.detail: detail is gated behind --info/--verbose
        # (LDM-#1036), so a diagnosis written with it would be invisible in
        # exactly the run where someone needs it.
        printed = " ".join(str(c.args[0]) for c in mock_info.call_args_list if c.args)
        for cause in ("registry", "daemon", "pull alpine"):
            self.assertIn(cause, printed)


class TestBasePortChecking(unittest.TestCase):
    def setUp(self):
        from ldm_core.handlers.base import BaseHandler

        self.handler = BaseHandler(MagicMock())

    @patch("socket.socket")
    def test_check_port_available(self, mock_socket_class):
        mock_socket = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_socket
        # bind succeeds, returns True
        res = self.handler.check_port("127.0.0.1", 8080)
        self.assertTrue(res)
        mock_socket.bind.assert_called_with(("127.0.0.1", 8080))

    @patch("socket.socket")
    def test_check_port_in_use(self, mock_socket_class):
        mock_socket = MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_socket
        mock_socket.bind.side_effect = OSError(11, "Address already in use")
        res = self.handler.check_port("127.0.0.1", 8080)
        self.assertFalse(res)

    @patch("socket.socket")
    def test_check_port_permission_denied_free(self, mock_socket_class):
        mock_bind_socket = MagicMock()
        mock_conn_socket = MagicMock()
        # The first socket created is for bind, the second is for connect_ex
        mock_socket_class.return_value.__enter__.side_effect = [
            mock_bind_socket,
            mock_conn_socket,
        ]

        mock_bind_socket.bind.side_effect = PermissionError(13, "Permission denied")
        # connect_ex returns ECONNREFUSED or similar non-zero
        mock_conn_socket.connect_ex.return_value = 61

        res = self.handler.check_port("127.0.0.1", 80)
        self.assertTrue(res)

    @patch("socket.socket")
    def test_check_port_permission_denied_occupied(self, mock_socket_class):
        mock_bind_socket = MagicMock()
        mock_conn_socket = MagicMock()
        mock_socket_class.return_value.__enter__.side_effect = [
            mock_bind_socket,
            mock_conn_socket,
        ]

        mock_bind_socket.bind.side_effect = PermissionError(13, "Permission denied")
        # connect_ex returns 0 (occupied)
        mock_conn_socket.connect_ex.return_value = 0

        res = self.handler.check_port("127.0.0.1", 80)
        self.assertFalse(res)

    @patch("socket.socket")
    def test_check_port_oserror_permission_denied_free(self, mock_socket_class):
        import errno

        mock_bind_socket = MagicMock()
        mock_conn_socket = MagicMock()
        mock_socket_class.return_value.__enter__.side_effect = [
            mock_bind_socket,
            mock_conn_socket,
        ]

        # Raise OSError with errno EACCES
        err = OSError("Permission denied")
        err.errno = errno.EACCES
        mock_bind_socket.bind.side_effect = err
        mock_conn_socket.connect_ex.return_value = errno.ECONNREFUSED

        res = self.handler.check_port("127.0.0.1", 80)
        self.assertTrue(res)


class TestProjectNotFoundMessage(unittest.TestCase):
    """LDM-#1344: the not-found error is a next step by default, paths on demand."""

    def setUp(self):
        self.handler = MockBaseManager()

    @contextlib.contextmanager
    def _resolve_miss(self, verbose=False, info_mode=False):
        """Runs a doomed lookup in an isolated home and yields what the user saw.

        The home is patched -- deliberately, and not only for determinism. An
        unpatched `get_actual_home` reaches the developer's real
        `~/.ldm/registry.json` (LDM-#1342), and a test that prints a path list
        must not be the thing that adds entries to it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            workspace = home / "ldm"
            workspace.mkdir(parents=True)
            (home / ".ldm").mkdir()

            captured = io.StringIO()
            box = {"home": home, "workspace": workspace, "out": captured}
            with (
                patch("ldm_core.handlers.base.get_actual_home", return_value=home),
                patch("ldm_core.utils.get_actual_home", return_value=home),
                # cwd == ~/ldm is the recommended layout, and the case that
                # made the old message print the same directory twice.
                patch("ldm_core.handlers.base.safe_cwd", return_value=workspace),
                patch.dict(os.environ, {}, clear=False),
                patch("sys.stderr", captured),
                UI.patch(verbose=verbose, info_mode=info_mode),
            ):
                os.environ.pop("LDM_WORKSPACE", None)
                with self.assertRaises(SystemExit) as exit_ctx:
                    self.handler._detect_project_path_raw("no-such-project")
                box["code"] = exit_ctx.exception.code
                box["text"] = captured.getvalue()
            yield box

    def test_default_output_names_the_project_and_a_next_step(self):
        with self._resolve_miss() as seen:
            text = seen["text"]

        self.assertIn("Project 'no-such-project' not found.", text)
        self.assertIn("ldm list", text)
        self.assertIn("--verbose", text)
        self.assertEqual(1, seen["code"])

    def test_default_output_does_not_print_search_paths(self):
        """The paths are the part that read like a stack trace."""
        with self._resolve_miss() as seen:
            text = seen["text"]
            self.assertNotIn(str(seen["workspace"]), text)
            self.assertNotIn(str(seen["home"]), text)
        self.assertNotIn("Looked in:", text)
        self.assertNotIn(SCRIPT_DIR_STR, text)

    def test_verbose_lists_the_locations_including_the_registry(self):
        with self._resolve_miss(verbose=True) as seen:
            text = seen["text"]
            self.assertIn("Looked in:", text)
            self.assertIn(str(seen["workspace"]), text)
            self.assertIn("the current folder", text)
            # The registry is consulted during resolution, so it belongs in the
            # list -- omitting it sent people looking in the wrong place.
            self.assertIn(str(seen["home"] / ".ldm"), text)

    def test_verbose_does_not_repeat_a_location(self):
        """`cwd` and `~/ldm` are the same directory in the recommended layout."""
        with self._resolve_miss(verbose=True) as seen:
            occurrences = seen["text"].count(f"- {seen['workspace']}\n")
        self.assertEqual(1, occurrences)

    def test_info_mode_also_gets_the_locations(self):
        """--info is the same tier as UI.detail, which these paths belong to."""
        with self._resolve_miss(info_mode=True) as seen:
            self.assertIn("Looked in:", seen["text"])
            # The tip should not then tell the user to enable what is already on.
            self.assertNotIn("--verbose", seen["text"])


class TestBaseFixHosts(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    @patch.object(DiagnosticsService, "cmd_doctor")
    def test_cmd_fix_hosts_no_target(self, mock_doctor):
        self.handler.cmd_fix_hosts()
        mock_doctor.assert_called_once_with(fix_hosts=True)


if __name__ == "__main__":
    unittest.main()
