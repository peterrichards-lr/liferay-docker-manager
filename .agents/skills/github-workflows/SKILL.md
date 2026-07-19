---
name: github-workflows
description: Activate this skill whenever opening Pull Requests, creating branches, or responding to GitHub issues.
---

# GitHub Workflows

## Scope Sprawl & Anti-Churn Mandate

To prevent unnecessary code churn, sweeping reformatting, and out-of-scope changes, all edits MUST be strictly scoped to the active issue:

- Keep pull requests atomic and limited in size.
- **Bugfixes** (branches starting with `fix/` or `bugfix/`) **MUST NOT** modify more than 10 files. Edits exceeding this limit will trigger a CI failure, requiring a bypass override (`[bypass sprawl]` or `[bypass limit]` in the PR title/description) or splitting the PR into smaller, atomic contributions.
- Do not perform codebase-wide style cleanups or refactoring unless explicitly requested in the issue.

## GitHub Pull Request & Issue Association

- Every Pull Request body MUST contain reference keywords linking to the related issue (e.g. `Closes #XYZ` or `Resolves #XYZ`) to ensure GitHub automatically closes the issues on merge.
- **MANDATORY REQUIREMENT**: PRs will be rejected if they do not include the GitHub issue link. You MUST ensure an issue is created and linked on every single PR you open.
