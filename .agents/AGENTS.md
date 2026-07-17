# Project automation rules

All development, issue backlog prioritization, release workflows, and deployments MUST strictly follow the specifications defined in the [Automation Playbook](../docs/PLAYBOOK.md).

## Scope Sprawl & Anti-Churn Mandate

To prevent unnecessary code churn, sweeping reformatting, and out-of-scope changes, all edits MUST be strictly scoped to the active issue:

- Keep pull requests atomic and limited in size.
- **Bugfixes** (branches starting with `fix/` or `bugfix/`) **MUST NOT** modify more than 10 files. Edits exceeding this limit will trigger a CI failure, requiring a bypass override (`[bypass sprawl]` or `[bypass limit]` in the PR title/description) or splitting the PR into smaller, atomic contributions.
- Do not perform codebase-wide style cleanups or refactoring unless explicitly requested in the issue.

## GitHub Pull Request & Issue Association

- Every Pull Request body MUST contain reference keywords linking to the related issue (e.g. `Closes #XYZ` or `Resolves #XYZ`) to ensure GitHub automatically closes the issues on merge.
- **MANDATORY REQUIREMENT**: PRs will be rejected if they do not include the GitHub issue link. You MUST ensure an issue is created and linked on every single PR you open.

## Pre-commit & CI Verification

- **Mandatory Local Pre-commit**: To prevent GitHub Actions CI failures caused by dirty states, you MUST run `. .venv/bin/activate && pre-commit run --all-files` locally before committing and pushing any changes. If you forget, the local git hooks (installed in `.git/hooks`) will intercept your `git commit` and run the linters automatically.
- **Handling Hook Failures**: If `git commit` fails because a hook (like `ruff-format` or `markdownlint`) modified files, you MUST re-stage the modified files (`git add .`) and run `git commit` again. If a hook like `mypy` or `detect-secrets` fails, you MUST fix the underlying code issue, re-stage, and commit again. Never use `--no-verify` to bypass these quality gates.

## Release & Version Automation

- **Automated Orchestrator**: AI agents MUST never manually bump version strings, modify metadata config files (e.g. `pyproject.toml`, `constants.py`), or create/push git tags. You MUST always use the automated orchestrator script:
  - To bump versions and tag pre-releases:

    ```bash
    python3 scripts/release.py --bump beta
    ```

  - To promote pre-releases to stable releases (must be run from the active release branch):

    ```bash
    python3 scripts/release.py --promote
    ```

## Custom Containers & Multi-Compose Architecture

- **Custom Containers Integration**: When a user requests to run external services (e.g., WordPress, Node.js, Web Crawler) alongside Liferay, use the LDM `custom_containers` feature rather than altering the native LDM Python orchestration.
- **Multi-Compose Decoupled Networks**: For enterprise multi-compose decoupled architecture setups, always refer to the reference templates in `docker-compose-templates/` to understand the standard `shared-search-net` and `shared-crawl-net` external networking boundaries. Do not invent new bridging architectures if these templates suffice.

## Endpoint Protection & Security

- **Mocking System Calls in Tests**: Never execute actual compiled binaries (like `lfr-tunnel`, `ldm`) during unit/integration tests using `subprocess` or `os.system`. All system and binary execution calls MUST be correctly mocked (`@patch("ldm_core.utils.run_command")` or `@patch("subprocess.Popen")`) to prevent triggering corporate endpoint protection tools (e.g., SentinelOne), which may detect these test invocations as malicious activity and aggressively quarantine/delete the binaries and surrounding development tools (like `brew`, `jenv`, etc.).

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-17* | *Last Reviewed: 2026-07-17*
