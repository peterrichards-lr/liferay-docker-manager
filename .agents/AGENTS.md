# Project automation rules

All development, issue backlog prioritization, release workflows, and deployments MUST strictly follow the specifications defined in the [Automation Playbook](file:///Users/peterrichards/dev/repos/liferay-docker-manager/docs/PLAYBOOK.md).

## Scope Sprawl & Anti-Churn Mandate

To prevent unnecessary code churn, sweeping reformatting, and out-of-scope changes, all edits MUST be strictly scoped to the active issue:

- Keep pull requests atomic and limited in size.
- **Bugfixes** (branches starting with `fix/` or `bugfix/`) **MUST NOT** modify more than 10 files. Edits exceeding this limit will trigger a CI failure, requiring a bypass override (`[bypass sprawl]` or `[bypass limit]` in the PR title/description) or splitting the PR into smaller, atomic contributions.
- Do not perform codebase-wide style cleanups or refactoring unless explicitly requested in the issue.
