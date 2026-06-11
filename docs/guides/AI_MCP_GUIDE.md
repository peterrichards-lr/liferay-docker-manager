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
* `get_config`: Retrieves properties, configuration metadata, and environment details for a project.

**Usage:**

You can start the MCP server natively via:

```bash
ldm mcp
```

*(Note: Since it communicates over `stdio`, you configure your MCP client to invoke `ldm mcp` rather than running it interactively).*
