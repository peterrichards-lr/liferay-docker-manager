import json
import os
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
    McpService(mock_manager)
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


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    import os

    import ldm_core.handlers.mcp as mcp_module

    mcp_module._mutation_history = []
    mcp_module._circuit_breaker_tripped = False
    if "LDM_MCP_CIRCUIT_BREAKER_MAX_ACTIONS" in os.environ:
        del os.environ["LDM_MCP_CIRCUIT_BREAKER_MAX_ACTIONS"]
    if "LDM_MCP_CIRCUIT_BREAKER_WINDOW" in os.environ:
        del os.environ["LDM_MCP_CIRCUIT_BREAKER_WINDOW"]


def test_circuit_breaker_under_threshold(mock_manager):
    from ldm_core.handlers.mcp import start_project

    # 4 calls are under the default threshold of 5
    for _ in range(4):
        res = start_project("liferay-project1")
        assert "Success" in res


def test_circuit_breaker_trips(mock_manager):
    from ldm_core.handlers.mcp import start_project, stop_project

    # 5 actions are allowed (threshold is 5)
    for _ in range(5):
        res = start_project("liferay-project1")
        assert "Success" in res

    # 6th action trips it
    res = start_project("liferay-project1")
    assert "Error: AI Action Circuit Breaker TRIPPED" in res

    # 7th action is blocked immediately by tripped flag
    res_stop = stop_project("liferay-project1")
    assert "Error: AI Action Circuit Breaker is currently TRIPPED" in res_stop


def test_circuit_breaker_non_mutating_allowed(mock_manager):
    from ldm_core.handlers.mcp import get_projects, start_project

    # Trip it
    for _ in range(6):
        start_project("liferay-project1")

    # Mutating command is blocked
    res = start_project("liferay-project1")
    assert "Circuit Breaker" in res

    # Non-mutating command is allowed
    res_projects = get_projects()
    assert "liferay-project1" in res_projects


def test_circuit_breaker_env_vars(mock_manager):
    from ldm_core.handlers.mcp import start_project

    os.environ["LDM_MCP_CIRCUIT_BREAKER_MAX_ACTIONS"] = "2"

    # 2 actions are allowed
    for _ in range(2):
        res = start_project("liferay-project1")
        assert "Success" in res

    # 3rd action trips it
    res2 = start_project("liferay-project1")
    assert "Error: AI Action Circuit Breaker TRIPPED" in res2


@patch("ldm_core.plugin_manager.ensure_mcp_installed")
def test_get_mcp_server(mock_ensure_mcp, mock_manager):
    import sys

    mock_fastmcp_module = MagicMock()
    mock_fast_mcp = MagicMock()
    mock_fastmcp_module.FastMCP = mock_fast_mcp
    sys.modules["mcp.server.fastmcp"] = mock_fastmcp_module

    import ldm_core.handlers.mcp as mcp_module

    # Reset singleton
    mcp_module._mcp_server_instance = None

    server_mock = MagicMock()
    mock_fast_mcp.return_value = server_mock

    # Act
    server = mcp_module.get_mcp_server()

    # Assert
    mock_ensure_mcp.assert_called_once()
    mock_fast_mcp.assert_called_once_with("LDM Diagnostics Server")
    assert server == server_mock

    # Check tools were registered
    assert server_mock.tool.call_count >= 7

    # Call it again to test singleton
    server2 = mcp_module.get_mcp_server()
    assert server2 == server_mock
    # Ensure it wasn't re-initialized
    mock_ensure_mcp.assert_called_once()


@patch("ldm_core.handlers.mcp.get_mcp_server")
@patch("ldm_core.handlers.mcp.logging.getLogger")
def test_mcp_service_cmd_mcp(mock_get_logger, mock_get_mcp_server, mock_manager):
    import ldm_core.handlers.mcp as mcp_module

    server_mock = MagicMock()
    mock_get_mcp_server.return_value = server_mock

    logger_mock = MagicMock()
    mock_get_logger.return_value = logger_mock

    service = mcp_module.McpService(mock_manager)
    service.cmd_mcp()

    mock_get_mcp_server.assert_called_once()
    mock_get_logger.assert_called_with("mcp")
    logger_mock.setLevel.assert_called_with(mcp_module.logging.CRITICAL)
    server_mock.run.assert_called_once()
