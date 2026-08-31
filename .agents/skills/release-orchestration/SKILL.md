---
name: release-orchestration
description: Activate this skill whenever preparing a release, bumping versions, or creating tags.
---

# Release Orchestration

## Release & Version Automation

- **Read this skill before touching a release. Reading `scripts/release.py` is not a substitute.** On 2026-08-22 an agent reverse-engineered the script instead — tracing the bump arithmetic in `ldm_core/handlers/dev.py`, checking the clean-workspace allowlist, even running the bump and observing the result — and concluded it had verified the approach. It had verified what the script *would do*, and nothing about what it was *permitted* to do. The very first rule below prohibited the approach outright; the CI gate that caught it is named two paragraphs later. `master` went red (LDM-#1288). Empirical verification of a mechanism is not authorisation to use it.
- **Automated Orchestrator**: AI agents MUST never manually bump version strings, modify metadata config files (e.g. `pyproject.toml`, `constants.py`), or create/push git tags. You MUST always use the automated orchestrator script:
  - To start a new pre-release cycle (run from `master`) or continue an already-open one (run from its existing `release/vX.Y.Z-pre.N` branch -- the script detects which by your current branch):

    ```bash
    python3 scripts/release.py --bump beta --issue 1204
    ```

    Passing `--issue <number>` automatically appends `Closes #<number>` to the PR description so GitHub links and auto-closes the tracked issue or epic upon promotion.

    Continuing a cycle bumps the `-pre.N` counter, commits and pushes to the *same* branch, and reuses its *same* open tracking PR -- it never creates a new branch or PR for a second/third/etc. beta increment, and it never merges that PR into `master`. A CI gate (`block-prerelease-on-master` in `.github/workflows/ci.yml`, part of the required checks on the `master` branch ruleset) independently rejects any attempt to merge a pre-release-versioned ref into `master`, so this can't be circumvented by manually running `gh pr merge` on the tracking PR either.
  - To open a pre-release cycle for a **minor or major** version (LDM-#1291), rather than the next patch:

    ```bash
    python3 scripts/release.py --bump preminor --issue 1264   # 2.15.33 -> 2.16.0-pre.1
    python3 scripts/release.py --bump premajor --issue 1264   # 2.15.33 -> 3.0.0-pre.1
    ```

    `--bump beta` only ever opens the *next patch* cycle (`X.Y.{Z+1}-pre.1`, `ldm_core/handlers/dev.py:182-193`) and `--bump minor` produces a **stable** version directly, so before #1291 a minor could not be exercised as a pre-release at all — across 300 releases no `X.Y.0-pre.*` existed, and every minor from v2.8.0 to v2.15.0 was cut straight to stable. Since every release must reach the wider user community only after a pre-release, use these to open the cycle.

    `preminor`/`premajor` only **open** a cycle. Continue it with `--bump beta` as usual — it matches on the `-pre.N` suffix and increments N whichever component started the cycle, so the tracking PR and `--promote` behave identically. Running `preminor` from an existing `release/*` branch is rejected, exactly as the other non-beta bumps are.

    **Never hand-set a version to reach `X.Y.0-pre.N`.** That is prohibited by the rule above on any branch; if the orchestrator lacks a path you need, add it to the orchestrator (as #1291 did) or ask the maintainer.

  - To publish a **disposable preview build** for validating an idea that may be abandoned (LDM-#1265), without consuming a `-pre.N` number:

    ```bash
    python3 scripts/release.py --preview --issue 1265
    ```

    Tags `preview-<issue>.<n>`, builds real binaries for every platform and publishes a GitHub pre-release. It does **not** bump the version, create a release branch, open a tracking PR, or advance the pre-release chain, and the version-marking commit is made on a detached HEAD so no branch is touched.

    Use this for a proof of concept or an experiment that may be dropped. Use `--bump beta`/`preminor` for anything intended to ship: a preview is never promoted, and `--preview --promote` is rejected.

    Two properties are load-bearing and were both measured, not assumed:

    - **The tag is deletable.** The `Protect Release Tags` ruleset targets `refs/tags/v[0-9]*.[0-9]*.[0-9]*`, so a `v`-prefixed preview would be permanently undeletable under the Burn Rule — defeating the entire point. `preview-*` is outside that glob. Clean up with `git push --delete origin preview-<issue>.<n>` and `gh release delete`.
    - **It can never be served as an upgrade.** `check_for_updates` skips the `preview-` prefix explicitly. Do not rely on version ordering for this: the marked version must use a **dash**, never a plus. `2.15.33+preview.1265.1` ranks `(2,15,33,1265)`, and 1265 beats the 999 assigned to a stable release, so a `+` form sorts *above* the release it previews. The dash form ranks `(2,15,33,2,1265)`, safely below.

  - To promote pre-releases to stable releases (must be run from the active release branch):

    ```bash
    python3 scripts/release.py --promote
    ```

    This is the **only** path that ever merges a release branch into `master` -- it bumps to a stable version first, then merges.

- **Fix on `master` first, then backport into the release branch -- never the reverse.** A release branch is short-lived and dies at promotion; `master` is the trunk and outlives everything. Cherry-picking *master -> release* means the duplicate disappears when the branch does. Cherry-picking *release -> master* leaves the same change arriving by two routes, and it collides when `--promote` merges.

  Hit on 2026-08-24 with LDM-#1300. The fix was urgent for in-flight Windows verification, so it landed on `release/v2.16.0` first -- correct for unblocking. But `master`'s copy of the script was then still broken, and new work (#1301) needed those helpers, so the commit was cherry-picked *release -> master-based branch*. After promotion that branch carried a duplicate commit and a stale `SCRIPT_VERSION`, conflicting against the very change it already contained.

  The sequence that serves the same urgency without the collision:

  1. fix on a branch off `master` -> PR -> merge
  2. cherry-pick that commit **into** the open `release/*` branch, unblocking verification
  3. build follow-up work on `master`, which now has it natively
  4. at promotion the merge is a no-op for that file

  Only genuinely release-only changes -- the version bump, its CHANGELOG entry, the compatibility-matrix sync -- should originate on the release branch.

  **Step 4 is not actually a no-op, because this repository squash-merges.** A squash merge puts one new commit on `master` with no ancestry link to the branch it came from, so the cherry-pick on `release/*` and the squashed commit on `master` are unrelated commits containing the same content. Git cannot reconcile them, and the tracking PR goes `CONFLICTING` at promotion -- following the rule correctly does not avoid this, because the cause is the squash, not the direction.

  Hit on 2026-08-25 promoting v2.17.0: PR #1320 turned `DIRTY` with both verification scripts conflicting, master having taken #1323 as squash `f2654d07` while the release branch carried cherry-pick `64796652`.

  The same merge is also a **routine precondition of every `--bump beta`**, not only a promotion-time remedy -- see the Backport Gate under Pre-Release Strategy. Framing it solely as conflict resolution is what let LDM-#1490 happen.

  Resolve it **before** running `--promote`, not during:

  1. `git merge origin/master` on the release branch
  2. confirm the release branch is a superset of `master` for the conflicting files -- diff them and check that master's only unique lines are ones the branch deliberately replaced
  3. resolve with `git checkout --ours <file>` for those files
  4. commit the merge. `scripts/agent_push.sh` cannot express a merge commit, so this needs `git commit`; run `pre-commit run --all-files`, `mypy ldm_core --config-file=mypy.ini` and the full `pytest` by hand before pushing, since the wrapper's gates are being bypassed
  5. verify `git merge-base --is-ancestor origin/master HEAD` before promoting

  Never resolve by taking `master`'s side without checking step 2 -- the release branch usually carries changes master has not seen, and `--theirs` would silently discard them.

- **Do not base new work on a cherry-pick from an open release branch.** If `master` lacks something you need, that is a signal to land it on `master` properly, not to borrow it sideways. Borrowing produces a branch that looks fine until the release lands and then conflicts with itself.

- **Branch from `master`, and prove it before raising the PR.** The commonest way to hit the squash collision above is not a deliberate cherry-pick -- it is creating a branch while still standing on `release/*`. The new branch inherits every release commit: the `-pre.N` bumps, the compatibility-matrix syncs, the promotion. `master` already has all of that content via the promotion squash, so the PR conflicts on files the change never touched.

  Hit on 2026-08-25, immediately after this rule was written. `fix/1325-1329-...` was branched off `release/v2.17.0` by accident and arrived as **33 files across 10 commits** instead of 6 files in 1, conflicting on `docs/TESTING.md`, `docs/reference/compatibility.md` and the verification reports -- none of which the fix touched. Rebuilt with `git branch -f <branch> origin/master` and a cherry-pick of the one real commit.

  Two checks, both cheap, before raising any PR:

  ```bash
  git log --oneline origin/master..HEAD    # only your own commits?
  git diff --name-only origin/master...HEAD # only the files you meant?
  ```

  A file list containing `CHANGELOG.md`, `pyproject.toml`, `ldm_core/constants.py`, or anything under `references/verification-results/` in a change that is not a release commit means the branch is sitting on release history. Rebuild it off `master` rather than resolving the conflicts -- resolving them merges release commits a second time.

## Pre-Release Strategy

To prevent "version fatigue" and ensure the stability of the main release channel:

- **Release Orchestration**: All version updates, pre-releases, and stable promotions MUST be performed using the automated orchestrator script. Manual git tagging or direct version modifications are strictly prohibited.
- **Experimental Features**: All brand new or complex functionality (specifically **Liferay Cloud Golden Path** integrations) MUST be released as **Pre-Releases** (e.g. `v2.10.x-pre.y`) first.
- **Backport Gate (the release branch is not `master`)**: BEFORE running `python3 scripts/release.py --bump beta`, verify the release branch actually contains the commits the pre-release is meant to carry.

  `--bump beta` pulls **only the release branch** (`scripts/release.py`, the `is_continuing_release` branch) and never merges `master`. Everything merged to `master` since the last `-pre.N` is therefore absent -- which is the normal case, because the "fix on `master` first, then backport" rule above sends every fix to `master`. Cutting regardless produces a `-pre.N` byte-identical to its predecessor plus a version bump: a pre-release containing none of the fixes it exists to verify, with the number burnt by the time verification reveals it.

  ```bash
  git log --oneline origin/release/vX.Y.Z..origin/master   # MUST be empty
  ```

  Not empty? Merge `master` into the release branch first, using the procedure under *Fix on `master` first* below, then re-run the check. `scripts/agent_push.sh` cannot express a merge commit, so run `pre-commit run --all-files`, `mypy ldm_core --config-file=mypy.ini` and the full `pytest` by hand before pushing.

  Two things that will not save you here. The tracking PR shows the release branch against `master`, so it looks healthy whether or not the backport happened. And CI passes either way -- it tests the branch as it is, not as it was meant to be. Nothing but this check distinguishes a correct `-pre.N` from an empty one.

  Nearly hit on 2026-08-31 cutting `v2.19.0-pre.2`: six commits sat on `master` and none on the release branch, including all four fixes the pre-release existed to verify (LDM-#1490).

- **Feature Verification Script Gate**: BEFORE running `python3 scripts/release.py --bump beta`, the agent MUST verify that all feature assertions for the issue being released are written and committed in `scripts/verify_e2e_refactor.sh` and `scripts/verify_e2e_refactor.ps1`. Never cut a pre-release for a feature whose E2E verification script checks have been deferred without a tracked issue or committed assertions.
- **Pre-Flight Quality Gate (Mandatory Local Verification)**: BEFORE running `python3 scripts/release.py --bump beta`, the agent MUST run `.venv/bin/python3 -m pre_commit run --all-files` locally and verify it passes with 0 errors. Never push a release bump without running local lint verification first.
  - **`./lint.sh` is NOT an equivalent substitute.** It does not run `check-version-sync`, `gitleaks`, `mypy`, `check-cli-drift`, `validate-compose`, `deptry` or `shellcheck`, and by default it *auto-fixes* rather than validates (use `./lint.sh --check` if you run it at all). `check-version-sync` is the most release-relevant hook in the set, so a release verified only by `lint.sh` is not verified. `scripts/release.py` refuses to fall back to it for this reason (LDM-#1244).
- **Post-Push Release Asset Verification Gate**: AFTER running `python3 scripts/release.py --bump beta`, the agent MUST execute `gh run list --workflow=ci.yml` and actively monitor the pushed tag run until the `release` job completes with status `success`. If CI fails at any step (e.g. `markdownlint`, `mypy`, `check-cli-drift`, or `detect-secrets`), immediately extract logs (`gh run view --log-failed`), run `pre-commit run --all-files` locally to quickly reproduce and resolve all quality gate failures in a single local iteration, re-verify with `./lint.sh`, and bump to the next candidate. Never assume pushing a tag automatically created the GitHub Release entity without verifying CI run completion.
- **Verification Gate**: A pre-release feature is only eligible for a stable release after the user has explicitly confirmed they have performed a full manual E2E verification of the pre-release. Do not automatically promote releases without explicit user confirmation.
- **Immutable Tags (The Burn Rule)**: GitHub Repository Rules strictly prohibit the deletion or force-updating of Git tags. Once a tag (e.g. `v2.15.19`) is pushed, it is permanently locked to that commit. Any premature tagging permanently burns the version number, requiring a version bump to recover. You MUST be absolutely certain all pre-requisites are met before tagging.
- **Compatibility Matrix Gate**: You MUST update the compatibility matrix (in the project documentation) to reflect the newly verified environments BEFORE moving to a stable release. Always run `python3 scripts/sync_compatibility.py` from a checkout whose `ldm_core/constants.py` `VERSION` actually matches the report(s) you're syncing (e.g. the active `release/vX.Y.Z-pre.N` branch for pre-release reports) -- running it from `master` (or any other mismatched checkout) silently archives every report whose binary/script version doesn't match as "stale," discarding real test data with no error.
  - **The script now refuses rather than discarding (LDM-#1390).** A raw report whose recorded version does not match the checkout's `VERSION` makes `sync_compatibility.py` exit non-zero *before moving anything*, naming each report and both versions. Check out the ref whose `VERSION` matches and re-run. `--archive-stale` is the deliberate opt-out for genuinely clearing an older cycle's reports -- it prints the full plan first (each report and the name it moves to) before touching anything; `--dry-run` shows the same diagnosis without a failing exit, and `--quiet` suppresses routine progress without ever hiding a refusal. This used to be a `UI.warning` followed by the move, which is easy to miss in a long run.
  - **Sandboxable (LDM-#1391)**: `--results-dir PATH` and `--table PATH` override the two locations the script mutates, defaulting to `references/verification-results/` and `docs/reference/compatibility.md`. Tests MUST use them -- a test run against the defaults archives and rewrites the real verification record, which the Honesty Rule below exists to protect.
  - **Preview first**: `python3 scripts/sync_compatibility.py --dry-run` lists every rename, archive and table edit without touching anything, and prints the `VERSION` it resolved so a mismatch is visible before any file moves. Note this only became safe in LDM-#1252 -- the script previously had no argument parsing, so `--help`, `--dry-run` or any typo silently performed a full sync.
  - Only *raw* (non-canonically-named) reports are version-checked. Reports already at their canonical `verify-<slug>-<status>.txt` name are never staleness-tested, so an existing matrix entry cannot be archived by a mismatched run -- it is freshly-dropped reports that are at risk.
- **Raw Verification Reports Are Immutable (The Honesty Rule)**: `references/verification-results/*.txt` files are a verbatim, honest historical record of what was actually tested. **NEVER** hand-edit their content -- including the `Version:`/`Script Ver:` lines -- to make `sync_compatibility.py` accept a report, not even "just this once" when you're confident the underlying logic didn't change. If `sync_compatibility.py` incorrectly rejects a report over a provably cosmetic version mismatch (e.g. the standalone verify-script lagging the binary between refreshes -- a normal, expected drift in the real verification workflow, since binaries and the verify script are upgraded independently and not via git checkout), fix the *sync tool's* logic (see `_is_verify_script_diff_cosmetic_only`/`_is_metadata_only_diff` in `scripts/sync_compatibility.py` for the existing pattern) or have the user genuinely re-run verification -- never falsify the raw record to route around the check.
- **Release Announcements Gate**: Before initiating or promoting any release, verify that `RELEASE_ANNOUNCEMENTS` in `ldm_core/constants.py` has an active, non-empty entry for the current minor release series (`X.Y`) with current feature highlights. This is enforced by `test_release_announcements_contract` in `ldm_core/tests/test_architectural_contracts.py`.
- **Stable Promotion**: Stable releases (`[release]`) MUST be reserved for hardened features and verified bugfixes.

## Release Pull Request Naming Conventions

To ensure clarity and prevent title drift across multi-commit pre-release iterations:

- **Pre-Release Tracking PR Title**: Pre-release tracking PRs MUST use the generic base release version format:
  `chore(release): release tracking PR for vX.Y.Z [release]`
  *(e.g., `chore(release): release tracking PR for v2.15.29 [release]`)*
  This accurately reflects that the PR stays open across multiple `-pre.1`, `-pre.2`, `-pre.N` iterations before final promotion.
- **Stable Promotion PR Title**: When promoted to stable (`python3 scripts/release.py --promote`), the PR title is updated to:
  `chore(release): release vX.Y.Z [release]`

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-31* | *Last Reviewed: 2026-08-31*
