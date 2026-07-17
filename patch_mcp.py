import re
with open("ldm_core/handlers/mcp.py", "r") as f:
    content = f.read()

# Remove the fastmcp import and global init
content = content.replace("from mcp.server.fastmcp import FastMCP", "")
content = content.replace("mcp_server = FastMCP(\"LDM Diagnostics Server\")", "_mcp_server_instance = None")

# Remove decorators
content = content.replace("@mcp_server.tool()\n", "")

# Find all tool functions to attach them later
# Functions that were tools
tool_funcs = [
    "get_projects", "get_logs", "start_project", "stop_project", "restart_project", "get_config", "get_cli_help"
]

# Add get_mcp_server factory
factory = f"""

def get_mcp_server():
    global _mcp_server_instance
    if _mcp_server_instance is not None:
        return _mcp_server_instance

    from ldm_core.plugin_manager import ensure_mcp_installed
    ensure_mcp_installed()

    from mcp.server.fastmcp import FastMCP
    server = FastMCP("LDM Diagnostics Server")
    
    server.tool()(get_projects)
    server.tool()(get_logs)
    server.tool()(start_project)
    server.tool()(stop_project)
    server.tool()(restart_project)
    server.tool()(get_config)
    server.tool()(get_cli_help)

    _mcp_server_instance = server
    return server
"""

content = content.replace("class McpService:", factory + "\n\nclass McpService:")

# In cmd_mcp, use get_mcp_server()
cmd_mcp_old = """    def cmd_mcp(self):
        \"\"\"Starts the MCP JSON-RPC Server over stdio.\"\"\"
        # Suppress logging to stdout so it doesn't corrupt the JSON-RPC stream
        logging.getLogger("mcp").setLevel(logging.CRITICAL)

        # Run the FastMCP server
        mcp_server.run()"""

cmd_mcp_new = """    def cmd_mcp(self):
        \"\"\"Starts the MCP JSON-RPC Server over stdio.\"\"\"
        server = get_mcp_server()
        # Suppress logging to stdout so it doesn't corrupt the JSON-RPC stream
        logging.getLogger("mcp").setLevel(logging.CRITICAL)

        # Run the FastMCP server
        server.run()"""

content = content.replace(cmd_mcp_old, cmd_mcp_new)

with open("ldm_core/handlers/mcp.py", "w") as f:
    f.write(content)
