import json
import sys

import requests
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

from ldm_core.handlers.base import BaseHandler

# We will use the existing mcp_server logic to mock the tool definitions for the REST API
from ldm_core.handlers.mcp import mcp_server
from ldm_core.ui import UI


class AiService(BaseHandler):
    """Service to handle the 'ldm ai' CLI conversational interface."""

    def __init__(self, manager):
        super().__init__(manager.args)
        self.manager = manager

    def _get_api_key(self):
        """Retrieves the Gemini API Key from the global config."""
        config = self.manager.config.get_global_config()
        key = config.get("gemini_api_key")
        if not key:
            UI.info("To use 'ldm ai', you need a free Google Gemini API Key.")
            UI.info(
                f"Get one here: {UI.CYAN}https://aistudio.google.com/app/apikey{UI.COLOR_OFF}"
            )
            key = UI.ask("Enter your Gemini API Key")
            if not key:
                UI.die("API Key is required to proceed.")

            config["gemini_api_key"] = key
            # Save it via config service
            from ldm_core.utils import get_actual_home

            config_path = get_actual_home() / ".ldmrc"
            config_path.write_text(json.dumps(config, indent=4))
            UI.success("API Key saved to global config.")
        return key

    def _get_mcp_tools_schema(self):
        """Converts our FastMCP tools into Google Gemini Function Calling schema."""
        tools = []
        for tool_name, tool in mcp_server._tools.items():
            params: dict = {
                "type": "object",
                "properties": {},
            }
            # FastMCP uses Pydantic under the hood to generate schemas
            schema = tool.parameters_schema
            if schema and "properties" in schema:
                for prop_name, prop_details in schema["properties"].items():
                    params["properties"][prop_name] = {
                        "type": prop_details.get("type", "string"),
                        "description": prop_details.get("description", ""),
                    }
                    if "enum" in prop_details:
                        params["properties"][prop_name]["enum"] = prop_details["enum"]

                if "required" in schema:
                    params["required"] = schema["required"]

            tools.append(
                {
                    "name": tool_name,
                    "description": tool.description,
                    "parameters": params,
                }
            )

        return [{"functionDeclarations": tools}]

    async def _execute_mcp_tool(self, tool_name, tool_args):
        """Executes the local MCP tool to gather data."""
        UI.info(
            f"🤖 AI is investigating using local tool: {UI.CYAN}{tool_name}({tool_args}){UI.COLOR_OFF}..."
        )

        # We spawn a subprocess connecting to our own `ldm mcp` command over stdio
        server_params = {"command": sys.executable, "args": [sys.argv[0], "mcp"]}

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=tool_args)

                # FastMCP returns a list of CallToolResult objects (text or image)
                return "\n".join(
                    content.text for content in result.content if content.type == "text"
                )

    async def _chat_loop(self, query):
        """The main execution loop for the Gemini REST API."""
        api_key = self._get_api_key()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

        system_instruction = {
            "parts": [
                {
                    "text": "You are LDM AI, a senior technical support engineer embedded within the Liferay Docker Manager (LDM) CLI. Your job is to help the user troubleshoot their local Liferay Docker environments or write configuration files. You have access to local tools to read their project lists, logs, and configuration files. Do not ask for logs if you can fetch them yourself using the tools."
                }
            ]
        }

        messages = [{"role": "user", "parts": [{"text": query}]}]
        tools = self._get_mcp_tools_schema()

        # Initial request
        payload = {
            "systemInstruction": system_instruction,
            "contents": messages,
            "tools": tools,
        }

        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()

        if "candidates" not in data:
            UI.error("Unexpected response from Gemini API.")
            return

        message = data["candidates"][0]["content"]

        # Check if the AI wants to call a tool
        if "parts" in message and any(
            "functionCall" in part for part in message["parts"]
        ):
            # Append AI's response to history
            messages.append(message)

            tool_responses = []
            for part in message["parts"]:
                if "functionCall" in part:
                    call = part["functionCall"]
                    tool_name = call["name"]
                    tool_args = call.get("args", {})

                    try:
                        result_text = await self._execute_mcp_tool(tool_name, tool_args)
                    except Exception as e:
                        result_text = f"Error executing tool: {e}"

                    tool_responses.append(
                        {
                            "functionResponse": {
                                "name": tool_name,
                                "response": {"name": tool_name, "content": result_text},
                            }
                        }
                    )

            # Send the tool outputs back to Gemini
            messages.append({"role": "user", "parts": tool_responses})

            payload = {
                "systemInstruction": system_instruction,
                "contents": messages,
                "tools": tools,
            }

            UI.info("🤖 AI is analyzing the diagnostic data...")
            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            message = data["candidates"][0]["content"]

        # Print final response
        for part in message.get("parts", []):
            if "text" in part:
                print(f"\n{UI.BOLD}{UI.CYAN}LDM AI:{UI.COLOR_OFF}\n")
                print(
                    UI.redact(part["text"])
                )  # codeql[py/clear-text-logging-sensitive-data]

    def cmd_ai(self, query):
        """Entry point for the ldm ai command."""
        import asyncio

        # We run the async chat loop synchronously for the CLI
        try:
            asyncio.run(self._chat_loop(query))
        except requests.exceptions.HTTPError as e:
            UI.error(f"API Error: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            UI.error(f"Failed to execute AI flow: {e}")
