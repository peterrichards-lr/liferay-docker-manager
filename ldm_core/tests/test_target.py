"""Unit tests for LDM Multi-Node Orchestration Target Registry (ldm_core/config.py)."""

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
