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

## Pre-Release Strategy

To prevent "version fatigue" and ensure the stability of the main release channel:

- **Release Orchestration**: All version updates, pre-releases, and stable promotions MUST be performed using the automated orchestrator script. Manual git tagging or direct version modifications are strictly prohibited.
- **Experimental Features**: All brand new or complex functionality (specifically **Liferay Cloud Golden Path** integrations) MUST be released as **Pre-Releases** (e.g. `v2.10.x-pre.y`) first.
- **Pre-Flight Quality Gate (Mandatory Local Verification)**: BEFORE running `python3 scripts/release.py --bump beta`, the agent MUST run `.venv/bin/python3 -m pre_commit run --all-files` locally and verify it passes with 0 errors. Never push a release bump without running local lint verification first.
  - **`./lint.sh` is NOT an equivalent substitute.** It does not run `check-version-sync`, `gitleaks`, `mypy`, `check-cli-drift`, `validate-compose`, `deptry` or `shellcheck`, and by default it *auto-fixes* rather than validates (use `./lint.sh --check` if you run it at all). `check-version-sync` is the most release-relevant hook in the set, so a release verified only by `lint.sh` is not verified. `scripts/release.py` refuses to fall back to it for this reason (LDM-#1244).
- **Post-Push Release Asset Verification Gate**: AFTER running `python3 scripts/release.py --bump beta`, the agent MUST execute `gh run list --workflow=ci.yml` and actively monitor the pushed tag run until the `release` job completes with status `success`. If CI fails at any step (e.g. `markdownlint`, `mypy`, `check-cli-drift`, or `detect-secrets`), immediately extract logs (`gh run view --log-failed`), run `pre-commit run --all-files` locally to quickly reproduce and resolve all quality gate failures in a single local iteration, re-verify with `./lint.sh`, and bump to the next candidate. Never assume pushing a tag automatically created the GitHub Release entity without verifying CI run completion.
- **Verification Gate**: A pre-release feature is only eligible for a stable release after the user has explicitly confirmed they have performed a full manual E2E verification of the pre-release. Do not automatically promote releases without explicit user confirmation.
- **Immutable Tags (The Burn Rule)**: GitHub Repository Rules strictly prohibit the deletion or force-updating of Git tags. Once a tag (e.g. `v2.15.19`) is pushed, it is permanently locked to that commit. Any premature tagging permanently burns the version number, requiring a version bump to recover. You MUST be absolutely certain all pre-requisites are met before tagging.
- **Compatibility Matrix Gate**: You MUST update the compatibility matrix (in the project documentation) to reflect the newly verified environments BEFORE moving to a stable release. Always run `python3 scripts/sync_compatibility.py` from a checkout whose `ldm_core/constants.py` `VERSION` actually matches the report(s) you're syncing (e.g. the active `release/vX.Y.Z-pre.N` branch for pre-release reports) -- running it from `master` (or any other mismatched checkout) silently archives every report whose binary/script version doesn't match as "stale," discarding real test data with no error.
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
*Last Updated: 2026-08-21* | *Last Reviewed: 2026-08-21*
