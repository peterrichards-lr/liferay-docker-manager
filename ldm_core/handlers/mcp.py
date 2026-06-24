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
def get_logs(
    project_id: str,
    lines: int = 200,
    grep: str | None = None,
    grep_i: bool = False,
    grep_v: bool = False,
    level: str | None = None,
) -> str:
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
    if not logs:
        return "No logs available or container not running."

    if not grep and not level:
        return logs

    # Apply regex filter if grep is specified
    pattern = None
    if grep:
        flags = re.IGNORECASE if grep_i else 0
        try:
            pattern = re.compile(grep, flags)
        except re.error as e:
            return f"Error: Invalid grep regex: {e}"

    # Severity level filter configuration
    SEVERITY_LEVELS = {
        "DEBUG": 10,
        "INFO": 20,
        "WARN": 30,
        "WARNING": 30,
        "ERROR": 40,
        "FATAL": 50,
    }

    target_severity = None
    if level:
        target_severity = SEVERITY_LEVELS.get(level.upper())
        if target_severity is None:
            return f"Error: Invalid log level: {level}"

    LEVEL_PATTERNS = {
        "FATAL": re.compile(r"\bFATAL\b|\[FATAL\]"),
        "ERROR": re.compile(r"\bERROR\b|\[ERROR\]"),
        "WARN": re.compile(r"\bWARN(?:ING)?\b|\[WARN(?:ING)?\]"),
        "INFO": re.compile(r"\bINFO\b|\[INFO\]"),
        "DEBUG": re.compile(r"\bDEBUG\b|\[DEBUG\]"),
    }

    def get_line_level(line):
        for lvl in ["FATAL", "ERROR", "WARN", "INFO", "DEBUG"]:
            if LEVEL_PATTERNS[lvl].search(line):
                return lvl
        return None

    filtered_lines = []
    print_subsequent = level is None

    for line in logs.splitlines():
        # 1. Level Filter evaluation
        if target_severity is not None:
            line_level = get_line_level(line)
            if line_level is not None:
                print_subsequent = SEVERITY_LEVELS[line_level] >= target_severity
                match_level = print_subsequent
            else:
                match_level = print_subsequent
        else:
            match_level = True

        # 2. Grep Filter evaluation
        if match_level:
            if pattern is not None:
                match_grep = bool(pattern.search(line))
                if grep_v:
                    match_grep = not match_grep
            else:
                match_grep = True
        else:
            match_grep = False

        if match_grep:
            filtered_lines.append(line)

    return "\n".join(filtered_lines)


@mcp_server.tool()
def start_project(project_id: str) -> str:
    """Starts the Liferay stack (containers) for a specific project."""
    if not _manager:
        return "Error: Manager not initialized"

    roots = _manager.find_dxp_roots()
    project_path = None
    for r in roots:
        path = r["path"]
        meta = _manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )
        if project_id in (name, path.name):
            project_path = path
            break

    if not project_path:
        return f"Error: Project '{project_id}' not found."

    try:
        _manager.runtime.cmd_run(project_id=project_path.name)
        return f"Success: Started project '{project_id}'."
    except Exception as e:
        return f"Error: Failed to start project '{project_id}': {e}"


@mcp_server.tool()
def stop_project(project_id: str) -> str:
    """Stops the Liferay stack (containers) for a specific project."""
    if not _manager:
        return "Error: Manager not initialized"

    roots = _manager.find_dxp_roots()
    project_path = None
    for r in roots:
        path = r["path"]
        meta = _manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )
        if project_id in (name, path.name):
            project_path = path
            break

    if not project_path:
        return f"Error: Project '{project_id}' not found."

    try:
        _manager.runtime.cmd_stop(project_id=project_path.name)
        return f"Success: Stopped project '{project_id}'."
    except Exception as e:
        return f"Error: Failed to stop project '{project_id}': {e}"


@mcp_server.tool()
def restart_project(project_id: str, service: str | None = None) -> str:
    """Restarts specific services or the entire stack for a project."""
    if not _manager:
        return "Error: Manager not initialized"

    roots = _manager.find_dxp_roots()
    project_path = None
    for r in roots:
        path = r["path"]
        meta = _manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )
        if project_id in (name, path.name):
            project_path = path
            break

    if not project_path:
        return f"Error: Project '{project_id}' not found."

    try:
        _manager.runtime.cmd_restart(project_id=project_path.name, service=service)
        return (
            f"Success: Restarted project '{project_id}' (service: {service or 'all'})."
        )
    except Exception as e:
        return f"Error: Failed to restart project '{project_id}': {e}"


@mcp_server.tool()
def get_config(project_id: str) -> str:
    """Retrieves the properties and configuration metadata for a project."""
    if not _manager:
        return json.dumps({"error": "Manager not initialized"})

    roots = _manager.find_dxp_roots()
    for r in roots:
        path = r["path"]
        meta = _manager.read_meta(path)

        # Sanitize metadata passwords and secrets
        sanitized_meta = {}
        for k, v in meta.items():
            if any(s in k.lower() for s in ["pass", "secret", "token", "key", "auth"]):
                sanitized_meta[k] = "[REDACTED]"
            else:
                sanitized_meta[k] = v

        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )
        if name == project_id:
            config_data = {
                "metadata": sanitized_meta,
            }
            # Try to grab portal-ext.properties if it exists
            portal_ext = path / "common" / "portal-ext.properties"
            if portal_ext.exists():
                content = portal_ext.read_text(errors="ignore")
                props = _manager.config._get_properties(content)
                sanitized_props = {}
                for k, v in props.items():
                    if any(
                        s in k.lower()
                        for s in ["password", "secret", "token", "key", "auth"]
                    ):
                        sanitized_props[k] = "[REDACTED]"
                    else:
                        sanitized_props[k] = v
                config_data["portal-ext"] = sanitized_props  # type: ignore[assignment]

            return json.dumps(config_data, indent=2)

    return json.dumps({"error": f"Project '{project_id}' not found."})


@mcp_server.tool()
def get_cli_help(command: str | None = None) -> str:
    """Retrieves the CLI usage and help manual for LDM commands to prevent flag hallucinations.

    Args:
        command: The specific subcommand (e.g., 'run', 'hydrate', 'logs', 'config') to get help for.
    """
    import io

    from ldm_core.cli import get_parser

    parser, _ = get_parser()
    if not command:
        f = io.StringIO()
        parser.print_help(f)
        return f.getvalue()

    # Find subcommand parser
    subparsers_actions = [
        action
        for action in parser._actions
        if action.__class__.__name__ == "_SubParsersAction"
    ]
    for action in subparsers_actions:
        if command in action.choices:
            sub_parser = action.choices[command]
            f = io.StringIO()
            sub_parser.print_help(f)
            return f.getvalue()

    # If subcommand wasn't found, list available ones
    available = []
    for action in subparsers_actions:
        available.extend(action.choices.keys())
    return f"Error: Command '{command}' not found. Available commands: {', '.join(available)}"


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
