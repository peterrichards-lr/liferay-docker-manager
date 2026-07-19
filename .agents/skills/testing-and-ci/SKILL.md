---
name: testing-and-ci
description: Activate this skill whenever writing tests, running linters, or committing code.
---

# Testing & CI Rules

## Pre-commit & CI Verification

- **Mandatory Local Pre-commit**: To prevent GitHub Actions CI failures caused by dirty states, you MUST run `. .venv/bin/activate && pre-commit run --all-files` locally before committing and pushing any changes. If you forget, the local git hooks (installed in `.git/hooks`) will intercept your `git commit` and run the linters automatically.
- **Handling Hook Failures**: If `git commit` fails because a hook (like `ruff-format` or `markdownlint`) modified files, you MUST re-stage the modified files (`git add .`) and run `git commit` again. If a hook like `mypy` or `detect-secrets` fails, you MUST fix the underlying code issue, re-stage, and commit again. Never use `--no-verify` to bypass these quality gates.

## Endpoint Protection & Security

- **Mocking System Calls in Tests**: Never execute actual compiled binaries (like `lfr-tunnel`, `ldm`) during unit/integration tests using `subprocess` or `os.system`. All system and binary execution calls MUST be correctly mocked (`@patch("ldm_core.utils.run_command")` or `@patch("subprocess.Popen")`) to prevent triggering corporate endpoint protection tools (e.g., SentinelOne), which may detect these test invocations as malicious activity and aggressively quarantine/delete the binaries and surrounding development tools (like `brew`, `jenv`, etc.).

## Python Virtual Environment (venv)

- **Mandatory Alignment**: All development, testing, linting, and Git operations MUST be conducted within the project's Python virtual environment (`.venv`).
- **Hook Isolation**: Git hooks and pre-commit checks rely on packages installed in the virtual environment. Running operations outside the virtual environment (e.g. using global system Python) will trigger hook failures.
