# Gemini Agent Redirect

> [!IMPORTANT]
> **This file exists solely for Gemini/Antigravity auto-discovery.**
> The canonical AI agent context for this repository is
> [`AGENTS.md`](../AGENTS.md). All rules, conventions, and active work
> state are maintained there as the single source of truth.
>
> **Do not duplicate context here.** Read and follow `AGENTS.md` instead.
> This keeps the project AI-provider agnostic and avoids maintaining
> state in multiple locations.

## Provider-Specific Process (Gemini / Antigravity only)

These are interface-level workflow preferences for this provider. They **add
to** the shared rules `AGENTS.md` routes to; they never override them.

- **Visual Confirmation**: Present changes in the VS Code Diff view before applying them.
- **Logic-First Planning**: For any function or logic block longer than roughly 10 lines, output a `<plan>` tag containing the step-by-step algorithm, then wait for a "Proceed" command before writing code.
- **Atomic Turns**: No multi-file edits in a single turn without a pre-approved written plan, and do not move to "Step 2" until "Step 1" is verified.

## Task State Does Not Live Here

Transient state -- in-flight plans, task checklists, the active release cycle --
belongs in `.agent-state.md` (git-ignored), as `AGENTS.md` mandates. It MUST NOT
be written back into this file.

Until LDM-#1381 this file carried 348 lines: its own numbered mandates on scope,
code quality, testing gates, branching and release automation, plus 239 lines of
completed-task `Gemini Added Memories`. The mandates were migrated into the
skills `AGENTS.md` routes to (script parity into `testing-and-ci`; terminal UI
integrity, founding patterns, Client Extension standards and piped-input
automation into `ldm-architecture`; the PR review feedback loop into
`github-workflows`). The memories were every-item-complete task history tracked
in git and were dropped rather than migrated.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-27* | *Last Reviewed: 2026-08-27*
