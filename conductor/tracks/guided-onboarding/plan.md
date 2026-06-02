# Track Implementation Plan: Guided Onboarding (`ldm init`)

This track focuses on implementing a guided, template-based setup experience for new LDM projects in LDM v2.5.0.

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

## 4. Implementation Steps & Status Checklist

### Phase 1: Folder Structure & Basic Template

- [ ] Create `references/templates/` directory.
- [ ] Implement `references/templates/basic/manifest.json`.
- [ ] Add boilerplate assets (empty folders) for `basic` template.

### Phase 2: CLI Refactor

- [ ] Update `ldm_core/cli.py` to support `ldm init` arguments (`--template`, `--interactive`).
- [ ] Link `init` command to `WorkspaceHandler.cmd_init`.

### Phase 3: Core Logic (The Template Manager)

- [ ] Implement `TemplateManager` in `ldm_core/handlers/workspace.py`.
- [ ] Implement directory creation and asset copying logic.

### Phase 4: Interactive Prompts

- [ ] Add metadata collection sequence (project name, Liferay version, database).
- [ ] Integrate prompts with the scaffolding logic.

### Phase 5: Documentation Generation

- [ ] Implement `README.md` and `setup.md` generation.
- [ ] Verify template-based documentation injection.

---

## 5. Verification & Testing (Definition of Done)

- [ ] Run `ldm init my-new-project` and follow the prompts.
- [ ] Verify that the folder structure matches the selected template (e.g., scaffolds from "Basic" template).
- [ ] Verify that the `.liferay-docker.meta` file is correctly populated.
- [ ] Verify that the new project passes `ldm doctor`.
- [ ] Verify that running `ldm run` in the new project works without further manual configuration.
