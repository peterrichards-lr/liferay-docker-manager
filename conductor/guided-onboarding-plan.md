# Implementation Plan: Guided Onboarding (`ldm init`)

## 1. Objective

Simplify the creation of new LDM projects through a guided CLI experience (`ldm init`) that provides templates for common Liferay use cases.

## 2. Key Requirements

- **Interactive Prompts**: Ask the user for project name, Liferay version, database type, and search mode.
- **Template Selection**: Allow users to choose from pre-defined templates (e.g., "Empty Workspace," "Commerce Demo," "Headless React").
- **Asset Injection**: Automatically create folders (`client-extensions/`, `deploy/`, `osgi/configs/`) and populate them with standard boilerplate.
- **Auto-Documentation**: Generate a project-specific `README.md` and `setup.md` based on the selected template.

## 3. Technical Design

### CLI Update (`ldm_core/cli.py`)

- Add `ldm init [project]` command.
- Arguments:
  - `--template`: Optional template name.
  - `--interactive`: Force prompt-based setup (default).

### Handler Logic (`ldm_core/handlers/workspace.py`)

- Implement `cmd_init(project_id, template=None)`.
- Use `UI.ask_choices` and `UI.ask_text` for user input.
- Create a `TemplateManager` to handle copying assets from `references/templates/`.

### Template Storage (`references/templates/`)

- Organize templates as subfolders:
  - `references/templates/basic/`
  - `references/templates/commerce/`
  - `references/templates/headless/`
- Each template should contain a `manifest.json` describing its structure and required files.

## 4. Implementation Steps

1. **Step 1: Folder Structure**: Create `references/templates/` and define the "Basic" template structure.
2. **Step 2: CLI Wiring**: Add the `init` subparser to `cli.py` and link it to the handler.
3. **Step 3: Core Logic**: Implement the directory creation and file copying logic in `WorkspaceHandler`.
4. **Step 4: Interactive Prompts**: Add the prompt sequence to gather metadata before project creation.
5. **Step 5: Documentation Generation**: Create a simple template-based generator for `README.md`.

## 5. Verification & Testing

1. Run `ldm init my-new-project` and follow the prompts.
2. Verify that the folder structure matches the selected template.
3. Verify that the `.liferay-docker.meta` file is correctly populated.
4. Verify that running `ldm run` in the new project works without further manual configuration.
