# LDM — AI Agent Context

> This is the single source of truth for any AI coding agent working on
> this repository. Provider-specific files (`GEMINI.md`, `CLAUDE.md`,
> `.gemini/gemini.md`) redirect here. Do not duplicate context in those
> files.

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
| Developer runbook | `.agents/skills/ldm-developer/SKILL.md` |
| Upstream JIRA tracking | label an issue `JIRA`; the `github-jira-sync` plugin raises and tracks it |

`.claude/skills` is a symlink to `.agents/skills` (LDM-#1378). Claude Code only
discovers project skills under `.claude/skills/`, so without it none of the
above are auto-activated -- an agent loads a skill only if it remembers to
consult this table, which is how LDM-#1288 happened. Keep `.agents/` as the
canonical location so the arrangement stays provider-agnostic; the symlink is a
pointer, not a second copy.

**Windows caveat.** `.claude/skills` is the first symlink tracked in this
repository. On Windows without Developer Mode or `git config core.symlinks true`,
git materialises it as a plain text file containing `../.agents/skills` rather
than a link. Nothing breaks -- no build or CI job reads that path -- but skills
go undiscovered again, silently. Windows contributors should confirm
`git config core.symlinks` reports `true` before relying on skill activation.

Each skill directory name MUST match its frontmatter `name:`. Discovery keys on
the directory, so a mismatch registers the skill under the directory name and
silently ignores the declared one.

Reference the skills from this table as markdown links, **not** `@` imports.
`@` eagerly loads a file into every session, and these seven total ~56 KB
(~14k tokens) regardless of the task at hand.

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

"The project documentation" is not only `docs/*.md`. The man page
(`ldm_core/resources/ldm.1`) and the CLI help strings are user-facing too, and
both ship inside the binary. See *Documentation Surfaces* in
`.agents/skills/ldm-developer/SKILL.md` for the full list and which guard
covers each -- the man page went four minor releases out of date because it
has no timestamp footer and so never looked like documentation (LDM-#1482).

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

### Pragmatism & Velocity Principles

To maintain high developer velocity while preserving software quality:

- **Surgical Fixes & Minimal Diffs**: Focus strictly on the requested bug or feature. Do not refactor surrounding working code or re-architect functional logic unless explicitly asked.
- **Preserve CLI & Flag Semantics**: Never alter or break existing CLI flags, combinations, or user habits under the guise of "intent vs mechanism" or "semantic purity."
- **Verify Claims Historically**: Before asserting that a feature "never worked" or "is broken," inspect past commit history (`git log -S`) to avoid misdiagnosing a recent regression as an initial design flaw.
- **No Unsolicited Audit Cascades**: Do not create cascades of secondary micro-debt issues during routine bug fixes unless a full audit was explicitly requested.

### Delegating to Subagents

This section is in `AGENTS.md` rather than a skill on purpose: the decision to
delegate is taken **before** any skill loads, so a rule kept in a skill cannot
reach it.

Each rule below exists because its absence cost something measurable.

- **Delegate for fan-out, not for a task you can name.** If you already know the
  file and the symbol, read it. Delegation earns its cost when the answer needs
  sweeping many files and you want the conclusion rather than the file dumps.

- **Brief the question and the method, not the topic.** State the artefact you
  want back and the cheapest way to produce it. A brief reading "check the shell
  scripts for X" produced a 16-minute full test-suite run for a question that
  needed a static scan of two files.

- **State the prohibition explicitly.** A subagent will run the expensive thing
  if nothing forbids it. `Read-only. Do not run pytest, pre-commit, or any
  container.` is one line and would have saved that run.

- **One expensive resource at a time.** Never dispatch a subagent that may run
  the suite, containers or the E2E while this session is running one. The suite
  is CPU-bound and takes ~16 minutes; Docker is a single shared daemon, so two
  runs contend for ports and container names, not merely for CPU. Two concurrent
  suites also triggered repeated out-of-memory kills of background tasks.

- **Ask it to REFUTE, not to confirm.** When a fix rests on a diagnosis, have a
  subagent try to break the diagnosis before you build on it: number the steps,
  ask for CONFIRMED / REFUTED / CANNOT DETERMINE per step with `file:line`
  evidence, and say that a refutation is the valuable outcome. Hand it the
  evidence rather than your conclusion -- an agent given a conclusion tends to
  confirm it. This found LDM-#1987 after three fixes built on unverified
  readings, and corrected three overstatements in the diagnosis it confirmed.

- **Do not poll a background task.** Check once and get on with something else;
  the harness notifies on completion. Repeated blocking checks produce nothing
  and look like a hung session.

- **Isolate anything that writes.** Use a git worktree so a subagent cannot
  disturb this session's working tree. Note a fresh worktree has no ignored
  files, so `ldm_core/ui_colors.py` is absent and the ANSI tests fail on
  invisible characters -- generate it with `scripts/sync_colors.py` or tell the
  subagent the suite is unavailable there.

## Current Work State

Status updates, active release cycles, open-issue tracking, and any other
transient/in-flight information live exclusively in `.agent-state.md`
(git-ignored). **Read that file upon starting work to resume seamlessly
across AI providers.** This file (`AGENTS.md`) intentionally carries no
status content of its own -- it is a routing/conventions file only, and
should not need to change as work progresses.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-09-28* | *Last Reviewed: 2026-09-28*
