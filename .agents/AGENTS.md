# Project automation rules

All development, issue backlog prioritization, release workflows, and deployments MUST strictly follow the specifications defined in the [Automation Playbook](file:///Users/peterrichards/dev/repos/liferay-docker-manager/docs/PLAYBOOK.md).

## Scope Sprawl & Anti-Churn Mandate

To prevent unnecessary code churn, sweeping reformatting, and out-of-scope changes, all edits MUST be strictly scoped to the active issue:

- Keep pull requests atomic and limited in size.
- **Bugfixes** (branches starting with `fix/` or `bugfix/`) **MUST NOT** modify more than 10 files. Edits exceeding this limit will trigger a CI failure, requiring a bypass override (`[bypass sprawl]` or `[bypass limit]` in the PR title/description) or splitting the PR into smaller, atomic contributions.
- Do not perform codebase-wide style cleanups or refactoring unless explicitly requested in the issue.

## GitHub Pull Request & Issue Association

- Every Pull Request body MUST contain reference keywords linking to the related issue (e.g. `Closes #XYZ` or `Resolves #XYZ`) to ensure GitHub automatically closes the issues on merge.

## Pre-commit & CI Verification

- **Mandatory Local Pre-commit**: To prevent GitHub Actions CI failures caused by dirty states, you MUST run `. .venv/bin/activate && pre-commit run --all-files` locally before committing and pushing any changes.
- **Documentation Timestamps Awareness**: The `bump-docs-timestamps` hook will automatically modify the `Last Updated` footers of any `*.md` files if there have been changes. If this hook modifies many `.md` files that are unrelated to your active task, DO NOT stage and commit these automated timestamp changes alongside your logic changes if it causes your PR to exceed the 10-file limit. Instead, put the `.md` timestamp bumps in a separate `chore/` PR to keep your main `fix/` PR strictly under the limit.
