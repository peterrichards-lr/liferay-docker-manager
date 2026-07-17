import subprocess
import sys
from unittest.mock import MagicMock, patch

from ldm_core.plugin_manager import ensure_mcp_installed


@patch("ldm_core.plugin_manager.importlib.util.find_spec")
@patch("ldm_core.plugin_manager.get_actual_home")
@patch("ldm_core.plugin_manager.subprocess.run")
def test_ensure_mcp_installed_already_present(mock_run, mock_get_home, mock_find_spec):
    # Mock that mcp is already installed
    mock_find_spec.return_value = MagicMock()

    home_path = MagicMock()
    mock_get_home.return_value = home_path

    plugins_dir = home_path / ".ldm" / "plugins" / "ai"

    ensure_mcp_installed()

    # Should not run pip install
    mock_run.assert_not_called()
    assert str(plugins_dir) in sys.path


@patch("ldm_core.plugin_manager.importlib.util.find_spec")
@patch("ldm_core.plugin_manager.get_actual_home")
@patch("ldm_core.plugin_manager.subprocess.run")
def test_ensure_mcp_installed_not_present(mock_run, mock_get_home, mock_find_spec):
    # Mock that mcp is NOT installed
    mock_find_spec.return_value = None

    home_path = MagicMock()
    mock_get_home.return_value = home_path

    plugins_dir = home_path / ".ldm" / "plugins" / "ai"

    ensure_mcp_installed()

    # Should run pip install
    mock_run.assert_called_once_with(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "mcp==1.28.1",
            "--target",
            str(plugins_dir),
            "--upgrade",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert str(plugins_dir) in sys.path


@patch("ldm_core.plugin_manager.importlib.util.find_spec")
@patch("ldm_core.plugin_manager.get_actual_home")
@patch("ldm_core.plugin_manager.subprocess.run")
@patch("ldm_core.plugin_manager.sys.exit")
def test_ensure_mcp_installed_fails(mock_exit, mock_run, mock_get_home, mock_find_spec):
    mock_find_spec.return_value = None
    mock_run.side_effect = subprocess.CalledProcessError(1, "pip")

    home_path = MagicMock()
    mock_get_home.return_value = home_path

    ensure_mcp_installed()

    mock_exit.assert_called_once_with(1)
