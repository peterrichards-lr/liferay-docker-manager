# Implementation Plan: AI-Assisted Orchestration (`ldm ai`)

## 1. Objective

Integrate a specialized AI handler (Gemini-powered) to provide interactive help, troubleshooting, and configuration generation within LDM.

## 2. Key Requirements

- **Interactive Help**: Ask questions like "How do I configure LDAP?" and get LDM-specific instructions.
- **Context-Aware Troubleshooting**: Automatically inject `ldm doctor` reports and project metadata into AI queries.
- **Recipe Generation**: Ask the AI to generate `portal-ext.properties` or `LCP.json` configurations based on natural language descriptions.
- **Zero-Copy Support**: Directly fix small configuration issues identified by the AI (with user confirmation).

## 3. Technical Design

### AI Handler (`ldm_core/handlers/ai.py`)

- Implement a `GeminiClient` that communicates with the Google Gemini API.
- Use a dedicated prompt template that includes LDM architectural mandates and the current project state.
- Implement "Context Injection" to include `ldm doctor` output, metadata, and logs in the prompt.

### CLI Update (`ldm_core/cli.py`)

- Add `ldm ai` command.
- Arguments:
  - `query`: The natural language question or request.
  - `--doctor`: Explicitly include the full doctor report (default: summary).
  - `--project`: Focus the AI on a specific project's context.

### Security & Privacy

- **Scrubbing**: Automatically remove sensitive information (passwords, tokens, custom URLs) from the context before sending to the AI.
- **API Key Management**: Store the Gemini API key in the user's LDM config (`~/.ldm/config.json`).

## 4. Implementation Steps

1. **Step 1: API Integration**: Implement the basic `GeminiClient` and API key management.
2. **Step 2: Context Gathering**: Create the context injection logic that collects metadata, doctor output, and logs.
3. **Step 3: Prompt Engineering**: Develop and refine the "LDM Expert" prompt template.
4. **Step 4: Interactive Command**: Implement the `ldm ai` interactive shell.
5. **Step 5: Safe Fixes**: Implement the logic to apply AI-suggested file changes (with a diff view and confirmation).

## 5. Verification & Testing

1. Configure an API key: `ldm config gemini_api_key YOUR_KEY`.
2. Ask a question: `ldm ai "How do I enable SSL for my project?"`.
3. Ask for a fix: `ldm ai "My Liferay container is crashing, why?"` (verify it analyzes the logs).
4. Request a configuration: `ldm ai "Generate a portal-ext.properties for a 2-node cluster."`.
5. Verify that no sensitive data is leaked in the AI context.
