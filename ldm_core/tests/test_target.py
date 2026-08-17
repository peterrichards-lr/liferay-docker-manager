"""Unit tests for LDM Multi-Node Orchestration Target Registry (ldm_core/config.py)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.config import (
    TargetNode,
    delete_target_node,
    get_active_target,
    load_targets,
    save_target_node,
    set_default_target,
)


class TestTargetRegistry(unittest.TestCase):
    """Test suite for TargetNode management and ~/.ldmrc target persistence."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / ".ldmrc"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_targets_default_fallback(self) -> None:
        """Verify load_targets creates default 'local' target if file doesn't exist."""
        targets = load_targets(self.config_path)
        self.assertIn("local", targets)
        self.assertTrue(targets["local"].is_default)
        self.assertEqual(targets["local"].host, "localhost")

    def test_save_target_node(self) -> None:
        """Verify saving a new TargetNode persists correctly in JSON config."""
        node = TargetNode(
            name="win-wsl",
            host="192.168.1.50",
            user="developer",
            key_path="~/.ssh/id_rsa",
            is_default=False,
        )
        saved = save_target_node(node, self.config_path)
        self.assertEqual(saved.name, "win-wsl")

        targets = load_targets(self.config_path)
        self.assertIn("win-wsl", targets)
        self.assertEqual(targets["win-wsl"].host, "192.168.1.50")
        self.assertEqual(targets["win-wsl"].user, "developer")
        self.assertFalse(targets["win-wsl"].is_default)

    def test_save_target_node_switch_default(self) -> None:
        """Verify saving a new default target clears default flag on existing targets."""
        # Ensure local default exists
        load_targets(self.config_path)

        node = TargetNode(name="aws-ec2", host="34.200.1.50", is_default=True)
        save_target_node(node, self.config_path)

        targets = load_targets(self.config_path)
        self.assertTrue(targets["aws-ec2"].is_default)
        self.assertFalse(targets["local"].is_default)

    def test_set_default_target(self) -> None:
        """Verify explicit default target switching."""
        node = TargetNode(name="remote-server", host="10.0.0.5", is_default=False)
        save_target_node(node, self.config_path)

        res = set_default_target("remote-server", self.config_path)
        self.assertTrue(res)

        targets = load_targets(self.config_path)
        self.assertTrue(targets["remote-server"].is_default)
        self.assertFalse(targets["local"].is_default)

    def test_delete_target_node(self) -> None:
        """Verify target deletion and default reassignment."""
        node = TargetNode(name="temp-vps", host="10.0.0.9", is_default=True)
        save_target_node(node, self.config_path)

        # Cannot delete 'local'
        self.assertFalse(delete_target_node("local", self.config_path))

        # Delete default temp-vps -> should reassign default to local
        self.assertTrue(delete_target_node("temp-vps", self.config_path))
        targets = load_targets(self.config_path)
        self.assertNotIn("temp-vps", targets)
        self.assertTrue(targets["local"].is_default)

    def test_get_active_target(self) -> None:
        """Verify active target resolution for project vs global default."""
        node = TargetNode(name="custom-node", host="172.16.0.2", is_default=True)
        save_target_node(node, self.config_path)

        # Project target takes precedence
        active_proj = get_active_target("local", self.config_path)
        self.assertEqual(active_proj.name, "local")

        # Fallback to default when project target is None
        active_default = get_active_target(None, self.config_path)
        self.assertEqual(active_default.name, "custom-node")


