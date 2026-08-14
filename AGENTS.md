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

> Keep this section ≤20 lines. Track high-level release cycles and open issues here.
> For transient, in-flight sub-step tracking, consult `.agent-state.md` (git-ignored).

### Active Pre-Release Cycle: v2.15.28

- **v2.15.28-pre.1**: Verified by downstream e2e testing team (all 9 features passed).
- **v2.15.28-pre.2**: Tagged & released. Bundles fixes for:
  - #1090: Skip local port check for remote `--node` targets.
  - #1091 / #1115: Surface `http_ready` & `http_status` in `--json` outputs.
  - #1092: Deduplicate external-drive warning in `ldm stop`.
  - #1097: Expanded E2E verify script coverage (`verify_e2e_refactor.sh`/`.ps1`).
- #1088 closed (verified DB fallback works end-to-end).

### Open Issues (2)

| # | Title | Type |
|---|-------|------|
| #1117 | Root-owned bind mounts/volumes under `--node` break `--fix-permissions` and license placement (follow-up to closed #1090) | bug |
| #883 | [Upstream] Headless REST API PUT for Site Initializer pages | JIRA |

### Uncommitted In-Flight Work

- None. Tracking PR #1113 remains open across the `v2.15.28-pre.1`/`pre.2` cycle by design (see `.agent-state.md`); awaiting downstream verification before `--promote`.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-14* | *Last Reviewed: 2026-08-14*
