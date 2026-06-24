import json
from unittest.mock import MagicMock, patch

import pytest

import ldm_core.handlers.mcp as mcp_module
from ldm_core.handlers.mcp import McpService, get_config, get_logs, get_projects


@pytest.fixture
def mock_manager():
    manager = MagicMock()
    path_mock = MagicMock()
    path_mock.name = "project1"

    manager.find_dxp_roots.return_value = [{"path": path_mock, "version": "2024.q1.3"}]

    manager.read_meta.return_value = {
        "liferay_container_name": "liferay-project1",
        "db_password": "supersecret",  # pragma: allowlist secret
    }

    # Mock config properties
    manager.config._get_properties.return_value = {
        "custom.secret.token": "12345",
        "normal.prop": "value",
    }

    mcp_module._manager = manager
    yield manager
    mcp_module._manager = None


def test_mcp_service_init(mock_manager):
    mcp_module._manager = None
    service = McpService(mock_manager)
    assert mcp_module._manager == mock_manager


@patch("ldm_core.handlers.mcp.run_command")
def test_get_projects(mock_run_command, mock_manager):
    mock_run_command.return_value = "running\n"
    res = get_projects()
    data = json.loads(res)
    assert len(data) == 1
    assert data[0]["name"] == "liferay-project1"
    assert data[0]["status"] == "Running"
    assert data[0]["version"] == "2024.q1.3"


@patch("ldm_core.handlers.mcp.run_command")
def test_get_logs(mock_run_command, mock_manager):
    mock_run_command.return_value = "log line 1\nlog line 2"
    res = get_logs("liferay-project1", 10)
    assert "log line 1" in res
    mock_run_command.assert_called_with(
        ["docker", "logs", "--tail", "10", "liferay-project1"], check=False
    )


def test_get_logs_invalid_format(mock_manager):
    res = get_logs("invalid project !@#")
    assert "Error: Invalid project ID format" in res


def test_get_logs_not_found(mock_manager):
    res = get_logs("non-existent")
    assert "not found" in res


def test_get_config(mock_manager):
    # Setup mock portal-ext.properties
    path_mock = mock_manager.find_dxp_roots.return_value[0]["path"]
    portal_ext_mock = MagicMock()
    portal_ext_mock.exists.return_value = True
    portal_ext_mock.read_text.return_value = (
        "custom.secret.token=12345\nnormal.prop=value"
    )

    # Make path_mock / "common" / "portal-ext.properties" return portal_ext_mock
    common_dir_mock = MagicMock()
    path_mock.__truediv__.return_value = common_dir_mock
    common_dir_mock.__truediv__.return_value = portal_ext_mock

    res = get_config("liferay-project1")
    data = json.loads(res)

    assert "metadata" in data
    assert data["metadata"]["db_password"] == "[REDACTED]"
    assert "portal-ext" in data
    assert data["portal-ext"]["custom.secret.token"] == "[REDACTED]"
    assert data["portal-ext"]["normal.prop"] == "value"


def test_get_config_not_found(mock_manager):
    res = get_config("non-existent")
    data = json.loads(res)
    assert "error" in data


@patch("ldm_core.handlers.mcp.run_command")
def test_get_logs_filtering(mock_run_command, mock_manager):
    log_output = (
        "Startup log\n"
        "10:00:00 INFO  [main] portal starting\n"
        "10:00:01 WARN  [main] configuration warning\n"
        "10:00:02 ERROR [main] database failed\n"
        "java.sql.SQLException\n"
        "    at MyClass.run"
    )
    mock_run_command.return_value = log_output

    # Test grep
    res = get_logs("liferay-project1", 200, grep="database")
    assert "database failed" in res
    assert "portal starting" not in res

    # Test level ERROR
    res = get_logs("liferay-project1", 200, level="ERROR")
    # Should keep ERROR log and subsequent traceback lines
    assert "database failed" in res
    assert "java.sql.SQLException" in res
    assert "portal starting" not in res
    assert "configuration warning" not in res
    assert "Startup log" not in res


def test_start_project(mock_manager):
    from ldm_core.handlers.mcp import start_project

    res = start_project("liferay-project1")
    assert "Success" in res
    mock_manager.runtime.cmd_run.assert_called_with(project_id="project1")


def test_stop_project(mock_manager):
    from ldm_core.handlers.mcp import stop_project

    res = stop_project("liferay-project1")
    assert "Success" in res
    mock_manager.runtime.cmd_stop.assert_called_with(project_id="project1")


def test_restart_project(mock_manager):
    from ldm_core.handlers.mcp import restart_project

    res = restart_project("liferay-project1", service="liferay")
    assert "Success" in res
    mock_manager.runtime.cmd_restart.assert_called_with(
        project_id="project1", service="liferay"
    )


def test_get_cli_help():
    from ldm_core.handlers.mcp import get_cli_help

    # Test overall help
    res = get_cli_help()
    assert "usage:" in res.lower()

    # Test subcommand help
    res_sub = get_cli_help("run")
    assert "run" in res_sub.lower()

    # Test non-existent command error
    res_err = get_cli_help("non-existent-subcommand")
    assert "Error" in res_err
    assert "Available commands" in res_err
