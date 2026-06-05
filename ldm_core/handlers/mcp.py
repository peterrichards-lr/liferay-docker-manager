import json
import logging

from mcp.server.fastmcp import FastMCP

from ldm_core.utils import run_command

# Initialize FastMCP Server
mcp_server = FastMCP("LDM Diagnostics Server")
# Store manager as a global for the MCP tools to access
_manager = None


@mcp_server.tool()
def get_projects() -> str:
    """Lists all managed Liferay Docker environments and their status."""
    if not _manager:
        return json.dumps({"error": "Manager not initialized"})

    roots = _manager.find_dxp_roots()
    projects = []

    for r in roots:
        path = r["path"]
        meta = _manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )

        status = "Stopped"
        containers_status = run_command(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name=^{name}$",
                "--format",
                "{{.State}}",
            ],
            check=False,
        )
        if containers_status:
            states = containers_status.splitlines()
            if states:
                status = states[0].capitalize()

        projects.append(
            {
                "name": name,
                "version": r["version"],
                "status": status,
                "path": str(path),
            }
        )

    return json.dumps(projects, indent=2)


@mcp_server.tool()
def get_logs(project_id: str, lines: int = 200) -> str:
    """Retrieves the recent logs for a specific Liferay project container."""
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", str(project_id)):
        return "Error: Invalid project ID format."

    try:
        lines_int = int(lines)
    except ValueError:
        return "Error: lines must be an integer."

    if not _manager:
        return "Error: Manager not initialized"

    roots = _manager.find_dxp_roots()
    project_path = None
    container_name = None

    for r in roots:
        path = r["path"]
        meta = _manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )
        if name == project_id:
            project_path = path
            container_name = str(name)
            break

    if not project_path or not container_name:
        return f"Error: Project '{project_id}' not found."

    logs = run_command(
        ["docker", "logs", "--tail", str(lines_int), container_name],
        check=False,
    )
    return logs or "No logs available or container not running."


@mcp_server.tool()
def get_config(project_id: str) -> str:
    """Retrieves the properties and configuration metadata for a project."""
    if not _manager:
        return json.dumps({"error": "Manager not initialized"})

    roots = _manager.find_dxp_roots()
    for r in roots:
        path = r["path"]
        meta = _manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )
        if name == project_id:
            config_data = {
                "metadata": meta,
            }
            # Try to grab portal-ext.properties if it exists
            portal_ext = path / "common" / "portal-ext.properties"
            if portal_ext.exists():
                config_data["portal-ext"] = portal_ext.read_text(errors="ignore")

            return json.dumps(config_data, indent=2)

    return json.dumps({"error": f"Project '{project_id}' not found."})


class McpService:
    """Service to handle the MCP JSON-RPC Server lifecycle."""

    def __init__(self, manager):
        self.manager = manager
        global _manager  # noqa: PLW0603
        _manager = manager

    def cmd_mcp(self):
        """Starts the MCP JSON-RPC Server over stdio."""
        # Suppress logging to stdout so it doesn't corrupt the JSON-RPC stream
        logging.getLogger("mcp").setLevel(logging.CRITICAL)

        # Run the FastMCP server
        mcp_server.run()
