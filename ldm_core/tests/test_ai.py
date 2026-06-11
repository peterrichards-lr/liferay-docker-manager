import json
from unittest.mock import MagicMock, patch

import pytest

from ldm_core.handlers.ai import AiService


@pytest.fixture
def mock_manager():
    manager = MagicMock()
    # Mock args
    manager.args = MagicMock()
    # Mock config dict
    manager.config.get_global_config.return_value = {}
    return manager


@patch("ldm_core.handlers.ai.UI")
def test_get_gemini_val_asks_if_missing(mock_ui, mock_manager, tmp_path):
    mock_ui.ask.return_value = "test-api-key"

    with patch("ldm_core.utils.get_actual_home", return_value=tmp_path):
        service = AiService(mock_manager)
        key = service._get_gemini_val()

        assert key == "test-api-key"
        mock_ui.ask.assert_called_once()

        # Check if saved
        config_path = tmp_path / ".ldmrc"
        assert config_path.exists()
        saved_data = json.loads(config_path.read_text())
        assert (
            saved_data.get("gemini_api_key") == "test-api-key"
        )  # pragma: allowlist secret


def test_get_gemini_val_uses_existing(mock_manager):
    mock_manager.config.get_global_config.return_value = {
        "gemini_api_key": "existing-key"  # pragma: allowlist secret
    }
    service = AiService(mock_manager)
    key = service._get_gemini_val()
    assert key == "existing-key"


def test_get_mcp_tools_schema(mock_manager):
    service = AiService(mock_manager)
    schema = service._get_mcp_tools_schema()

    assert len(schema) == 1
    assert "functionDeclarations" in schema[0]
    funcs = schema[0]["functionDeclarations"]

    assert len(funcs) > 0
    names = [f["name"] for f in funcs]
    assert "get_projects" in names
    assert "get_logs" in names
    assert "get_config" in names


@patch("ldm_core.handlers.ai.AiService._chat_loop")
def test_cmd_ai_success(mock_chat_loop, mock_manager):
    async def dummy(*args):
        pass

    mock_chat_loop.side_effect = dummy
    service = AiService(mock_manager)
    service.cmd_ai("help me")
    mock_chat_loop.assert_called_once()


@patch("ldm_core.handlers.ai.AiService._chat_loop")
@patch("ldm_core.handlers.ai.UI")
def test_cmd_ai_handles_exceptions(mock_ui, mock_chat_loop, mock_manager):
    mock_chat_loop.side_effect = Exception("Test error")
    service = AiService(mock_manager)
    service.cmd_ai("help me")
    mock_ui.error.assert_called_with("Failed to execute AI flow: Test error")
