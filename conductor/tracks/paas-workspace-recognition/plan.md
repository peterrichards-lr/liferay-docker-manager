# Track: PaaS Workspace Recognition & Golden Path

## Overview

Replicating a Liferay Cloud PaaS environment locally currently requires developers to understand the strict boundary between code (managed by Git) and data (managed by LCP Backups), and then manually stitch these together using multiple LDM commands (`init-from` and `cloud-fetch`).

This track aims to formalize this "Golden Path" through dedicated documentation while simultaneously enhancing the `ldm init-from` command. The command will detect Liferay Cloud Git repositories and automatically guide the user through the data-hydration phase, making the process intuitive and error-free without crossing the boundary of becoming a Git management tool.

## Objectives

1. **Formal Documentation:** Create a definitive guide (`docs/guides/PAAS_LOCAL_DEV.md`) detailing the exact manual steps to clone a PaaS repo and replicate the environment locally.
2. **Smart Recognition:** Enhance `ldm init-from` to dynamically detect if the source directory is a Liferay Cloud workspace.
3. **Guided Hydration:** If a PaaS workspace is detected, orchestrate interactive prompts to execute the `cloud-fetch` (data and env vars) process seamlessly.

## Implementation Plan

### Phase 1: The "Golden Path" Documentation

* **Goal:** Establish the documented manual workflow to clarify tool boundaries.
* **Tasks:**
  * Create `docs/guides/PAAS_LOCAL_DEV.md`.
  * Clearly state the boundary: LDM manages runtime/data; Git manages source control.
  * Document the 4-step manual flow:
        1. `git clone <cloud-repo>`
        2. `ldm init-from ./cloud-repo`
        3. `ldm cloud-fetch <env> --download --restore`
        4. `ldm cloud-fetch <env> --sync-env`
  * Update `docs/README.md` to link prominently to this new guide.

### Phase 2: LCP Workspace Detection

* **Goal:** Allow LDM to silently identify Liferay Cloud repository structures.
* **Tasks:**
  * Update `ldm_core/handlers/workspace.py` (specifically `cmd_init_from` or `cmd_import`).
  * Implement a heuristic check within the imported source directory:
    * Presence of a `liferay/` folder containing an `LCP.json`.
    * Presence of `client-extensions/` alongside a root `lcp.json`.
  * If detected, extract the default `cloud_project_id` from the `LCP.json` (if available) and save it to the local LDM project's `.liferay-docker.meta` to streamline later `lcp` commands.

### Phase 3: The Guided Wizard

* **Goal:** Seamlessly transition the user from code import to data hydration.
* **Tasks:**
  * After the successful completion of the code sync in `cmd_init_from`, check the detection flag from Phase 2.
  * If `True` (and the CLI is not running in `-y` non-interactive mode), trigger the wizard:
    * Output: `> Detected Liferay Cloud Workspace structure.`
    * Prompt (using `UI.confirm`): `Would you also like to pull the remote database and document library to complete the local replica? [Y/n]`
  * If the user agrees, use the Cloud Handler (`self.manager.cloud`) to:
    * Verify `lcp` authentication.
    * Use `lcp list --json` to present an interactive menu of environments (`prd`, `uat`, etc.).
    * Programmatically execute the equivalent of `ldm cloud-fetch <env> --download --restore --sync-env`.

### Phase 4: Refinement & Testing

* **Goal:** Ensure the wizard gracefully handles edge cases.
* **Tasks:**
  * Ensure the wizard fails safely (falling back to just the code import) if `lcp` is not installed or authenticated, displaying an informative hint to the user instead of a hard crash.
  * Ensure all prompts are strictly bypassed if the user invoked `ldm init-from -y`.
