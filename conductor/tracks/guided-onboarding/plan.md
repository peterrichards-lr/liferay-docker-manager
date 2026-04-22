# Plan: Guided Onboarding (`ldm init`)

Implementation plan for the guided onboarding experience in LDM v2.5.0.

## Phase 1: Folder Structure & Basic Template

- [ ] Create `references/templates/` directory.
- [ ] Implement `references/templates/basic/manifest.json`.
- [ ] Add boilerplate assets (empty folders) for `basic` template.

## Phase 2: CLI Refactor

- [ ] Update `ldm_core/cli.py` to support `ldm init` arguments (`--template`, `--interactive`).
- [ ] Link `init` command to `WorkspaceHandler.cmd_init`.

## Phase 3: Core Logic (The Template Manager)

- [ ] Implement `TemplateManager` in `ldm_core/handlers/workspace.py`.
- [ ] Implement directory creation and asset copying logic.

## Phase 4: Interactive Prompts

- [ ] Add metadata collection sequence (project name, Liferay version, database).
- [ ] Integrate prompts with the scaffolding logic.

## Phase 5: Documentation Generation

- [ ] Implement `README.md` and `setup.md` generation.
- [ ] Verify template-based documentation injection.

## 🏁 Definition of Done

- [ ] `ldm init` successfully scaffolds a new project from the "Basic" template.
- [ ] All interactive prompts work as expected.
- [ ] The new project passes `ldm doctor`.
- [ ] The new project can be started with `ldm run`.
