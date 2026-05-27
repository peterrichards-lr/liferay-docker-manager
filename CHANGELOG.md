# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.7.28] - 2026-05-21

### Fixed

- **Port Mapping Verification**: Fixed a hardcoded port issue in the terminal output where LDM would always report `http://localhost:8080` for local access, even when a custom `--port` (like `8082` in E2E tests) was configured.

## [2.7.27] - 2026-05-21

### Fixed

- **E2E Script Adjustments**: Updated the verification script to skip the behavioral sudo guard check in CI, matching LDM's CI root allowance.
- **Workflow Dependencies**: Added `which` to the Fedora verification job to support script-level path discovery.

## [2.7.26] - 2026-05-21

### Fixed

- **CI Test Stability**: Updated security policy tests to correctly account for the `GITHUB_ACTIONS` environment, fixing a false-positive failure in CI.
- **Workflow Resilience**: Hardened system dependency installation in the verification workflow. Now uses `python3-full` and `hostname` to ensure all platforms have necessary tools for automated testing.

## [2.7.25] - 2026-05-21

### Fixed

- **CI Root Execution**: LDM now automatically allows root execution when `GITHUB_ACTIONS=true` is detected. This prevents security warnings from corrupting output slugs in CI environments.
- **Workflow Dependencies**: Added missing system packages (`python3-venv`, `hostname`) to the new verification workflow to ensure smooth execution on Fedora and Ubuntu runners.

## [2.7.24] - 2026-05-21

### Fixed

- **JVM Argument Deduplication**: Fixed a critical bug in the JVM options parser that caused `-Xms` (initial heap) and `-Xmx` (max heap) to collide, resulting in JVM initialization failures in resource-constrained environments.

## [2.7.23] - 2026-05-21

### Added

- **Lean JVM Profile**: Introduced a resource-optimized JVM profile (2GB heap) for constrained environments.
- **Automatic CI Optimization**: LDM now automatically switches to the Lean profile when it detects a GitHub Actions environment, ensuring reliable Liferay boots on standard 7GB runners.
- **`--lean` Flag**: Added a manual flag to `run` and `import` for users on low-memory local machines.

## [2.7.22] - 2026-05-21

### Fixed

- **Intelligent `ldm env`**: Fixed a discrepancy where hitting "Enter" at the environment prompt would save but not apply shell variables.
- **Unattended Environment Sync**: Enabled `ldm env <project> -y` to automatically synchronize all passthrough shell variables (e.g. AI keys) without requiring explicit arguments, significantly easing CI automation.

## [2.7.21] - 2026-05-21

### Fixed

- **Polished DNS Output**: Cleaned up the pre-flight hostname verification to suppress redundant manual fix suggestions when multiple subdomains are missing. LDM now provides a concise, unified summary before offering the automated `/etc/hosts` fix.

## [2.7.20] - 2026-05-21

### Fixed

- **Polished Upgrade Errors**: Improved the error handling in `ldm upgrade` to provide user-friendly messages when elevated privilege requests fail, replacing raw command strings with actionable advice.

## [2.7.19] - 2026-05-21

### Added

- **Comprehensive Project Rollback**: Enhanced the atomic initialization logic to ensure failed brand-new projects are not only deleted from disk but also removed from the global registry.
- **Improved Workspace Cleanup**: `ldm import` now proactively cleans up the temporary extraction directory and its parent `.ldm_temp` folder on completion or failure.

## [2.7.18] - 2026-05-21

### Added

- **Atomic Project Initialization**: Implemented a "Commit/Rollback" pattern for `ldm run` and `ldm import`. If a brand-new project fails to initialize (e.g., due to DNS errors or build failures), LDM now automatically cleans up the half-baked directory to prevent inconsistent project states.
- Enhanced `ldm import` with the shared **Intelligent Subdomain Fixing** engine, ensuring consistency with the `run` command.

## [2.7.17] - 2026-05-21

### Fixed

- Fixed `ldm fix-hosts` to correctly fallback to treating the target as a direct hostname if no matching project is found.
- Hardened `_apply_hosts_fix` to prevent adding empty configuration blocks to `/etc/hosts`.
- Improved `check_hostname` to verify that domains resolve to local IPs, preventing false-positive resolution reports for remote addresses.

## [2.7.16] - 2026-05-21

### Fixed

- Fixed `ldm scale` command failing in non-interactive environments when the project was already running.

## [2.7.15] - 2026-05-21

### Documentation

- Formally documented the **Environment Variable Forwarding** logic in `README.md`, covering `LDM_` prefix stripping, automatic AI passthrough, and service-specific targeting.
- Documented the **Automatic Volume Hardening** behavior for macOS external drives.

## [2.7.14] - 2026-05-21

### Changed

- **Refactored Environment Forwarding**: Consolidated global and service-specific variable logic. LDM now supports:
  - **`LDM_` Strip-Forwarding**: Global variables (e.g. `LDM_VAR=xxx`) are forwarded to all containers with the prefix removed (`VAR=xxx`).
  - **Unified Passthrough**: Liferay Cloud and AI provider keys (`OPENAI_`, `GEMINI_`, etc.) are automatically forwarded as-is.
  - **Custom Passthrough**: Users can now extend the passthrough list by setting `LDM_FORWARD_PREFIXES` (comma-separated) on the host.

