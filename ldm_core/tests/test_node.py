"""Unit tests for NodeService (ldm node power subcommands)."""

from unittest.mock import MagicMock, patch

from ldm_core.handlers.node import NodeService


class TestNodeService:
    """Test suite for NodeService handler methods."""

    def setup_method(self) -> None:
        self.mock_manager = MagicMock()
        self.node_service = NodeService(self.mock_manager)

    @patch("subprocess.run")
    def test_cmd_node_power_status(self, mock_run: MagicMock) -> None:
        mock_run.return_value.returncode = 0
        res = self.node_service.cmd_node_power_status()
        assert res == 0
        assert mock_run.call_count == 1
        args = mock_run.call_args[0][0]
        assert "status" in args

    @patch("subprocess.run")
    def test_cmd_node_power_wake(self, mock_run: MagicMock) -> None:
        mock_run.return_value.returncode = 0
        res = self.node_service.cmd_node_power_wake("aws-1", ttl="4h")
        assert res == 0
        assert mock_run.call_count == 1
        args = mock_run.call_args[0][0]
        assert "wake" in args
        assert "aws-1" in args
        assert "4h" in args

    @patch("subprocess.run")
    def test_cmd_node_power_sleep(self, mock_run: MagicMock) -> None:
        mock_run.return_value.returncode = 0
        res = self.node_service.cmd_node_power_sleep("aws-1")
        assert res == 0
        assert mock_run.call_count == 1
        args = mock_run.call_args[0][0]
        assert "sleep" in args
        assert "aws-1" in args

    @patch("subprocess.run")
    def test_cmd_node_power_enforce(self, mock_run: MagicMock) -> None:
        mock_run.return_value.returncode = 0
        res = self.node_service.cmd_node_power_enforce()
        assert res == 0
        assert mock_run.call_count == 1
        args = mock_run.call_args[0][0]
        assert "enforce" in args

    @patch("subprocess.run")
    def test_cmd_node_power_sync_dns(self, mock_run: MagicMock) -> None:
        mock_run.return_value.returncode = 0
        res = self.node_service.cmd_node_power_sync_dns()
        assert res == 0
        assert mock_run.call_count == 1
        args = mock_run.call_args[0][0]
        assert "sync-dns" in args
