import re
with open("ldm_core/handlers/ai.py", "r") as f:
    content = f.read()

content = content.replace("from mcp.client.session import ClientSession\n", "")
content = content.replace("from mcp.client.stdio import stdio_client\n", "")
content = content.replace("from ldm_core.handlers.mcp import mcp_server\n", "")

# In _get_mcp_tools_schema
old_get_tools = """    def _get_mcp_tools_schema(self):
        \"\"\"Converts our FastMCP tools into Google Gemini Function Calling schema.\"\"\"
        tools = []
        for tool_name, tool in mcp_server._tool_manager._tools.items():"""

new_get_tools = """    def _get_mcp_tools_schema(self):
        \"\"\"Converts our FastMCP tools into Google Gemini Function Calling schema.\"\"\"
        from ldm_core.handlers.mcp import get_mcp_server
        server = get_mcp_server()
        tools = []
        for tool_name, tool in server._tool_manager._tools.items():"""
content = content.replace(old_get_tools, new_get_tools)

# In _execute_mcp_tool
old_execute = """    async def _execute_mcp_tool(self, tool_name, tool_args):
        \"\"\"Executes the local MCP tool to gather data.\"\"\"
        UI.info(
            f"🤖 AI is investigating using local tool: {UI.CYAN}{tool_name}({tool_args}){UI.COLOR_OFF}..."
        )

        # We spawn a subprocess connecting to our own `ldm mcp` command over stdio
        server_params = {"command": sys.executable, "args": [sys.argv[0], "mcp"]}

        async with stdio_client(server_params) as (read, write):  # type: ignore[arg-type]
            async with ClientSession(read, write) as session:"""

new_execute = """    async def _execute_mcp_tool(self, tool_name, tool_args):
        \"\"\"Executes the local MCP tool to gather data.\"\"\"
        from mcp.client.session import ClientSession
        from mcp.client.stdio import stdio_client

        UI.info(
            f"🤖 AI is investigating using local tool: {UI.CYAN}{tool_name}({tool_args}){UI.COLOR_OFF}..."
        )

        # We spawn a subprocess connecting to our own `ldm mcp` command over stdio
        server_params = {"command": sys.executable, "args": [sys.argv[0], "mcp"]}

        async with stdio_client(server_params) as (read, write):  # type: ignore[arg-type]
            async with ClientSession(read, write) as session:"""

content = content.replace(old_execute, new_execute)

with open("ldm_core/handlers/ai.py", "w") as f:
    f.write(content)
