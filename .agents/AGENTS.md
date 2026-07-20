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

## Active Documentation Maintenance Rule

After implementing any code change, the agent MUST review the project documentation to determine if updates are needed:

1. **Review and Update**: If a code change requires documentation updates, the agent must update the relevant document(s) AND update both the *Last Updated* and *Last Reviewed* timestamp footer at the bottom of the document. A single code change may require updates to multiple documents.
2. **Review Only**: If a document was reviewed in relation to a change but no content updates were necessary, the agent MUST still update the *Last Reviewed* timestamp footer to reflect the review.
3. **New Documentation**: If no documentation exists around the implemented change, and it makes logical sense to document it, the agent MUST create a new document (with timestamp footers) unless the information can be appropriately added as a new section to an existing document.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-20* | *Last Reviewed: 2026-07-20*
