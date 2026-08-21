---
name: ldm-developer
description: Guides and scripts for developing, testing, linting, and releasing Liferay Docker Manager (LDM).
---

# LDM Developer Skill & Runbook

This skill guides you through the standards, commands, and scripts required to develop, verify, and release updates in the Liferay Docker Manager (LDM) codebase.

---

## 1. Environment Constraints

- **Virtual Environment Gate**: You MUST conduct all development, testing, linting, and Git operations within the project's Python virtual environment (`.venv`).

- **Hook Isolation**: Git hooks and pre-commit checks rely on packages installed in the virtual environment. Running operations outside the virtual environment (e.g. using global system Python) will trigger hook failures.

### Environment Layout

The repository root holds more than one virtual environment. They are not interchangeable, and guessing between them is what caused [#1240](https://github.com/peterrichards-lr/liferay-docker-manager/issues/1240):

| Path | Role |
|---|---|
| `.venv` | **Authoritative** for everything that actually runs. `scripts/run_python.sh` (the resolver behind every local pre-commit hook) prefers `.venv/bin/python3`; `lint.sh` resolves `.venv`; `scripts/agent_push.sh` uses `.venv/bin/python3 -m pre_commit`. |
| `.pytest_venv` | Holds the `pre-commit` **console script**, which is why hook *installation* is documented as `.pytest_venv/bin/pre-commit install`. |
| `.smoke_venv`, `.temp_venv` | Transient, created by tooling. Not used by any documented workflow. |

> [!IMPORTANT]
> **`pre-commit` must be invoked as a module from `.venv`**: `.venv/bin/python3 -m pre_commit ...`
>
> The package is installed in `.venv`, but **no `.venv/bin/pre-commit` console script exists**. Commands written in the console-script form fail with `no such file or directory`. The same applies to `pytest`, which is correctly documented throughout as `.venv/bin/python -m pytest`.

---

## 2. Standard Developer Commands

Always run these commands from the repository root:

### Linting and Formatting

```bash
# Auto-format Python files
.venv/bin/ruff format .

# Check and auto-fix simple linting warnings
.venv/bin/ruff check . --fix
```

### Running Unit Tests

```bash
# Run the entire pytest suite (automatically gathers coverage)
.venv/bin/python -m pytest

# Run a specific test file
.venv/bin/python -m pytest ldm_core/tests/test_config.py

# Run a specific test case
.venv/bin/python -m pytest ldm_core/tests/test_config.py -k test_sync_common_assets_cascade_and_important
```

### Pre-commit Verification

```bash
# Run all pre-commit hooks across the codebase (runs Ruff, MyPy, ShellCheck, Pytest, bandit, markdownlint-cli2, etc.)
# NOTE the module form: there is no `pre-commit` console script in .venv -- see Environment Layout in section 1.
.venv/bin/python3 -m pre_commit run --all-files
```

---

## 3. Exit Code Contract

All CLI handler command routines must output consistent exit codes:

- `0`: Success

- `1`: Generic/Validation Error

- `2`: Authentication/Permission Error (e.g. LCP login required)

- `3`: Infrastructure/Data Error (e.g. Backup download failure)

- `4`: Orchestration/Deployment Error

- `126`: Command Invocation Error

---

## 4. Releasing Updates (Automated Script)

Do not manually bump versions or tag releases. Instead, use the automated release script:

```bash
.venv/bin/python scripts/release.py --bump [patch|minor|major|beta]
```

### How the release script works

1. Verifies that the workspace only contains modified version files (`pyproject.toml`, `constants.py`, `CHANGELOG.md`) and documentation files (`.md`). **Any Python source file edits must be committed first.**

2. Bumps the SemVer version in LDM metadata configuration files.

3. Automatically runs all pre-commit quality checks.

4. Commits changes and pushes a new branch (e.g., `release/v2.11.43`).

5. Raises a Pull Request via GitHub CLI (`gh pr create`).

6. Enables auto-merge (`gh pr merge --auto`).

7. Polls until GitHub Actions builds pass and the PR merges.

8. Checks out master locally, pulls changes, tags the release (`v2.11.43`), and pushes the tag to trigger the final GitHub release workflows.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-21* | *Last Reviewed: 2026-08-21*
