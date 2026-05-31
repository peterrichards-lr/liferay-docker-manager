# Track: CLI Exit Code Standardization

## Overview

To support headless automation and CI/CD pipelines, all LDM commands must adopt a standardized set of predictable exit codes. Currently, LDM commands often default to generic failure states or rely heavily on human-readable error messages. This track aligns LDM with best practices for CLI automation, making it natively compatible with scripting environments and CI runners.

## Automation & CLI Alignment

This track establishes a universal contract for exit codes across the entire `ldm` application, including both the existing flat commands and the upcoming namespaced commands (like `ldm cloud push`).

### Standard Exit Code Contract

* `0`: **Success**. The command completed its primary objective without critical errors.
* `1`: **Generic/Validation Error**. Invalid configuration, missing files, or unexpected runtime exceptions.
* `2`: **Authentication/Permission Error**. Missing LCP token, insufficient file system privileges, or expired sessions.
* `3`: **Infrastructure/Data Error**. Docker daemon unreachable, backup upload failed, or network timeout.
* `4`: **Orchestration/Deployment Error**. Container failed to start within timeout, or `lcp deploy` failed.
* `126`: **Command Invocation Error**. Invalid CLI arguments or flags.

## Implementation Plan

### Phase 1: Core Framework Integration

* Update `ldm_core/ui.py` to ensure methods like `UI.die()` accept an optional `exit_code` parameter (defaulting to 1).
* Define a centralized `ExitCode` enum or constant class in `ldm_core/constants.py` to store the standard codes, ensuring magic numbers aren't scattered throughout the codebase.

### Phase 2: Retrofitting Existing Commands

* Audit all exit calls across `ldm_core/handlers/*.py`.
* Map existing failure states to the new standard exit codes.
* Specifically update critical automation commands like `ldm run`, `ldm import`, `ldm snapshot`, and `ldm wait` to return granular exit codes for infrastructure failures (`3`) vs. deployment timeouts (`4`).

### Phase 3: Alignment with Future Tracks

* Ensure the upcoming `cloud-push` and `cloud-fetch` implementations strictly adhere to this contract from day one.
* Update the CLI documentation (`docs/CLI_REFERENCE.md`) and `ldm --help` outputs to explicitly define the expected exit codes for automation engineers.
