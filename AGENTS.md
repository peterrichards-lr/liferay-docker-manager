# LDM — AI Agent Context

> This is the single source of truth for any AI coding agent working on
> this repository. Provider-specific files (`GEMINI.md`, `CLAUDE.md`)
> redirect here. Do not duplicate context in those files.

## Project Identity

- **Name**: Liferay Docker Manager (LDM)
- **Language**: Python 3.10+ (CLI + FastMCP web dashboard)
- **Package**: `ldm_core` — distributed as PyInstaller binary + PyPI
- **Docs**: `docs/` (MkDocs site), `README.md`

## Conventions & Guardrails

All architecture mandates and workflow conventions are defined in modular
skill files. **Do not duplicate them in this file.** Read the relevant
skill before starting work:

| Concern | Source |
|---------|--------|
| Architecture (volumes, exit codes, infra) | `.agents/skills/ldm-architecture/SKILL.md` |
| Testing & CI quality gates | `.agents/skills/testing-and-ci/SKILL.md` |
| Release orchestration | `.agents/skills/release-orchestration/SKILL.md` |
| GitHub workflows & PR scope | `.agents/skills/github-workflows/SKILL.md` |
| Developer runbook | `.agents/skills/ldm_developer/SKILL.md` |
| Upstream JIRA tracker | `.agents/skills/jira_tracker/SKILL.md` |

## Global Rules

### Documentation Maintenance

After implementing any code change, review the project documentation to
determine if updates are needed:

- **Review and Update**: If a code change requires documentation updates,
  update the relevant document(s) AND update both the *Last Updated* and
  *Last Reviewed* timestamp footer at the bottom of the document.
- **Review Only**: If a document was reviewed in relation to a change but
  no content updates were necessary, still update the *Last Reviewed*
  timestamp footer to reflect the review.
- **New Documentation**: If no documentation exists around the implemented
  change, and it makes logical sense to document it, create a new document
  (with timestamp footers) unless the information can be appropriately
  added as a new section to an existing document.

### Technical Debt Tracking

When encountering any of the following categories of technical debt during
a task, record it by creating a GitHub Issue with the `tech-debt` label:

- Code Smells, Duplication, Over-complexity, Fragile Coupling
- Missing Safety Guards, Missing Tests, Security Hygiene
- Deprecated Patterns, Config Drift, Documentation Debt

Include the file path, the specific nature of the debt, and a brief
proposed remediation. Immediate resolution is not required — the primary
goal is to ensure the debt is recorded in the backlog.

### No Assumptions (Anti-Hallucination)

Any technical statement, explanation, or conclusion MUST be strictly based
on actual, referenceable code or documentation in this repository. Do not
make blind assumptions about how systems behave without verifying via
search, reading the code, or consulting this file and the skill modules.

## Current Work State

> Keep this section ≤20 lines. Track only **active, in-flight work**.
> Completed items belong in git history and CHANGELOG.md.

### Active Pre-Release Cycle: v2.15.28

- **v2.15.28-pre.1** tagged Aug 14 — awaiting downstream verification.
- Verification request sent to dependent project agents (9 fixes bundled).
- Feedback needed on: #1090 (--node gaps), #1091 (health signals),
  #1092 (triple warning repro), #1088 (fragment PUT fallback).

### Open Issues (6)

| # | Title | Type |
|---|-------|------|
| #1092 | `ldm stop` prints external-drive warning 3× | bug |
| #1091 | Health/readiness signals disagree with reality | bug |
| #1090 | `--node` inconsistently wired across commands | bug |
| #1088 | Fragment PUT patch always 400s | bug |
| #1097 | Add synthetic CX deploy check to verify_e2e | tech-debt |
| #883 | [Upstream] Headless REST API PUT for Site Initializer pages | JIRA |

### Uncommitted In-Flight Work

- `ldm_core/handlers/base.py` (+20): Skip local port-check for remote
  `--node` targets (#1090)
- `ldm_core/tests/test_base.py` (+30): Tests for above

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-14* | *Last Reviewed: 2026-08-14*
