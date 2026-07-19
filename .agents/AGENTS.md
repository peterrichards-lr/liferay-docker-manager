# Project Automation Rules (Routing Index)

All development, issue backlog prioritization, release workflows, and deployments MUST strictly follow the modular skills defined in this project.

This file serves as a routing index to prevent cognitive overload. Before executing complex tasks, review the relevant `SKILL.md` from the list below:

- **[GitHub Workflows & Scope Management](./skills/github-workflows/SKILL.md)**
  *Activate this skill whenever opening Pull Requests, creating branches, or responding to GitHub issues.*
  (Contains PR rules, issue linking mandates, and anti-churn scope sprawl limits).

- **[Testing & CI Quality Gates](./skills/testing-and-ci/SKILL.md)**
  *Activate this skill whenever writing tests, running linters, or committing code.*
  (Contains pre-commit hook rules, endpoint protection mocking constraints, and Python venv enforcement).

- **[Release Orchestration](./skills/release-orchestration/SKILL.md)**
  *Activate this skill whenever preparing a release, bumping versions, or creating tags.*
  (Contains automated orchestrator script constraints and pre-release gates).

- **[LDM Architecture Mandates](./skills/ldm-architecture/SKILL.md)**
  *Activate this skill whenever designing new features, modifying Docker compose logic, or interacting with Liferay environments.*
  (Contains volume strategies, infrastructure enforcement, custom containers logic, and exit code standards).

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-19* | *Last Reviewed: 2026-07-19*
