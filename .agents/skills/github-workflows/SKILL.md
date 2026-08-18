---
name: github-workflows
description: Activate this skill whenever opening Pull Requests, creating branches, or responding to GitHub issues.
---

# GitHub Workflows

## Scope Sprawl & Anti-Churn Mandate

To prevent unnecessary code churn, sweeping reformatting, and out-of-scope changes, all edits MUST be strictly scoped to the active issue:

- Keep pull requests atomic and limited in size.
- **Bugfixes** (branch starting with `fix/`, `fix-`, `bugfix/`, or `bugfix-`, or a PR title starting with `fix:`/`fix(scope):`/`bugfix:`/`bugfix(scope):`/`bug:`) **MUST NOT** modify more than 10 files. Edits exceeding this limit will trigger a CI failure (`pr-sprawl-check`), requiring a bypass override (`[bypass sprawl]` or `[bypass limit]` in the PR title/description) or splitting the PR into smaller, atomic contributions.
- Do not perform codebase-wide style cleanups or refactoring unless explicitly requested in the issue.

## GitHub Pull Request & Issue Association

- **MANDATORY REQUIREMENT, CI-enforced**: the `issue-link-check` workflow (`.github/workflows/issue-link-check.yml`) fails any PR missing a `Closes/Fixes/Resolves #N` reference in its title or body. If a change is genuinely too trivial to warrant a tracked issue, add the `no-issue-needed` label instead of skipping the link -- do not just omit it and hope the check doesn't run.

## Sub-Issue Lifecycle in Pre-Release Cycles

Because release-tracking PRs stay open across multiple `--bump beta` iterations before final promotion:

- **Mandatory Issue Linking**: Every release bump created for a tracked issue or epic MUST pass `--issue <number>` to `scripts/release.py` (or specify `Closes #N` in the PR body). The `no-issue-needed` label is strictly forbidden when implementing tracked issues.
- **Immediate Closure Upon Delivery**: Once a sub-issue's implementation is committed, tagged in a pre-release candidate, and verified, the agent MUST comment on the issue and explicitly close it via `gh issue close <issue-number> --reason "completed"` during that turn.
- Do not leave verified sub-issues open waiting for the parent tracking PR to merge to `master`.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-18* | *Last Reviewed: 2026-08-18*
