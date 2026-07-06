---
name: ensure-agents-md
description: Run FIRST before any task when workspace root lacks AGENTS.md. Creates AGENTS.md tailored to the Python environment (pyproject.toml, .venv, pytest).
---

# Ensure AGENTS.md (Bootstrap)

Before any other work, the agent MUST:

1. Check whether `AGENTS.md` exists at the workspace root.
2. If it exists, skip this skill entirely.
3. If it does not exist, create `AGENTS.md` based on the Python environment.

## Steps

1. **Verify Environment**: Check `pyproject.toml` and `.venv` existence to confirm this is the Liferay Docker Manager Python project.
2. **Generate AGENTS.md**: Create `AGENTS.md` with instructions for AI coding agents to always use `.venv/bin/` for commands like `ruff` or `pytest`, and reference the `ldm_developer` skill.
3. **Inform User**: Tell the user that the bootstrap is complete, and proceed with their request.
