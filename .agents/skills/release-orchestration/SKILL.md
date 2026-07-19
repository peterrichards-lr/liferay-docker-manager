---
name: release-orchestration
description: Activate this skill whenever preparing a release, bumping versions, or creating tags.
---

# Release Orchestration

## Release & Version Automation

- **Automated Orchestrator**: AI agents MUST never manually bump version strings, modify metadata config files (e.g. `pyproject.toml`, `constants.py`), or create/push git tags. You MUST always use the automated orchestrator script:
  - To bump versions and tag pre-releases:

    ```bash
    python3 scripts/release.py --bump beta
    ```

  - To promote pre-releases to stable releases (must be run from the active release branch):

    ```bash
    python3 scripts/release.py --promote
    ```

## Pre-Release Strategy

To prevent "version fatigue" and ensure the stability of the main release channel:

- **Release Orchestration**: All version updates, pre-releases, and stable promotions MUST be performed using the automated orchestrator script. Manual git tagging or direct version modifications are strictly prohibited.
- **Experimental Features**: All brand new or complex functionality (specifically **Liferay Cloud Golden Path** integrations) MUST be released as **Pre-Releases** (e.g. `v2.10.x-pre.y`) first.
- **Verification Gate**: A pre-release feature is only eligible for a stable release after the user has performed a full E2E verification and confirmed its success.
- **Stable Promotion**: Stable releases (`[release]`) MUST be reserved for hardened features and verified bugfixes.
