import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler


class MockManager:
    def __init__(self):
        self.args = MagicMock()
        self.args.non_interactive = True
        self.args.verbose = False
        self.workspace = MagicMock()


class TestHostnameResolutionDI(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.handler = BaseHandler(self.manager.args)
        self.handler.manager = self.manager  # type: ignore[assignment]

    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.check_hostname", return_value=True)
    def test_ensure_hostnames_resolve_uses_manager_workspace_when_available(
        self, mock_check_hostname, mock_setup_paths
    ):
        # GIVEN a workspace with client extensions
        tmp_root = Path("/tmp/fake_root_resolution")
        mock_setup_paths.return_value = {
            "root": tmp_root,
            "cx": MagicMock(exists=MagicMock(return_value=True)),
            "ce_dir": tmp_root / "ce_dir",
        }

        # Mock workspace scan results
        self.manager.workspace.scan_client_extensions.return_value = [
            {"id": "ext1", "deploy": True, "has_load_balancer": True},
            {"id": "ext2", "deploy": True, "has_load_balancer": False},
        ]

        # WHEN calling ensure_hostnames_resolve
        with patch("ldm_core.handlers.workspace.WorkspaceService") as mock_ws_class:
            resolved = self.handler.ensure_hostnames_resolve(tmp_root, "example.local")

            # THEN WorkspaceService should NOT be instantiated inline
            mock_ws_class.assert_not_called()

            # AND manager.workspace.scan_client_extensions should be used
            self.manager.workspace.scan_client_extensions.assert_called_once()

            # AND the hostnames (main + extension with load balancer) checked
            mock_check_hostname.assert_any_call("example.local", silent=True)
            mock_check_hostname.assert_any_call("ext1.example.local", silent=True)

            # Note: ext2 shouldn't be checked because has_load_balancer is False
            calls = [c[0][0] for c in mock_check_hostname.call_args_list]
            self.assertNotIn("ext2.example.local", calls)
            self.assertTrue(resolved)

    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.check_hostname", return_value=True)
    @patch("ldm_core.handlers.workspace.WorkspaceService")
    def test_ensure_hostnames_resolve_falls_back_when_manager_workspace_unavailable(
        self, mock_ws_class, mock_check_hostname, mock_setup_paths
    ):
        # GIVEN a handler without manager workspace composed
        self.handler.manager = None  # type: ignore[assignment]
        tmp_root = Path("/tmp/fake_root_resolution")
        mock_setup_paths.return_value = {
            "root": tmp_root,
            "cx": MagicMock(exists=MagicMock(return_value=True)),
            "ce_dir": tmp_root / "ce_dir",
        }

        # Mock the fallback WorkspaceService scanner
        mock_ws_instance = MagicMock()
        mock_ws_class.return_value = mock_ws_instance
        mock_ws_instance.scan_client_extensions.return_value = [
            {"id": "ext-fallback", "deploy": True, "has_load_balancer": True}
        ]

        # WHEN calling ensure_hostnames_resolve
        resolved = self.handler.ensure_hostnames_resolve(tmp_root, "example.local")

        # THEN it should fall back to instantiating WorkspaceService inline
        mock_ws_class.assert_called_once()
        mock_ws_instance.scan_client_extensions.assert_called_once()

        # AND the main and fallback extension hosts are checked
        mock_check_hostname.assert_any_call("example.local", silent=True)
        mock_check_hostname.assert_any_call("ext-fallback.example.local", silent=True)
        self.assertTrue(resolved)

    @patch("ldm_core.handlers.base.BaseHandler.setup_paths")
    @patch("ldm_core.handlers.base.BaseHandler.check_hostname", return_value=True)
    def test_ensure_hostnames_resolve_with_orchestrator_manager(
        self, mock_check_hostname, mock_setup_paths
    ):
        # GIVEN an orchestrator manager (subclassing BaseHandler without self.manager defined, but has workspace composed)
        class MockOrchestratorManager(BaseHandler):
            def __init__(self, args):
                # No super().__init__ called, mimicking LiferayManager
                self.args = args
                self.non_interactive = True
                self.verbose = False
                self.workspace = MagicMock()

        orchestrator = MockOrchestratorManager(self.manager.args)

        # Verify it has no manager attribute (to mimic the AttributeError trigger condition)
        with self.assertRaises(AttributeError):
            _ = orchestrator.manager

        tmp_root = Path("/tmp/fake_root_resolution")
        mock_setup_paths.return_value = {
            "root": tmp_root,
            "cx": MagicMock(exists=MagicMock(return_value=True)),
            "ce_dir": tmp_root / "ce_dir",
        }

        orchestrator.workspace.scan_client_extensions.return_value = [
            {"id": "ext-orch", "deploy": True, "has_load_balancer": True}
        ]

        # WHEN calling ensure_hostnames_resolve
        resolved = orchestrator.ensure_hostnames_resolve(tmp_root, "example.local")

        # THEN it should succeed and call the composed workspace directly
        self.assertTrue(resolved)
        orchestrator.workspace.scan_client_extensions.assert_called_once()
        mock_check_hostname.assert_any_call("ext-orch.example.local", silent=True)

    def test_cmd_fix_hosts_with_orchestrator_manager(self):
        # GIVEN an orchestrator manager (subclassing BaseHandler without self.manager defined, but has diagnostics composed)
        class MockOrchestratorManager(BaseHandler):
            def __init__(self, args):
                self.args = args
                self.diagnostics = MagicMock()

        orchestrator = MockOrchestratorManager(self.manager.args)

        # WHEN calling cmd_fix_hosts with no target
        orchestrator.cmd_fix_hosts(target=None)

        # THEN it should delegate to diagnostics.cmd_doctor
        orchestrator.diagnostics.cmd_doctor.assert_called_once_with(fix_hosts=True)
