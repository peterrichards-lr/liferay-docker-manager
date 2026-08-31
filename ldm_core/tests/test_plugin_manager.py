import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.constants import PIP_INSTALL_TIMEOUT
from ldm_core.plugin_manager import MCP_PIN, ensure_mcp_installed


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
            MCP_PIN,
            "--target",
            str(plugins_dir),
            "--upgrade",
            "--break-system-packages",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # LDM-#1332: bounded. Both streams go to DEVNULL here, so an install
        # against an unreachable index hung with no output at all.
        timeout=PIP_INSTALL_TIMEOUT,
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


@patch("ldm_core.plugin_manager.importlib.util.find_spec")
@patch("ldm_core.plugin_manager.get_actual_home")
@patch("ldm_core.plugin_manager.subprocess.run")
def test_ensure_gui_installed(mock_run, mock_get_home, mock_find_spec):
    from ldm_core.plugin_manager import ensure_gui_installed

    mock_find_spec.return_value = None
    home_path = MagicMock()
    mock_get_home.return_value = home_path
    plugins_dir = home_path / ".ldm" / "plugins" / "gui"

    with patch("sys.platform", "darwin"):
        ensure_gui_installed()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "--break-system-packages" in args
        assert "pyobjc-framework-Quartz" in args
        assert "pyobjc-framework-Cocoa" in args
        assert "--target" in args
        assert str(plugins_dir) in sys.path


class TestMcpPinConsistency(unittest.TestCase):
    """The runtime installer's pin must match the declared dependency (#1483).

    `ensure_mcp_installed` pip-installs mcp into ~/.ldm/plugins/ai for *binary*
    deployments, which have no requirements.txt to install from. Its pin is
    therefore a second, independent copy of the same fact.

    Dependabot updates requirements.txt and pyproject.toml but not
    plugin_manager.py, so #1480 proposed shipping a binary that installed
    mcp 2.0.0 while the source declared 2.1.1. Nothing caught it: the old test
    asserted a hardcoded literal, which agreed with the stale pin.
    """

    def _declared(self, path, pattern):
        text = Path(path).read_text(encoding="utf-8")
        match = re.search(pattern, text, re.MULTILINE)
        if match is None:
            # self.fail is NoReturn, which narrows for mypy where
            # assertIsNotNone does not.
            self.fail(f"no mcp pin found in {path}")
        return match.group(1)

    def test_matches_requirements_txt(self):
        declared = self._declared("requirements.txt", r"^(mcp==[^\s]+)$")
        self.assertEqual(
            MCP_PIN,
            declared,
            "plugin_manager.MCP_PIN has drifted from requirements.txt -- a "
            "binary install would fetch a different mcp than the source declares",
        )

    def test_matches_pyproject(self):
        declared = self._declared("pyproject.toml", r'"(mcp==[^"]+)"')
        self.assertEqual(
            MCP_PIN,
            declared,
            "plugin_manager.MCP_PIN has drifted from pyproject.toml",
        )
