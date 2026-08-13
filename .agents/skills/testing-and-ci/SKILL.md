---
name: testing-and-ci
description: Activate this skill whenever writing tests, running linters, or committing code.
---

# Testing & CI Rules

## Pre-commit & CI Verification

- **Mandatory Pre-commit Installation**: The agent MUST proactively verify that `pre-commit` hooks are installed locally (i.e. `.git/hooks/pre-commit` exists). If they are missing, you MUST run `.pytest_venv/bin/pre-commit install` (or `ldm dev-setup`) before attempting to commit code. This ensures that the local git hooks will intercept `git commit` and run linters like `ruff format` automatically, preventing unformatted code from slipping through and failing the CI Quality Gate.
- **Strict Mechanical Enforcement (Agent Push)**: As an AI agent, you are STRICTLY PROHIBITED from using `git commit --no-verify` or bypassing quality gates. You MUST ONLY use the `./scripts/agent_push.sh "<commit message>"` wrapper script, which mechanically forces the execution of `pre-commit run --all-files` and `pytest`.
- **Handling Hook Failures & Missing Binaries**: If a hook fails due to a missing binary in the local environment (e.g. `semgrep`, `detect-secrets`, or `actionlint`, which may only be installed in CI), you are FORBIDDEN from using `--no-verify`. You MUST explicitly skip ONLY the failing hooks using the `SKIP` environment variable. For formatting failures (e.g. `ruff format`), you MUST run the formatter locally, stage the fixed files, and try again.
  - `./scripts/agent_push.sh` (the mandated wrapper) sets `SKIP=bump-docs-timestamps,actionlint,semgrep,detect-secrets` by default on every run. `semgrep`/`detect-secrets`/`actionlint` are skipped for the missing-binary reason above; `bump-docs-timestamps` is skipped *deliberately*, not for a missing binary -- that hook rewrites every markdown file's `Last Updated` footer on any `--all-files` run, which would otherwise stamp dozens of unrelated docs on every single commit. If you invoke `pre-commit`/`git commit` manually instead of via the wrapper, mirror this same `SKIP` list rather than inventing a narrower one.
- **Handling Secrets Baseline Shifts**: The `detect-secrets` hook will fail in CI if line numbers for existing tracked secrets shift due to code changes above them (e.g., adding lines to `ci.yml`). When making structural changes or adding lines to files tracked in `.secrets.baseline`, you MUST proactively run `.venv/bin/pre-commit run detect-secrets --all-files` (or manually patch the line numbers in `.secrets.baseline`) and commit the updated baseline file to prevent CI cascade failures.
- **Pure ASCII PowerShell Encoding**: All PowerShell scripts (`.ps1` and `.psm1`) MUST strictly use pure ASCII encoding without non-ASCII multi-byte characters. Windows PowerShell 5.1 misparses non-ASCII UTF-8 bytes on ANSI code pages. Validate via `scripts/check_powershell_ascii.py`.

## Post-Push CI Monitoring (No Silent Failures)

- **Never leave a red GitHub Action unaddressed**: After pushing to a PR branch (or after a merge/push to `master` you triggered or observe), you MUST watch the run to completion (e.g. via `gh pr checks`/`gh run watch` or the `Monitor` tool) and, if it fails, investigate the actual failure log (`gh run view --job <id> --log`) before doing anything else with that PR/branch. Do not just report "CI failed" and move on, and do not assume a failure is unrelated/transient without reading the log to confirm.
- **Fix or triage, then re-trigger**: If the failure is caused by the change under review, fix it, push, and watch the re-run. If it is a genuine one-off infra hiccup unrelated to the diff (verify by re-reading the actual error, not by guessing), rerun the failed jobs (`gh run rerun --failed`) rather than leaving them red -- but only after confirming the failure isn't a real regression the rerun would just mask.
- **A failure on `master` is not "someone else's problem"**: If a push/merge you performed triggers a CI run on `master` and it fails, that failure must be investigated and resolved (fix + re-push, or rerun if transient) before considering the task done -- do not treat PR-merge as the finish line while a resulting `master`-branch CI run sits failed and unexamined.

## Runtime-Sensitive Changes Need Live Verification, Not Just Green CI

- **Unit-test-green and CI-green are not the same claim as "this works."** A unit test that mocks Docker/Liferay can only prove "this code produces the artifact I intended" (e.g. the right compose YAML). It cannot prove the artifact behaves correctly at runtime -- container boot timing, OSGi bundle activation races, and mount-semantics differences (bind-mount vs. Named Volume) are invisible to a mocked test by construction. Treat any change to container-boot sequencing, OSGi/client-extension mount strategy, or Docker volume semantics as its own category that requires an actual live run (real containers, real boot, the real repro scenario) before you consider it verified -- 100% unit coverage on the new code is not a substitute and does not lower this bar.
- **Watch out for auto-merge racing ahead of your own verification.** If auto-merge is enabled on a PR, a green CI run can merge it the moment CI finishes -- even while you are still mid-way through the live verification you yourself said you'd do before trusting the fix. This isn't hypothetical: it happened during the #1083 investigation (PR #1089 auto-merged while its live re-verification was still running, and that verification then proved the fix didn't work, requiring an immediate revert PR). If you're mid-verification on a PR with auto-merge enabled, say so explicitly and don't let CI-green get treated as the finish line until you've actually finished checking.
- **A confirmed root cause from one clean test run is not confirmed.** One A/B comparison that "looks clean" can still be coincidental (e.g. "faster overall boot" rather than "the specific mechanism I changed"). Don't write up a root cause as confirmed in an issue/PR until you've verified the *fix*, not just the *diagnosis*, end-to-end against the original repro.

## Endpoint Protection & Security

- **Mocking System Calls in Tests**: Never execute actual compiled binaries (like `lfr-tunnel`, `ldm`) during unit/integration tests using `subprocess` or `os.system`. All system and binary execution calls MUST be correctly mocked (`@patch("ldm_core.utils.run_command")` or `@patch("subprocess.Popen")`) to prevent triggering corporate endpoint protection tools (e.g., SentinelOne), which may detect these test invocations as malicious activity and aggressively quarantine/delete the binaries and surrounding development tools (like `brew`, `jenv`, etc.).

## Python Virtual Environment (venv)

- **Mandatory Alignment**: All development, testing, linting, and Git operations MUST be conducted within the project's Python virtual environment (`.venv`).
- **Hook Isolation**: Git hooks and pre-commit checks rely on packages installed in the virtual environment. Running operations outside the virtual environment (e.g. using global system Python) will trigger hook failures.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-13* | *Last Reviewed: 2026-08-13*
