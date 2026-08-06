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
- **Compatibility Matrix Gate**: You MUST update the compatibility matrix (in the project documentation) to reflect the newly verified environments BEFORE moving to a stable release.
- **Stable Promotion**: Stable releases (`[release]`) MUST be reserved for hardened features and verified bugfixes.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-06* | *Last Reviewed: 2026-08-06*
