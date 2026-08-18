---
name: release-orchestration
description: Activate this skill whenever preparing a release, bumping versions, or creating tags.
---

# Release Orchestration

## Release & Version Automation

- **Automated Orchestrator**: AI agents MUST never manually bump version strings, modify metadata config files (e.g. `pyproject.toml`, `constants.py`), or create/push git tags. You MUST always use the automated orchestrator script:
  - To start a new pre-release cycle (run from `master`) or continue an already-open one (run from its existing `release/vX.Y.Z-pre.N` branch -- the script detects which by your current branch):

    ```bash
    python3 scripts/release.py --bump beta
    ```

    Continuing a cycle bumps the `-pre.N` counter, commits and pushes to the *same* branch, and reuses its *same* open tracking PR -- it never creates a new branch or PR for a second/third/etc. beta increment, and it never merges that PR into `master`. A CI gate (`block-prerelease-on-master` in `.github/workflows/ci.yml`, part of the required checks on the `master` branch ruleset) independently rejects any attempt to merge a pre-release-versioned ref into `master`, so this can't be circumvented by manually running `gh pr merge` on the tracking PR either.
  - To promote pre-releases to stable releases (must be run from the active release branch):

    ```bash
    python3 scripts/release.py --promote
    ```

    This is the **only** path that ever merges a release branch into `master` -- it bumps to a stable version first, then merges.

## Pre-Release Strategy

To prevent "version fatigue" and ensure the stability of the main release channel:

- **Release Orchestration**: All version updates, pre-releases, and stable promotions MUST be performed using the automated orchestrator script. Manual git tagging or direct version modifications are strictly prohibited.
- **Experimental Features**: All brand new or complex functionality (specifically **Liferay Cloud Golden Path** integrations) MUST be released as **Pre-Releases** (e.g. `v2.10.x-pre.y`) first.
- **Verification Gate**: A pre-release feature is only eligible for a stable release after the user has explicitly confirmed they have performed a full manual E2E verification of the pre-release. Do not automatically promote releases without explicit user confirmation.
- **Immutable Tags (The Burn Rule)**: GitHub Repository Rules strictly prohibit the deletion or force-updating of Git tags. Once a tag (e.g. `v2.15.19`) is pushed, it is permanently locked to that commit. Any premature tagging permanently burns the version number, requiring a version bump to recover. You MUST be absolutely certain all pre-requisites are met before tagging.
- **Compatibility Matrix Gate**: You MUST update the compatibility matrix (in the project documentation) to reflect the newly verified environments BEFORE moving to a stable release. Always run `python3 scripts/sync_compatibility.py` from a checkout whose `ldm_core/constants.py` `VERSION` actually matches the report(s) you're syncing (e.g. the active `release/vX.Y.Z-pre.N` branch for pre-release reports) -- running it from `master` (or any other mismatched checkout) silently archives every report whose binary/script version doesn't match as "stale," discarding real test data with no error.
- **Raw Verification Reports Are Immutable (The Honesty Rule)**: `references/verification-results/*.txt` files are a verbatim, honest historical record of what was actually tested. **NEVER** hand-edit their content -- including the `Version:`/`Script Ver:` lines -- to make `sync_compatibility.py` accept a report, not even "just this once" when you're confident the underlying logic didn't change. If `sync_compatibility.py` incorrectly rejects a report over a provably cosmetic version mismatch (e.g. the standalone verify-script lagging the binary between refreshes -- a normal, expected drift in the real verification workflow, since binaries and the verify script are upgraded independently and not via git checkout), fix the *sync tool's* logic (see `_is_verify_script_diff_cosmetic_only`/`_is_metadata_only_diff` in `scripts/sync_compatibility.py` for the existing pattern) or have the user genuinely re-run verification -- never falsify the raw record to route around the check.
- **Release Announcements Gate**: Before initiating or promoting any release, verify that `RELEASE_ANNOUNCEMENTS` in `ldm_core/constants.py` has an active, non-empty entry for the current minor release series (`X.Y`) with current feature highlights. This is enforced by `test_release_announcements_contract` in `ldm_core/tests/test_architectural_contracts.py`.
- **Stable Promotion**: Stable releases (`[release]`) MUST be reserved for hardened features and verified bugfixes.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-18* | *Last Reviewed: 2026-08-18*
