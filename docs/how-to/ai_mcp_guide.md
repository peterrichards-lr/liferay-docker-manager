# Liferay Docker Manager (LDM) AI & MCP Guide

LDM now includes intelligent AI-Assisted Orchestration, powered by Google Gemini, and a Model Context Protocol (MCP) server. These tools allow you to converse directly with an AI assistant that can seamlessly access your Liferay Docker environments, analyze configurations, and diagnose issues.

## 1. Prerequisites

To use the AI features, you will need a free Google Gemini API Key.

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Generate an API Key.
3. The first time you run an `ldm ai` command, you will be prompted for this key, and it will be securely saved to your local config.

## 2. Using the LDM AI CLI (`ldm ai`)

The `ldm ai` command provides a built-in chatbot that can troubleshoot your environments for you.

**Example Use Cases:**

* "Why is my liferay-custom project failing to start?"
* "Show me the database password for the latest project I created."
* "Check the logs for out of memory errors."

**Usage:**

```bash
ldm ai "Analyze the logs for my project and tell me why it crashed."
```

The AI will automatically invoke local MCP tools (like fetching logs or reading `portal-ext.properties`), analyze the output, and respond with a solution.

## 3. Using the LDM MCP Server (`ldm mcp`)

If you prefer to use your own MCP-compatible client (like Claude Desktop, Cursor, or the Anthropic CLI), you can connect it directly to LDM's Diagnostic Server.

**Available Tools:**

* `get_projects`: Lists all managed Liferay Docker environments and their status.
* `get_logs`: Retrieves recent logs for a specific Liferay project container.
  * **Arguments:**
    * `project_id` (string, required): The project ID or container name.
    * `lines` (integer, optional, default: `200`): Number of trailing log lines to retrieve.
    * `grep` (string, optional): A regular expression pattern to filter logs.
    * `grep_i` (boolean, optional, default: `false`): Run a case-insensitive regex match.
    * `grep_v` (boolean, optional, default: `false`): Invert regex match logic (exclude matches).
    * `level` (string, optional): Severity threshold filter (e.g., `WARN`, `ERROR`, `FATAL`). Shows lines matching or exceeding this level, preserving stack traces.
* `start_project`: Starts the Liferay stack (containers) for a specific project.
  * **Arguments:**
    * `project_id` (string, required): The project ID or container name.
* `stop_project`: Stops the Liferay stack (containers) for a specific project.
  * **Arguments:**
    * `project_id` (string, required): The project ID or container name.
* `restart_project`: Restarts specific services or the entire stack for a project.
  * **Arguments:**
    * `project_id` (string, required): The project ID or container name.
    * `service` (string, optional): The name of a service to restart (e.g., `liferay`, `db`). Restarts the entire stack if omitted.
* `get_config`: Retrieves properties, configuration metadata, and environment details for a project.
* `get_cli_help`: Retrieves LDM CLI subcommand manual and flags usage description to prevent command option hallucinations.
  * **Arguments:**
    * `command` (string, optional): A specific LDM subcommand (e.g., `run`, `hydrate`, `logs`, `config`) to retrieve usage and flags for. Returns overall LDM CLI help if omitted.

**Usage:**

You can start the MCP server natively via:

```bash
ldm mcp
```

*(Note: Since it communicates over `stdio`, you configure your MCP client to invoke `ldm mcp` rather than running it interactively).*

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-22* | *Last Reviewed: 2026-07-02*
