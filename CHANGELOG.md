# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

## [v2.7.2-beta.40] - 2026-05-18

### Fixed

- **Enhanced Readiness Detection**: Updated `ldm run` and E2E scripts to monitor Liferay logs for the Tomcat "Server startup" marker. This provides a faster and more reliable signal that Liferay is ready for access, especially in CI environments where the Docker healthcheck status may be significantly delayed.
