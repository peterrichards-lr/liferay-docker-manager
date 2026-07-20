---
name: testing-and-ci
description: Activate this skill whenever writing tests, running linters, or committing code.
---

# Testing & CI Rules

## Pre-commit & CI Verification

- **Mandatory Pre-commit Installation**: The agent MUST proactively verify that `pre-commit` hooks are installed locally (i.e. `.git/hooks/pre-commit` exists). If they are missing, you MUST run `.pytest_venv/bin/pre-commit install` (or `ldm dev-setup`) before attempting to commit code. This ensures that the local git hooks will intercept `git commit` and run linters like `ruff format` automatically, preventing unformatted code from slipping through and failing the CI Quality Gate.
- **Mandatory Local Pre-commit & Tests**: I will make sure to strictly run `pre-commit run --all-files` and `pytest` locally on all future changes before pushing! To prevent GitHub Actions CI failures caused by dirty states, you MUST run `. .venv/bin/activate && pre-commit run --all-files && pytest` locally before committing and pushing any changes.
- **Handling Hook Failures**: If `git commit` fails because a hook (like `ruff-format` or `markdownlint`) modified files, you MUST re-stage the modified files (`git add .`) and run `git commit` again. If a hook like `mypy` or `detect-secrets` fails, you MUST fix the underlying code issue, re-stage, and commit again. Never use `--no-verify` to bypass these quality gates.

## Endpoint Protection & Security

- **Mocking System Calls in Tests**: Never execute actual compiled binaries (like `lfr-tunnel`, `ldm`) during unit/integration tests using `subprocess` or `os.system`. All system and binary execution calls MUST be correctly mocked (`@patch("ldm_core.utils.run_command")` or `@patch("subprocess.Popen")`) to prevent triggering corporate endpoint protection tools (e.g., SentinelOne), which may detect these test invocations as malicious activity and aggressively quarantine/delete the binaries and surrounding development tools (like `brew`, `jenv`, etc.).

## Python Virtual Environment (venv)

- **Mandatory Alignment**: All development, testing, linting, and Git operations MUST be conducted within the project's Python virtual environment (`.venv`).
- **Hook Isolation**: Git hooks and pre-commit checks rely on packages installed in the virtual environment. Running operations outside the virtual environment (e.g. using global system Python) will trigger hook failures.
