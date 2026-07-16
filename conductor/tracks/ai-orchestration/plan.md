# Implementation Plan: AI-Assisted Orchestration (`ldm ai` & `ldm mcp`)

## 1. Objective

Provide intelligent, context-aware troubleshooting and orchestration for Liferay Docker environments using a dual-layer architecture: an open Model Context Protocol (MCP) server for IDE integration, and a built-in CLI chat client (`ldm ai`) for terminal users.

## 2. Dual-Layer Architecture

Instead of tightly coupling LLM API calls directly into the CLI commands, LDM will decouple the "knowledge" from the "chat interface".

### Layer 1: The MCP Server (`ldm mcp`)

LDM will expose a standard MCP JSON-RPC Server over `stdio` or HTTP. This acts as a read-only data layer that safely exposes the LDM environment to any AI tool (like Cursor, Claude Desktop, or Windsurf).

**Exposed MCP Tools:**

- `get_projects()`: Lists all managed workspaces.
- `get_health(project_id)`: Executes `ldm doctor` logic and returns diagnostic JSON.
- `get_logs(project_id, lines=200)`: Retrieves the tail of the Liferay container logs.
- `get_config(project_id)`: Retrieves the project metadata and `portal-ext.properties`.

### Layer 2: The CLI Client (`ldm ai`)

A built-in chat session for Sales Engineers and developers who prefer the terminal.

- It acts as an MCP Client connecting to its own `ldm mcp` server.
- It manages the Gemini API key (`~/.ldmrc`).
- It uses a lightweight REST client to send user queries and the MCP tool outputs to the Gemini 2.5 Flash API.
- It streams the response back to the terminal.

## 3. Key Requirements

- **Interactive Help**: Ask questions like "How do I configure LDAP?" and get LDM-specific instructions.
- **Context-Aware Troubleshooting**: Ask "Why did my server crash?" and the AI uses MCP tools to fetch the logs and diagnose the issue.
- **IDE Portability**: Developers can configure their AI code editors to use `ldm mcp` to troubleshoot client extension issues directly in their IDE.
- **Security**: The MCP server strictly exposes read-only diagnostic tools.

## 4. Implementation Steps

1. **Step 1: The MCP Server (`ldm_core/handlers/mcp.py`)**: Implement the JSON-RPC interface and expose the diagnostic functions as tools.
2. **Step 2: API Integration (`ldm_core/handlers/ai.py`)**: Implement a lightweight REST client for the Gemini API that supports tool calling (function calling).
3. **Step 3: The CLI Wrapper**: Add `ldm mcp` (starts the server) and `ldm ai` (starts the interactive chat session) to `cli.py`.
4. **Step 4: System Prompts**: Define the internal system prompt that instructs Gemini on how to interpret LDM data and Liferay logs.

## 5. Verification & Testing

1. **Test MCP**: Connect an external tool (like the official MCP Inspector) to `ldm mcp` and verify it can list projects and fetch logs.
2. **Test CLI Config**: Configure an API key: `ldm config gemini_api_key YOUR_KEY`.
3. **Test CLI Chat**: Run `ldm ai "My forge project is crashing, can you look at the logs?"`. Verify the CLI automatically invokes the internal MCP tool, fetches the logs, sends them to Gemini, and prints the analysis.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-16* | *Last Reviewed: 2026-07-02*
