---
name: testing-and-ci
description: Activate this skill whenever writing tests, running linters, or committing code.
---

# Testing & CI Rules

## Pre-commit & CI Verification

- **Mandatory Pre-commit Installation**: The agent MUST proactively verify that `pre-commit` hooks are installed locally (i.e. `.git/hooks/pre-commit` exists). If they are missing, you MUST run `.pytest_venv/bin/pre-commit install` (or `ldm dev-setup`) before attempting to commit code. This ensures that the local git hooks will intercept `git commit` and run linters like `ruff format` automatically, preventing unformatted code from slipping through and failing the CI Quality Gate.
- **Strict Mechanical Enforcement (Agent Push)**: As an AI agent, you are STRICTLY PROHIBITED from using `git commit --no-verify` or bypassing quality gates. You MUST ONLY use the `./scripts/agent_push.sh "<commit message>"` wrapper script, which mechanically forces the execution of `pre-commit run --all-files` and `pytest`.
- **You MUST `git add` before calling the wrapper — it does NOT stage for you.** `agent_push.sh` commits only what you staged. Since LDM-#1280 it *refuses* rather than misleading you: a pre-flight check runs **before** the ~10-minute gates and hard-errors when nothing is staged but the working tree has changes, listing the offending paths. A staging mistake now costs a second.

  Know what that guard replaced, because the shape of the bug recurs. `git add .` used to run *only inside the hook-failure branch*; the commit was guarded on staged changes, but **the push ran unconditionally**. A run where every gate passed first time therefore committed nothing, pushed an empty branch, printed `✅ Push completed successfully!` and exited `0`. The incentive was inverted — **the cleaner the work, the more likely it silently committed nothing** — because work that tripped a hook got rescued by that `git add .` while clean work fell straight through. Hit on 2026-08-22 during LDM-#1262; the sole signal was one line buried in ~600 lines of pytest output, and a PR containing zero changes was moments from being opened.

  Three habits follow, none of which the guards make redundant:

  - **Stage explicitly, then verify the commit landed.** `git add <paths>` first; afterwards check `git log --oneline -1` and `git show --stat HEAD`. A success banner is not evidence that anything was committed — the wrapper now asserts `HEAD` moved, but confirm rather than trust.
  - **Prefer `git add <paths>` over `git add -A`.** On hook failure the wrapper re-stages only the paths that were already staged, so unrelated edits no longer ride along (the old blanket `git add .` is a plausible contributor to LDM-#1233, committed scratch debris). It warns about hook-modified files it left unstaged — read that warning, because `.secrets.baseline` genuinely may need to be included.
  - **PUSH-ONLY mode is announced, not silent.** With a clean tree, nothing staged and unpushed commits present (e.g. re-running after a failed push), the wrapper proceeds without committing and says so. If you see it and did not intend it, stop — you probably forgot to stage.
- **Handling Hook Failures**: If a hook fails, you are FORBIDDEN from using `--no-verify`. For formatting failures (e.g. `ruff format`), you MUST run the formatter locally, stage the fixed files, and try again. Only if a hook is genuinely unrunnable in your environment may you skip it, and then ONLY that hook, via the `SKIP` environment variable -- never a broad list.
  - `./scripts/agent_push.sh` (the mandated wrapper) sets `SKIP=bump-docs-timestamps` by default on every run, and nothing else. That hook is skipped *deliberately*: it rewrites every markdown file's `Last Updated` footer on any `--all-files` run, which would otherwise stamp dozens of unrelated docs on every single commit. If you invoke `pre-commit`/`git commit` manually instead of via the wrapper, mirror this same `SKIP` value rather than inventing a longer one.
  - **Do not re-add `semgrep`, `detect-secrets` or `actionlint` to that list.** They were skipped for years on the stated grounds that their binaries "may only be installed in CI". That premise was false (LDM-#1246): all three are `repo:`-based hooks, so pre-commit provisions its own isolated environment for each and nothing needs to be installed locally at all. Measured including one-time provisioning: `detect-secrets` 3s, `actionlint` 6s, `semgrep` 21s -- all passing. The effect was that two security scanners never ran on any agent-driven commit.
- **Handling Secrets Baseline Shifts**: The `detect-secrets` hook will fail in CI if line numbers for existing tracked secrets shift due to code changes above them (e.g., adding lines to `ci.yml`). When making structural changes or adding lines to files tracked in `.secrets.baseline`, you MUST proactively run `.venv/bin/python3 -m pre_commit run detect-secrets --all-files` (or manually patch the line numbers in `.secrets.baseline`) and commit the updated baseline file to prevent CI cascade failures.
- **Pure ASCII PowerShell Encoding**: All PowerShell scripts (`.ps1` and `.psm1`) MUST strictly use pure ASCII encoding without non-ASCII multi-byte characters. Windows PowerShell 5.1 misparses non-ASCII UTF-8 bytes on ANSI code pages. Validate via `scripts/check_powershell_ascii.py`.

## Post-Push CI Monitoring (No Silent Failures)

- **Never leave a red GitHub Action unaddressed**: After pushing to a PR branch (or after a merge/push to `master` you triggered or observe), you MUST watch the run to completion (e.g. via `gh pr checks`/`gh run watch` or the `Monitor` tool) and, if it fails, investigate the actual failure log (`gh run view --job <id> --log`) before doing anything else with that PR/branch. Do not just report "CI failed" and move on, and do not assume a failure is unrelated/transient without reading the log to confirm.
- **Fix or triage, then re-trigger**: If the failure is caused by the change under review, fix it, push, and watch the re-run. If it is a genuine one-off infra hiccup unrelated to the diff (verify by re-reading the actual error, not by guessing), rerun the failed jobs (`gh run rerun --failed`) rather than leaving them red -- but only after confirming the failure isn't a real regression the rerun would just mask.
- **A failure on `master` is not "someone else's problem"**: If a push/merge you performed triggers a CI run on `master` and it fails, that failure must be investigated and resolved (fix + re-push, or rerun if transient) before considering the task done -- do not treat PR-merge as the finish line while a resulting `master`-branch CI run sits failed and unexamined.

## Tests MUST NOT Touch the Developer's Real State

**Hard rule: no test may read or write the real `~/.ldm`, `~/.ldmrc`, `~/.ssh`, or the machine's Docker contexts.** Set `LDM_HOME` to a temp directory, or patch `get_actual_home`.

`HOME` is not sufficient and never was. `get_actual_home()` reconstructs `/Users/<username>` from `SUDO_USER`/`USER` on macOS so that it still finds the real home under `sudo`, so it ignores `HOME` completely. `LDM_HOME` (LDM-#1349) exists precisely because there was otherwise no way to redirect state from outside the process -- which meant subprocess-based tests could not be isolated at all.

What that cost, and why the rule is phrased as an absolute:

- `test_e2e_diagnostics.py` ran `ldm prune --seeds --samples --clean-hosts` as a real subprocess against the developer's own home. `--seeds` bypasses the confirmation prompt (`elif prune_seeds or (not non_interactive and UI.confirm(...))`), so the piped "n" answers protected nothing. **Every suite run deleted the real pre-warmed seed cache** -- ~1GB per entry -- and the sample cache. It went unnoticed for months because seeding itself was broken until #1322, so the missing cache was indistinguishable from the pre-existing failure. It surfaced only when a developer's `ldm run` downloaded a seed and the concurrently-running suite deleted it between caching and extraction.
- `find_dxp_roots()` reconciliation writes to the real `~/.ldm/registry.json`, so running the suite registers pytest tempdirs as the developer's projects (#1342).
- `cmd_target_add` runs a real `docker context create` for any non-local host, so `test_target.py` leaves a `wsl -> ssh://dev@192.168.1.10` context on the machine (#1342). Patching `_get_config_path` spares `~/.ldmrc` but not Docker.

Two habits follow:

- **A destructive assertion needs a canary, and the canary must be able to fail.** A guard test that writes a sentinel into the real location and asserts it survives is only worth having if it actually trips. The first version of the #1349 guard used a filename that did not match `prune`'s `*.tar.gz` glob, so the delete branch was never reached and the guard passed against the unfixed code. Verify the guard fails before trusting that it passes.
- **Prefer asserting the behaviour over asserting the absence of a crash.** The destroyed-cache test only checked exit code 0 and one line of stdout; it never asserted that pruning cleared anything. A test that had checked its own temp cache would have been both safer and stronger.

## Assertions About Runtime Behaviour MUST Be Observed Before Being Committed

**Hard rule: assertions about runtime behaviour must be empirically executed and observed locally before being committed.** Reading the implementation is *not* sufficient evidence that an assertion is correct.

This is not a style preference — it is the single most expensive class of mistake made in this repository. On 2026-08-21 it cost **two permanently burned release tags** (`v2.15.33-pre.1`, `v2.15.33-pre.2`), three red CI runs across five platforms each, and several hours:

1. An E2E check asserted `ldm -y up` returns exit `5` on an already-running project. That is exactly what `ldm_core/pipelines/run.py:246` does — but the suite **stops the project** at `verify_e2e_refactor.sh:625` first, so the call correctly returned `0`. The assertion was right about the code and wrong about the state.
2. The fix let execution reach the next check *for the first time ever* (the previous failure had aborted before it), revealing that block was also broken — it passed a directory to `ldm deploy` where `cmd_deploy` expects a service name or artifact file (#1262).

The one time the behaviour was actually measured, it was correct first time and took about thirty seconds.

### What "observed" means

Run the exact command, on the exact state, and look at the result:

```bash
# Establish the state the assertion depends on, then measure it.
ldm -y up .            # observe: 0 when stopped
ldm -y up .            # observe: 5 when already running
echo "exit=$?"
```

Cheap observation is usually available if you look for it. To confirm exit `5` without booting anything, a throwaway project directory whose hand-written `meta` sets `container_name` to an **already-running** container is enough -- `RuntimeValidationStage` is stage 3 of 7 and short-circuits before `ComposerStage`/`ExecutionStage`, so nothing is created or started.

### Applies to

- Any new or tightened assertion in `scripts/verify_e2e_refactor.{sh,ps1}`.
- Any claim about an exit code, a JSON schema, or CLI output shape. Verify against **real output** (`ldm list --json | ...`), not against the code that produces it -- `status --json` and `list --json` have different shapes, and an assertion written from the source asserted keys that only ever existed in the other command.
- Any assertion whose outcome depends on ordering or prior state. State is invisible in the function you are reading.

### Corollary: ruling something OUT by reading is the same error as ruling it in

The rule above is usually applied to assertions. It applies just as much to **exclusions** — deciding a file, test or component *cannot* be the cause and skipping it.

Worked example, LDM-#1271. A leak of Docker volumes was traced by first excluding `ldm_core/tests/test_e2e_interactive.py`, on the strength of a comment in it:

> `# This prevents it from proceeding to verify_runtime_environment which triggers Docker.`

That comment describes **one test's input handling**, not the module's behaviour. Two other tests in the same file were creating real containers and volumes on every run. Because the file had been excluded up front, the recommended bisect-by-module search then ran across eight candidate modules and reproduced **nothing** — the guilty one had already been removed from the search space by an act of reading.

What worked was measuring: polling `docker volume ls` during a verbose run and recording which test was executing whenever a volume appeared. That named both culprits in a single pass.

Two habits follow:

- **Do not narrow a search space using source comments.** A comment records an intention at the time of writing; it is not evidence about present behaviour, and it is never evidence about the other code around it.
- **When a search over a "complete" candidate list finds nothing, suspect the list, not the phenomenon.** A reproducible effect that no candidate produces means a candidate was wrongly excluded.

A related trap in the same issue: the first fix targeted exactly the right volume names and silently did nothing, because a container in `Created` state still referenced them (`volume is in use`). Verify a fix by observing the effect disappear, not by confirming the code looks right.

### Corollary: read every failing run, not the first one

A release tag fires three to four workflows. Reporting "the" failure after reading one is how a transient infrastructure error gets mistaken for a code defect, and vice versa. On `v2.15.33-pre.2`, two workflows failed on a genuine defect while `LDM CI & Release` failed independently on `HttpError: other side closed` from `softprops/action-gh-release` -- rerunnable via `gh run rerun --failed`, needing no new tag. Enumerate every non-passing run before diagnosing.

## Runtime-Sensitive Changes Need Live Verification, Not Just Green CI

- **Unit-test-green and CI-green are not the same claim as "this works."** A unit test that mocks Docker/Liferay can only prove "this code produces the artifact I intended" (e.g. the right compose YAML). It cannot prove the artifact behaves correctly at runtime -- container boot timing, OSGi bundle activation races, and mount-semantics differences (bind-mount vs. Named Volume) are invisible to a mocked test by construction. Treat any change to container-boot sequencing, OSGi/client-extension mount strategy, or Docker volume semantics as its own category that requires an actual live run (real containers, real boot, the real repro scenario) before you consider it verified -- 100% unit coverage on the new code is not a substitute and does not lower this bar.
- **Watch out for auto-merge racing ahead of your own verification.** If auto-merge is enabled on a PR, a green CI run can merge it the moment CI finishes -- even while you are still mid-way through the live verification you yourself said you'd do before trusting the fix. This isn't hypothetical: it happened during the #1083 investigation (PR #1089 auto-merged while its live re-verification was still running, and that verification then proved the fix didn't work, requiring an immediate revert PR). If you're mid-verification on a PR with auto-merge enabled, say so explicitly and don't let CI-green get treated as the finish line until you've actually finished checking.
- **A confirmed root cause from one clean test run is not confirmed.** One A/B comparison that "looks clean" can still be coincidental (e.g. "faster overall boot" rather than "the specific mechanism I changed"). Don't write up a root cause as confirmed in an issue/PR until you've verified the *fix*, not just the *diagnosis*, end-to-end against the original repro.

## Endpoint Protection & Security

- **Mocking System Calls in Tests**: Never execute actual compiled binaries (like `lfr-tunnel`, `ldm`) during unit/integration tests using `subprocess` or `os.system`. All system and binary execution calls MUST be correctly mocked (`@patch("ldm_core.utils.run_command")` or `@patch("subprocess.Popen")`) to prevent triggering corporate endpoint protection tools (e.g., SentinelOne), which may detect these test invocations as malicious activity and aggressively quarantine/delete the binaries and surrounding development tools (like `brew`, `jenv`, etc.).

## Python Virtual Environment (venv)

- **Mandatory Alignment**: All development, testing, linting, and Git operations MUST be conducted within the project's Python virtual environment (`.venv`).
- **Hook Isolation**: Git hooks and pre-commit checks rely on packages installed in the virtual environment. Running operations outside the virtual environment (e.g. using global system Python) will trigger hook failures.

## No Un-tracked Deferrals of Verification Tests

**Hard rule: Any promised or deferred test addition (such as E2E verification script assertions in `scripts/verify_e2e_refactor.{sh,ps1}`) MUST be written to the test script or filed as a tracked GitHub issue BEFORE any release or pre-release is tagged.**

- **The "I'll do it after X lands" Trap**: Deferring test additions verbally without a failing test or a tracked issue guarantees context evaporation when X lands. A feature is NOT complete—and a release MUST NOT be cut—until the corresponding E2E verification assertions are merged into `scripts/verify_e2e_refactor.{sh,ps1}`.
- **Pre-Release Gate Check**: BEFORE running `python3 scripts/release.py --bump beta`, the agent MUST check `scripts/verify_e2e_refactor.{sh,ps1}` to confirm that all feature verification assertions for the tracked issue are present and passing.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-26* | *Last Reviewed: 2026-08-26*
