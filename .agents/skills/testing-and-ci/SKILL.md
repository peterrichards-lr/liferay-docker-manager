---
name: testing-and-ci
description: Activate this skill whenever writing tests, running linters, or committing code.
---

# Testing & CI Rules

## Pre-commit & CI Verification

- **Mandatory Pre-commit Installation**: The agent MUST proactively verify that `pre-commit` hooks are installed locally (i.e. `.git/hooks/pre-commit` exists). If they are missing, you MUST run `.pytest_venv/bin/pre-commit install` (or `ldm dev-setup`) before attempting to commit code. This ensures that the local git hooks will intercept `git commit` and run linters like `ruff format` automatically, preventing unformatted code from slipping through and failing the CI Quality Gate.
- **Strict Mechanical Enforcement (Agent Push)**: As an AI agent, you are STRICTLY PROHIBITED from using `git commit` or `git push` directly in the terminal to prevent bypassing local CI validation gates via `--no-verify`. You MUST ONLY use the `./scripts/agent_push.sh "<commit message>"` wrapper script. This script mechanically forces the execution of `pre-commit run --all-files` and `pytest` before committing, ensuring that formatting errors and test regressions are caught locally before poisoning the CI pipeline.
- **Handling Hook Failures**: The `agent_push.sh` script will automatically stage files and retry if hooks simply auto-format code (e.g. `ruff format`). However, if a hook like `mypy` or `detect-secrets` fails, or if PyTest fails, the script will abort. You MUST fix the underlying code issue and re-run `./scripts/agent_push.sh`.
- **Handling Secrets Baseline Shifts**: The `detect-secrets` hook will fail in CI if line numbers for existing tracked secrets shift due to code changes above them (e.g., adding lines to `ci.yml`). When making structural changes or adding lines to files tracked in `.secrets.baseline`, you MUST proactively run `.venv/bin/pre-commit run detect-secrets --all-files` (or manually patch the line numbers in `.secrets.baseline`) and commit the updated baseline file to prevent CI cascade failures.

## Endpoint Protection & Security

- **Mocking System Calls in Tests**: Never execute actual compiled binaries (like `lfr-tunnel`, `ldm`) during unit/integration tests using `subprocess` or `os.system`. All system and binary execution calls MUST be correctly mocked (`@patch("ldm_core.utils.run_command")` or `@patch("subprocess.Popen")`) to prevent triggering corporate endpoint protection tools (e.g., SentinelOne), which may detect these test invocations as malicious activity and aggressively quarantine/delete the binaries and surrounding development tools (like `brew`, `jenv`, etc.).

## Python Virtual Environment (venv)

- **Mandatory Alignment**: All development, testing, linting, and Git operations MUST be conducted within the project's Python virtual environment (`.venv`).
- **Hook Isolation**: Git hooks and pre-commit checks rely on packages installed in the virtual environment. Running operations outside the virtual environment (e.g. using global system Python) will trigger hook failures.
