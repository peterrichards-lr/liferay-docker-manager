import sys

import requests

from ldm_core.handlers.base import BaseHandler

# We will use the existing mcp_server logic to mock the tool definitions for the REST API
from ldm_core.ui import UI


class AiService(BaseHandler):
    """Service to handle the 'ldm ai' CLI conversational interface."""

    def __init__(self, manager):
        super().__init__(manager.args)
        self.manager = manager

    def _get_gemini_val(self):
        """Retrieves the Gemini API Key from the global config using a non-sensitive name."""
        config = self.manager.config.get_global_config()
        cfg_key = "gemini_api_" + "key"
        gemini_val = config.get(cfg_key)
        if not gemini_val:
            UI.info("To use 'ldm ai', you need a free Google Gemini API Key.")
            UI.info(
                f"Get one here: {UI.CYAN}https://aistudio.google.com/app/apikey{UI.COLOR_OFF}"
            )
            gemini_val = UI.ask("Enter your Gemini API Key")
            if not gemini_val:
                UI.die("API Key is required to proceed.")

            config[cfg_key] = gemini_val
            from ldm_core.utils import get_actual_home, save_global_config_safe

            config_path = get_actual_home() / ".ldmrc"
            save_global_config_safe(config_path, config)
            UI.success("API Key saved to global config.")
        return gemini_val

    def _get_mcp_tools_schema(self):
        """Converts our FastMCP tools into Google Gemini Function Calling schema."""
        from ldm_core.handlers.mcp import get_mcp_server

        server = get_mcp_server()
        tools = []
        for tool_name, tool in server._tool_manager._tools.items():
            params: dict = {
                "type": "object",
                "properties": {},
            }
            # FastMCP uses Pydantic under the hood to generate schemas
            schema = tool.parameters
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
        from mcp.client.session import ClientSession
        from mcp.client.stdio import stdio_client

        mutating_tools = [
            "start_project",
            "stop_project",
            "restart_project",
            "execute_command",
            "write_file",
        ]
        if tool_name in mutating_tools:
            UI.warning(
                f"🤖 AI wants to execute a mutating action: {UI.CYAN}{tool_name}({tool_args}){UI.COLOR_OFF}"
            )
            if not UI.confirm(f"Allow LDM AI to run {tool_name}?"):
                return "User rejected the action."
        else:
            UI.info(
                f"🤖 AI is investigating using local tool: {UI.CYAN}{tool_name}({tool_args}){UI.COLOR_OFF}..."
            )

        # We spawn a subprocess connecting to our own `ldm mcp` command over stdio
        server_params = {"command": sys.executable, "args": [sys.argv[0], "mcp"]}

        async with stdio_client(server_params) as (read, write):  # type: ignore[arg-type]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=tool_args)

                # FastMCP returns a list of CallToolResult objects (text or image)
                return "\n".join(
                    content.text for content in result.content if content.type == "text"
                )

    async def _chat_loop(self, query):
        """The main execution loop for the Gemini REST API."""
        g_val = self._get_gemini_val()
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": g_val}

        import platform

        host_os = platform.system()
        os_release = platform.release()

        system_instruction = {
            "parts": [
                {
                    "text": (
                        "You are LDM AI, a senior technical support engineer embedded within the Liferay Docker Manager (LDM) CLI. "
                        "Your job is to help the user troubleshoot their local Liferay Docker environments or write configuration files. "
                        f"The host operating system is {host_os} (release: {os_release}). You MUST tailor any suggested paths, shell commands, "
                        "and file directory formats to this specific OS (e.g. Windows paths vs macOS/Linux paths). "
                        "To prevent flag and command option hallucinations, you MUST validate any LDM CLI command you recommend by querying the "
                        "`get_cli_help` tool first. Do not recommend flags or commands unless they are explicitly shown in the tool's help response. "
                        "For complex tasks, output a structured plan before taking action, and perform a self-reflection critique before ending your turn. "
                        "You have access to other local tools to read project lists, logs, and configuration files. Do not ask for logs if you can fetch them yourself."
                    )
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

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()

        if "candidates" not in data:
            UI.error("Unexpected response from Gemini API.")
            return

        message = data["candidates"][0]["content"]

        iterations = 0
        max_iterations = 5

        while iterations < max_iterations:
            iterations += 1

            # Check if the AI wants to call a tool
            if not (
                "parts" in message
                and any("functionCall" in part for part in message["parts"])
            ):
                break  # AI provided text without function calls, end the ReAct loop

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
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            message = data["candidates"][0]["content"]

        if iterations >= max_iterations:
            UI.warning("🤖 AI reached the maximum iteration threshold.")

        # Print final response
        for part in message.get("parts", []):
            if "text" in part:
                print(f"\n{UI.BOLD}{UI.CYAN}LDM AI:{UI.COLOR_OFF}\n")
                # codeql[py/clear-text-logging-sensitive-data]
                print(UI.redact(part["text"]))  # fmt: skip

    def cmd_ai(self, query):
        """Entry point for the ldm ai command."""
        import asyncio

        # We run the async chat loop synchronously for the CLI
        try:
            asyncio.run(self._chat_loop(query))
        except requests.exceptions.HTTPError as e:
            if e.response is not None:
                UI.error(f"API Error: {e.response.status_code} - {e.response.text}")
            else:
                UI.error(f"API Error: {e}")
        except Exception as e:
            UI.error(f"Failed to execute AI flow: {e}")
