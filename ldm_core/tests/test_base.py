import unittest
import os
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.workspace import WorkspaceHandler


class MockBaseManager(BaseHandler, WorkspaceHandler):
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True


class TestBaseHardening(unittest.TestCase):
    def setUp(self):
        self.handler = MockBaseManager()

    @unittest.skipIf(
        platform.system().lower() == "windows", "Colima tests only valid on POSIX"
    )
    def test_get_colima_mount_flags_home(self):
        # Path in home directory
        with patch.dict(os.environ, {"USER": "peter", "SUDO_USER": ""}):
            paths = [Path("/Users/peter/repos/project")]
            flags = self.handler.get_colima_mount_flags(paths)
            self.assertIn("--mount /Users/$(whoami):w", flags)

    @unittest.skipIf(
        platform.system().lower() == "windows", "Colima tests only valid on POSIX"
    )
    def test_get_colima_mount_flags_volumes(self):
        # Path on external volume
        paths = [Path("/Volumes/SanDisk/projects")]
        flags = self.handler.get_colima_mount_flags(paths)
        self.assertIn("--mount /Volumes/SanDisk:w", flags)

    @unittest.skipIf(
        platform.system().lower() == "windows", "Colima tests only valid on POSIX"
    )
    def test_get_colima_mount_flags_multiple(self):
        # Mixed paths
        with patch.dict(os.environ, {"USER": "peter"}):
            paths = [Path("/Users/peter/certs"), Path("/Volumes/SanDisk/project")]
            flags = self.handler.get_colima_mount_flags(paths)
            self.assertIn("--mount /Users/$(whoami):w", flags)
            self.assertIn("--mount /Volumes/SanDisk:w", flags)

    @patch("ldm_core.handlers.base.BaseHandler.get_resolved_ip")
    @patch("ldm_core.handlers.base.BaseHandler.read_meta")
    @patch("ldm_core.handlers.workspace.WorkspaceHandler.scan_client_extensions")
    def test_validate_project_dns_filtering(self, mock_scan, mock_meta, mock_resolve):
        # Setup: forge.demo with 3 extensions
        mock_meta.return_value = {"host_name": "forge.demo"}
        mock_resolve.return_value = "127.0.0.1"  # All resolve initially

        mock_scan.return_value = [
            {
                "id": "active-ext",
                "kind": "Deployment",
                "deploy": True,
                "has_load_balancer": True,
            },
            {
                "id": "job-ext",
                "kind": "Job",
                "deploy": True,
                "has_load_balancer": False,
            },
            {
                "id": "disabled-ext",
                "kind": "Deployment",
                "deploy": False,
                "has_load_balancer": True,
            },
        ]

        # Run validation
        with patch.object(
            BaseHandler,
            "setup_paths",
            return_value={"root": Path("."), "cx": Path("."), "ce_dir": Path(".")},
        ):
            ok, unresolved = self.handler.validate_project_dns(".")

        # Verify: Only "active-ext" should have been checked
        self.assertTrue(ok)

        # Now mock failure for the active one
        def resolve_side_effect(host):
            if host == "active-ext.forge.demo":
                return None
            return "127.0.0.1"

        mock_resolve.side_effect = resolve_side_effect
        ok, unresolved = self.handler.validate_project_dns(".")

        self.assertFalse(ok)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0], "active-ext.forge.demo")


if __name__ == "__main__":
    unittest.main()