class TestTargetCLIHandlers(unittest.TestCase):
    """Test suite for CLI target handlers in ConfigService."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / ".ldmrc"
        self.project_dir = Path(self.temp_dir.name) / "my_project"
        self.project_dir.mkdir(parents=True, exist_ok=True)

        from unittest.mock import MagicMock

        self.manager = MagicMock()
        self.manager.detect_project_path.return_value = self.project_dir
        self.manager.read_meta.return_value = {"project_name": "my_project"}
        self.manager.args = MagicMock()
        self.manager.args.project = "my_project"

        from ldm_core.handlers.config import ConfigService

        self.service = ConfigService(self.manager)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cmd_target_add_and_ls(self) -> None:
        """Test cmd_target_add and cmd_target_ls execution."""
        from unittest.mock import patch

        with patch("ldm_core.config._get_config_path", return_value=self.config_path):
            self.service.cmd_target_add(
                "wsl", host="192.168.1.10", user="dev", default=True
            )
            self.service.cmd_target_ls()

    def test_cmd_target_use_and_rm(self) -> None:
        """Test cmd_target_use and cmd_target_rm execution."""
        from unittest.mock import patch

        with patch("ldm_core.config._get_config_path", return_value=self.config_path):
            self.service.cmd_target_add("aws", host="34.1.1.1")
            self.service.cmd_target_use("aws")
            self.service.cmd_target_rm("aws")

    def test_cmd_target_set(self) -> None:
        """Test cmd_target_set updates project meta.json."""
        from unittest.mock import patch

        with patch("ldm_core.config._get_config_path", return_value=self.config_path):
            self.service.cmd_target_add("aws", host="34.1.1.1")
            self.service.cmd_target_set("aws")
            self.manager.write_meta.assert_called_once()
            called_meta = self.manager.write_meta.call_args[0][1]
            self.assertEqual(called_meta.get("target"), "aws")

    def test_cmd_target_status_online(self) -> None:
        """Test cmd_target_status with mocked online probe response."""
        from unittest.mock import patch

        with (
            patch("ldm_core.config._get_config_path", return_value=self.config_path),
            patch("ldm_core.handlers.config.run_command") as mock_run,
        ):
            mock_run.return_value = "27.0.1|8|17179869184|3"
            self.service.cmd_target_add("wsl", host="192.168.1.10")
            self.service.cmd_target_status("wsl")

    def test_cmd_target_status_offline(self) -> None:
        """Test cmd_target_status with mocked offline failure response."""
        from unittest.mock import patch

        with (
            patch("ldm_core.config._get_config_path", return_value=self.config_path),
            patch("ldm_core.handlers.config.run_command") as mock_run,
        ):
            mock_run.return_value = ""
            self.service.cmd_target_status("local")

    def test_sync_project_to_target_local(self) -> None:
        """Test sync_project_to_target returns True immediately for local target."""
        from ldm_core.config import sync_project_to_target

        res = sync_project_to_target(
            self.project_dir, target_name="local", config_path=self.config_path
        )
        self.assertTrue(res)

    def test_sync_project_to_target_remote(self) -> None:
        """Test sync_project_to_target generates rsync command for remote target."""
        from unittest.mock import patch

        from ldm_core.config import TargetNode, save_target_node, sync_project_to_target

        with patch("ldm_core.config._get_config_path", return_value=self.config_path):
            node = TargetNode(
                name="aws", host="34.1.1.1", user="ubuntu", key_path="~/.ssh/id_rsa"
            )
            save_target_node(node, config_path=self.config_path)

            with patch("ldm_core.config.run_command") as mock_run:
                mock_run.return_value = "OK"
                res = sync_project_to_target(
                    self.project_dir, target_name="aws", config_path=self.config_path
                )
                self.assertTrue(res)
                self.assertGreaterEqual(mock_run.call_count, 2)

    def test_resolve_remote_home_local_returns_none(self) -> None:
        from ldm_core.config import TargetNode, resolve_remote_home

        local = TargetNode(name="local", host="localhost")
        self.assertIsNone(resolve_remote_home(local))

    def test_resolve_remote_home_remote(self) -> None:
        # LDM-#1134: bind-mount sources must be absolute paths -- the
        # remote Docker daemon does not shell-expand `~`.
        from unittest.mock import patch

        from ldm_core.config import TargetNode, resolve_remote_home

        target = TargetNode(name="aws", host="34.1.1.1", user="ec2-user")
        with patch("ldm_core.config.run_command") as mock_run:
            mock_run.return_value = "/home/ec2-user\n"
            home = resolve_remote_home(target)
            self.assertEqual(home, "/home/ec2-user")
            ssh_cmd = mock_run.call_args[0][0]
            self.assertIn("ssh", ssh_cmd)
            self.assertIn("ec2-user@34.1.1.1", ssh_cmd)

    def test_resolve_remote_home_ssh_failure_returns_none(self) -> None:
        from unittest.mock import patch

        from ldm_core.config import TargetNode, resolve_remote_home

        target = TargetNode(name="aws", host="34.1.1.1", user="ec2-user")
        with patch("ldm_core.config.run_command") as mock_run:
            mock_run.return_value = None
            self.assertIsNone(resolve_remote_home(target))

    def test_get_remote_project_root(self) -> None:
        from unittest.mock import patch

        from ldm_core.config import TargetNode, get_remote_project_root

        target = TargetNode(name="aws", host="34.1.1.1", user="ec2-user")
        with patch(
            "ldm_core.config.resolve_remote_home", return_value="/home/ec2-user"
        ):
            root = get_remote_project_root(target, "my-project")
            self.assertEqual(root, "/home/ec2-user/.liferay-docker/projects/my-project")

    def test_get_remote_project_root_returns_none_when_home_unresolvable(
        self,
    ) -> None:
        from unittest.mock import patch

        from ldm_core.config import TargetNode, get_remote_project_root

        target = TargetNode(name="aws", host="34.1.1.1", user="ec2-user")
        with patch("ldm_core.config.resolve_remote_home", return_value=None):
            self.assertIsNone(get_remote_project_root(target, "my-project"))

    def test_cmd_target_migrate(self) -> None:
        """Test cmd_target_migrate execution workflow."""
        from unittest.mock import patch

        with (
            patch("ldm_core.config._get_config_path", return_value=self.config_path),
            patch.object(
                self.manager, "detect_project_path", return_value=self.temp_dir
            ),
            patch.object(self.manager, "read_meta", return_value={"target": "local"}),
            patch.object(self.manager, "write_meta"),
            patch.object(self.manager.snapshot, "cmd_snapshot") as mock_snap,
            patch.object(self.manager.runtime, "cmd_down") as mock_down,
            patch.object(self.manager.runtime, "cmd_run") as mock_run,
            patch("ldm_core.config.sync_project_to_target", create=True) as mock_sync,
        ):
            self.service.cmd_target_add("aws", host="34.1.1.1")
            self.service.cmd_target_migrate("local", "aws")
            mock_snap.assert_called_once()
            mock_down.assert_called_once()
            mock_sync.assert_called_once()
            mock_run.assert_called_once()


class TestTargetContext(unittest.TestCase):
    """Unit tests for TargetContext / resolve_target_context() -- the single
    resolver every command is meant to call, per
    docs/explanation/remote-node-architecture.md. Every test isolates
    config_path so it never depends on the real ~/.ldmrc."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / ".ldmrc"
        self.project_root = Path(self.temp_dir.name) / "myproj"
        self.project_root.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_resolves_local_when_nothing_configured(self) -> None:
        from ldm_core.config import resolve_target_context

        ctx = resolve_target_context(config_path=self.config_path)
        self.assertEqual(ctx.target.name, "local")
        self.assertFalse(ctx.is_remote)
        self.assertEqual(ctx.docker_prefix, ["docker"])

    def test_explicit_target_wins_and_gets_pinned_for_unpinned_project(
        self,
    ) -> None:
        from ldm_core.config import TargetContext, resolve_target_context

        node = TargetNode(name="aws-1", host="34.1.1.1", user="ec2-user")
        save_target_node(node, self.config_path)

        meta: dict = {}
        ctx = resolve_target_context(
            explicit_target="aws-1", meta=meta, config_path=self.config_path
        )
        self.assertIsInstance(ctx, TargetContext)
        self.assertEqual(ctx.target.name, "aws-1")
        self.assertTrue(ctx.is_remote)
        self.assertTrue(ctx.newly_pinned)
        self.assertFalse(ctx.conflict_overridden)
        # Pinning must mutate the meta dict handed in, so the caller's own
        # meta (which it will likely write to disk itself) reflects it.
        self.assertEqual(meta["target"], "aws-1")

    def test_falls_through_to_persisted_default_and_pins_it(self) -> None:
        from ldm_core.config import resolve_target_context

        node = TargetNode(name="aws-2", host="13.1.1.1", is_default=True)
        save_target_node(node, self.config_path)

        meta: dict = {}
        ctx = resolve_target_context(meta=meta, config_path=self.config_path)
        self.assertEqual(ctx.target.name, "aws-2")
        self.assertTrue(ctx.newly_pinned)
        self.assertEqual(meta["target"], "aws-2")

    def test_pinning_persists_to_disk_when_project_root_given(self) -> None:
        from ldm_core.config import resolve_target_context

        node = TargetNode(name="aws-2", host="13.1.1.1", is_default=True)
        save_target_node(node, self.config_path)

        meta: dict = {}
        with (
            patch("ldm_core.utils.write_meta") as mock_write_meta,
            # Avoid a real (slow, environment-dependent) SSH round trip to
            # this fixture's fake IP -- this test is about the pinning
            # write-back, not remote home-directory resolution.
            patch(
                "ldm_core.config.get_remote_project_root",
                return_value="/home/ec2-user/.liferay-docker/projects/myproj",
            ),
        ):
            resolve_target_context(
                meta=meta,
                project_root=self.project_root,
                config_path=self.config_path,
            )
            # write_meta() takes the actual meta *file* path, not the bare
            # project directory -- project_root must be resolved through
            # resolve_meta_file_path() first (see LDM-#1147 Phase 2; this
            # test used to assert the pre-fix, directory-clobbering call).
            mock_write_meta.assert_called_once_with(self.project_root / "meta", meta)

    def test_pin_false_resolves_correctly_without_writing_back(self) -> None:
        """pin=False must still resolve correctly (falling through to the
        persisted default, honoring an explicit target, etc.) but must
        never write into `meta` or touch disk -- for callers (like
        ComposerService.write_docker_compose()'s standalone fallback) that
        need a correct resolution without taking on the pinning
        decision."""
        from ldm_core.config import resolve_target_context

        node = TargetNode(name="aws-2", host="13.1.1.1", is_default=True)
        save_target_node(node, self.config_path)

        meta: dict = {}
        with (
            patch("ldm_core.utils.write_meta") as mock_write_meta,
            # Avoid a real SSH round trip to the fixture's fake IP -- this
            # test is about the pin=False write-back behavior, not remote
            # home-directory resolution (covered separately).
            patch(
                "ldm_core.config.get_remote_project_root",
                return_value="/home/ec2-user/.liferay-docker/projects/myproj",
            ),
        ):
            ctx = resolve_target_context(
                meta=meta,
                project_root=self.project_root,
                config_path=self.config_path,
                pin=False,
            )
            mock_write_meta.assert_not_called()

        self.assertEqual(ctx.target.name, "aws-2")
        self.assertTrue(ctx.is_remote)
        self.assertFalse(ctx.newly_pinned)
        self.assertNotIn("target", meta)

    def test_already_pinned_project_is_not_rewritten(self) -> None:
        from ldm_core.config import resolve_target_context

        node = TargetNode(name="aws-2", host="13.1.1.1", is_default=True)
        save_target_node(node, self.config_path)

        meta = {"target": "local"}
        with patch("ldm_core.utils.write_meta") as mock_write_meta:
            ctx = resolve_target_context(meta=meta, config_path=self.config_path)
            # The project was explicitly pinned to "local" -- the global
            # default being remote must NOT override an existing pin.
            self.assertEqual(ctx.target.name, "local")
            self.assertFalse(ctx.newly_pinned)
            mock_write_meta.assert_not_called()

    @patch("ldm_core.ui.UI.interruptible_pause")
    @patch("ldm_core.ui.UI.warning")
    def test_explicit_target_conflicting_with_pin_warns_and_overrides_for_this_run_only(
        self, mock_warning, mock_pause
    ) -> None:
        from ldm_core.config import resolve_target_context

        for name, host in (("aws-1", "34.1.1.1"), ("aws-2", "13.1.1.1")):
            save_target_node(TargetNode(name=name, host=host), self.config_path)

        meta = {"target": "aws-1"}
        ctx = resolve_target_context(
            explicit_target="aws-2", meta=meta, config_path=self.config_path
        )

        self.assertTrue(mock_warning.called)
        self.assertTrue(mock_pause.called)
        self.assertTrue(ctx.conflict_overridden)
        # The override applies for this run...
        self.assertEqual(ctx.target.name, "aws-2")
        # ...but must NOT silently and permanently reassign the project.
        self.assertEqual(meta["target"], "aws-1")
        self.assertFalse(ctx.newly_pinned)

    @patch("ldm_core.ui.UI.interruptible_pause")
    @patch("ldm_core.ui.UI.warning")
    def test_explicit_target_matching_pin_does_not_warn(
        self, mock_warning, mock_pause
    ) -> None:
        from ldm_core.config import resolve_target_context

        save_target_node(TargetNode(name="aws-1", host="34.1.1.1"), self.config_path)
        meta = {"target": "aws-1"}
        resolve_target_context(
            explicit_target="aws-1", meta=meta, config_path=self.config_path
        )
        mock_warning.assert_not_called()
        mock_pause.assert_not_called()

    def test_map_path_identity_for_local_target(self) -> None:
        from ldm_core.config import resolve_target_context

        ctx = resolve_target_context(
            project_root=self.project_root, config_path=self.config_path
        )
        deploy_path = self.project_root / "deploy"
        self.assertEqual(ctx.map_path(deploy_path), deploy_path)

    def test_map_path_rewrites_onto_remote_root(self) -> None:
        from ldm_core.config import TargetContext

        ctx = TargetContext(
            target=TargetNode(name="aws-1", host="34.1.1.1"),
            is_remote=True,
            docker_prefix=["docker", "--context", "aws-1"],
            compose_prefix=["docker", "--context", "aws-1", "compose"],
            local_root=self.project_root,
            remote_root="/home/ec2-user/.liferay-docker/projects/myproj",
        )
        deploy_path = self.project_root / "deploy"
        mapped = ctx.map_path(deploy_path)
        self.assertEqual(
            str(mapped), "/home/ec2-user/.liferay-docker/projects/myproj/deploy"
        )

    def test_map_path_falls_back_to_identity_when_remote_root_unresolved(
        self,
    ) -> None:
        from ldm_core.config import TargetContext

        ctx = TargetContext(
            target=TargetNode(name="aws-1", host="34.1.1.1"),
            is_remote=True,
            docker_prefix=["docker", "--context", "aws-1"],
            compose_prefix=["docker", "--context", "aws-1", "compose"],
            local_root=self.project_root,
            remote_root=None,
        )
        deploy_path = self.project_root / "deploy"
        self.assertEqual(ctx.map_path(deploy_path), deploy_path)


if __name__ == "__main__":
    unittest.main()