## [2.7.13] - 2026-05-21

### Added

- **Flexible `ldm deploy`**: Now accepts optional specific services or file paths (JAR, WAR, ZIP). ZIP files are intelligently handled as either Client Extensions or OSGi Fragments.
- **Dedicated `ldm wait` Command**: Standardized way to block scripts until Liferay is responding to HTTP requests.
- **Automatic Volume Hardening**: Proactively detects macOS `/Volumes/` paths and enables `--internal-state` for reliable OSGi file locking.
- **Automatic AI Environment Forwarding**: Host variables starting with `OPENAI_`, `GEMINI_`, `ANTHROPIC_`, or `MISTRAL_` are now automatically available inside all project containers.
- **Automatic Non-Interactive Hosts Fix**: When running with `-y`, LDM now attempts to fix missing `/etc/hosts` entries automatically using `sudo -n`.
- **Smarter Subdomain Fixing**: `ldm fix-hosts` and pre-flight checks now scan for active client extensions and include their required subdomains in the resolution check.

### Fixed

- **AutoDeployScanner Permissions**: Resolved "Unable to write" errors on Linux by ensuring the `osgi/` directory tree is included in proactive permission reclamation.
- **Zero-Race Atomic Deployment**: All file deployments now use hidden staging files with proactive permission fixups before the final atomic rename.
- **Developer Guardrails**: Pass-through for `-y/--non-interactive` to bypass internal developer mode prompts while maintaining protection for unattended CI environments.

## [2.7.11] - 2026-05-21

### Added

- **Smarter Subdomain Fixing**: Enhanced `ldm fix-hosts` and pre-flight checks to automatically identify and fix missing client extension subdomains. LDM now scans projects for active extensions and ensures every required URL resolves to 127.0.0.1 before starting.
- Updated documentation to clarify that LDM handles the entire project DNS tree (main host + wildcards/subdomains) automatically.

## [2.7.10] - 2026-05-21

### Fixed

- Implemented universal permission fixup (`chmod 666` and `chown 1000:1000`) for all file deployment operations on Unix. This definitively resolves the "Unable to write" errors in Liferay's `AutoDeployScanner` when LDM is running as root (e.g. in CI or with sudo).

## [2.7.9] - 2026-05-21

### Fixed

- Enabled true non-interactive execution for commands requiring elevation (`fix-hosts`, `upgrade`) on Linux and macOS. By passing `-y/--non-interactive`, LDM now uses `sudo -n` to suppress password prompts and fail fast if a password is required.

## [2.7.8] - 2026-05-21

### Documentation

- Formally documented the **Client Extension Routing & Wildcard SSL** logic in `README.md`.
- Reorganized the `README.md` to move the `LDM_COMMON_DIR` section under "Configuration Files" for better logical flow.

## [2.7.7] - 2026-05-21

### Documentation

- Formally documented the **Zero-Race Atomic Deployment Strategy** in `docs/LDM_ARCHITECTURE.md`, detailing the staging and permission fixup pattern.

## [2.7.6] - 2026-05-21

### Fixed

- Hardened atomic deployment logic by ensuring Unix permission fixups occur on hidden staging files *before* they are moved into Liferay's scanner path. This eliminates the race condition where `AutoDeployScanner` could see a file before its ownership was handed off to the `liferay` user.

## [2.7.4] - 2026-05-21

### Fixed

- Fixed permission errors (`Unable to write [file.zip]`) in `AutoDeployScanner` on Linux host environments (e.g. GitHub Actions) by ensuring the `osgi` directory is targeted during proactive volume permission reclamation.

## [2.7.3] - 2026-05-21

### Fixed

- Fixed port check conflict where `ldm import` would start a project and `ldm run` would fail.
- Added runtime state-awareness checks to commands (run, import) to prevent unexpected container collisions.
- Enabled non-interactive bypass for internal developer utility prompts.

## [v2.8.36] - 2026-05-27

### Added

-

## [v2.8.35] - 2026-05-27

### Added

-

## [v2.8.34] - 2026-05-27

### Added

-

## [v2.8.33] - 2026-05-27

### Added

-

## [v2.8.32] - 2026-05-27

### Added

-

## [v2.8.31] - 2026-05-27

### Added

-

## [v2.8.30] - 2026-05-27

### Added

-

## [v2.8.29] - 2026-05-27

### Added

-

## [v2.8.28] - 2026-05-27

### Added

-

## [v2.8.27] - 2026-05-27

### Added

-

## [v2.8.26] - 2026-05-27

### Added

-

## [v2.8.25] - 2026-05-27

### Added

-

## [v2.8.24] - 2026-05-27

### Added

-

## [v2.8.23] - 2026-05-27

### Added

-

## [v2.8.22] - 2026-05-27

### Added

-

## [v2.8.21] - 2026-05-27

### Added

-

## [v2.8.20] - 2026-05-27

### Added

-

## [v2.8.19] - 2026-05-27

### Added

-

## [v2.8.18] - 2026-05-27

### Added

-

## [v2.7.2-beta.40] - 2026-05-18

### Fixed

- **Enhanced Readiness Detection**: Updated `ldm run` and E2E scripts to monitor Liferay logs for the Tomcat "Server startup" marker. This provides a faster and more reliable signal that Liferay is ready for access, especially in CI environments where the Docker healthcheck status may be significantly delayed.
