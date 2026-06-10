# Track: Cloud Push

## 🛑 Feasibility Analysis (Status: Parked)

*This track has been paused due to UX and architectural concerns identified during the design phase.*

While technically possible, a local `cloud-push` command faces significant hurdles regarding developer experience and separation of concerns:

1. **The Code Transformation Problem:** LDM uses a simplified, flat structure (`common/`, `client-extensions/`). To push this to Liferay Cloud, LDM would have to silently generate a temporary `.ldm_temp/paas_workspace`, scaffold a valid `LCP.json`, map the files into nested directories (`liferay/configs/common/`), and then execute an `lcp deploy`. This is brittle and tightly couples LDM to the internal behavior of the `lcp` CLI.
2. **The Time-to-Complete Nightmare (Primary Blocker):** Pushing a local database to the PaaS and triggering a restore via the API forces the Liferay Cloud infrastructure to shut down the environment, wipe the volume, extract the backup, and restart.
   - A simple `lcp deploy` takes 10-15 minutes.
   - A database and volume restore can take **1 to 3 hours**.
   - A synchronous CLI command running on a developer's laptop is fundamentally ill-suited for this. If their machine sleeps or loses connection, the CLI loses state while the Cloud continues processing.
3. **Separation of Concerns:** LDM excels at bridging the gap *down* from the Cloud (`cloud-fetch` / `hydrate`) to rescue environments locally. Pushing back *up* to the Cloud is traditionally the domain of a CI/CD pipeline integrated with a proper Git repository, not a local scaffolding tool.

*Future Iterations:* If this track is revived, it should likely focus purely on generating the Git-ready PaaS workspace structure (`ldm export --format paas`), rather than attempting to directly trigger and monitor the cloud deployments.

---

## Overview

Implement an `ldm cloud-push` command that provides a seamless, two-way bridge between local LDM development and Liferay Cloud PaaS. This command will migrate a local LDM project to a remote Liferay Cloud environment by handling both data (database/document library) and code/configuration (client extensions/OSGi).

*Note on Naming:* We are establishing the semantic difference between `fetch` (downloading data without auto-integrating) and `push` (uploading and aggressively restoring to the remote target). When the broader CLI refactoring track is implemented, these will roll under the new `ldm cloud push` and `ldm cloud fetch` namespaces.

## Automation & CLI Alignment

To support CI/CD pipelines and headless automation, `cloud-push` MUST mirror the standard flags used by the `lcp` CLI and provide predictable exit codes.

### Required CLI Flags (Aligned with LCP)

- `-p, --project <project_id>`: Target Liferay Cloud project.
- `-e, --environment <env_id>`: Target Liferay Cloud environment.
- `-y, --yes` (or `--non-interactive`): Bypass all interactive prompts.
- `-q, --quiet`: Suppress progress animations and output minimal JSON/text for pipeline parsing.
- `--dry-run`: Validate paths, authentication, and targets without actually executing the upload or deployment.
- `--only-data`: Only perform Phase 2 (Database & Document Library upload/restore).
- `--only-code`: Only perform Phase 3 (Client Extension & Configuration deployment).

### Standard Exit Codes

- `0`: Success.
- `1`: General configuration or validation error.
- `2`: Authentication Error (Not logged into `lcp` or token expired).
- `3`: Data Push Failure (Backup upload failed or restore API returned an error).
- `4`: Code Push Failure (`lcp deploy` failed).

## Implementation Plan

### Phase 1: Pre-flight Checks & Target Selection

- **Goal:** Validate authentication and resolve the target Cloud project and environment.
- **Tasks:**
  - Verify LCP CLI is installed.
  - Extract the auth token using `lcp auth token`. Exit with code `2` if this fails.
  - If `-p` and `-e` are missing and `-y` is NOT set, use `lcp list --json` to prompt the user interactively (via `UI.ask_choices`).
  - If `--dry-run` is set, output a summary of the intended targets and exit `0`.

### Phase 2: The Data Push (`--only-data`)

- **Goal:** Push the local database and document library to Liferay Cloud.
- **Tasks:**
  - Trigger `ldm snapshot` to ensure the latest local state is archived (`database.gz` and `volume.tgz`).
  - Execute `lcp backup upload --project <p> --environment <e> --database <path> --doclib <path>`.
  - Implement an API call to `https://backup-{project}-{env}.lfr.cloud/backup/restore/from` using the token to trigger restoration.
  - Add polling to monitor the restore status. Exit with code `3` if any step fails.

### Phase 3: The Code & Config Push (`--only-code`)

- **Goal:** Package and deploy LDM configurations and client extensions to Liferay Cloud.
- **Tasks:**
  - Create a temporary directory structure (`.ldm_temp/paas_workspace`).
  - Generate a minimal `LCP.json` for the `liferay` service.
  - Map LDM's `common/` configurations into `.ldm_temp/paas_workspace/liferay/configs/common/`.
  - Copy LDM's `client-extensions/` into `.ldm_temp/paas_workspace/client-extensions/`.
  - Execute `lcp deploy --project <p> --environment <e>` from within `.ldm_temp/paas_workspace`. Exit with code `4` if deployment fails.

### Phase 4: Integration & UX Polish

- **Goal:** Wire the phases together into `ldm_core/cli.py` and `ldm_core/handlers/cloud.py`.
- **Tasks:**
  - Add the `cloud-push` command parser with all defined flags.
  - Implement stdout pipeline support: If `-q` is passed, suppress all `UI.info` spinners and only output the final JSON status or standard error streams.
  - Ensure robust cleanup of `.ldm_temp/paas_workspace` on both success and failure states.
