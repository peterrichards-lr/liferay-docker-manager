# Track: Virtual Environment Context Gating (`venv-context-gating`)

## Overview

Prevent developers from committing code, running tests, or performing environment checks outside the project's Python virtual environment (`.venv`). This enforces dependency isolation, alignment of git hooks, and consistent local formatting checks.

## Objectives

1. **System & Venv Probing**: Implement a robust check in `ldm doctor` that verifies if the tool is being executed inside the project's Python virtual environment.
2. **Onboarding Integration**: Update the onboarding logic (`ldm dev-setup`) to automatically confirm and prompt virtual environment status.
3. **Graceful Warnings**: Display clear, formatted warning blocks with copy-paste instructions for virtual environment activation if execution is done globally.

## Implementation Plan

### Phase 1: Environment Verification Logic

- Add a check in `ldm_core/handlers/diagnostics.py` (e.g., `check_venv`):
  - Probes `sys.prefix` against `sys.base_prefix` or looks for the `.venv` directory in the project root.
  - Returns `HEALTHY` (active `.venv`), `WARNING` (running in default Python but `.venv` exists), or `CRITICAL` (no `.venv` created at all).
  - Provide a clear diagnostic fix suggestion: `source .venv/bin/activate` or `python3 liferay_docker.py dev-setup`.

### Phase 2: CLI Integration & UI Warning

- Connect this check into `ldm doctor`.
- If the system is not inside a virtualenv, output:
  - `[WARN] Python Virtual Environment (Not Activated)`
  - Provide a Tip: `💡 Run: source .venv/bin/activate`

### Phase 3: Unit Testing

- Add test cases verifying `check_venv` behaves correctly under mock conditions (mocking `sys.prefix` and `sys.base_prefix`).

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-07* | *Last Reviewed: 2026-07-02*
