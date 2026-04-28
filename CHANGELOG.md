# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.4.26-beta.17] - 2026-04-27

### Added

- Hardened search orchestration and version-aware diagnostics.

## [v2.4.26-beta.16] - 2026-04-27

### Added

- DNS cleanup implemented.

## [v2.4.26-beta.15] - 2026-04-27

### Added

- Fixed missing health check status updates on Windows/WSL.

## [v2.4.26-beta.14] - 2026-04-27

### Added

- CHANGELOG automation implemented.

## [v2.4.26-beta.13] - 2026-04-27

### Fixed

- **Dev Environment**: Resolved `AttributeError: 'Namespace' object has no attribute 'yes'` in `_ensure_dev_env`.

## [v2.4.26-beta.12] - 2026-04-27

### Fixed

- **Update Detection**: Hardened GitHub API integration with `no-cache` headers and SemVer scanning across all releases.
- **CI/CD**: Corrected redundant download URL paths in release note templates.

## [v2.4.26-beta.10] - 2026-04-27

### Fixed

- **Pre-flight Checks**: Prevented tool crash when DNS resolution fails immediately after a hosts file update by adding a safety fallback to 127.0.0.1.

## [v2.4.26-beta.9] - 2026-04-27

### Added

- **DNS Cleanup**: Implemented surgical hosts entry removal via `--clean-hosts` flag on `down`, `rm`, and `prune`.
- **Safety Hatch**: Users on beta builds can now switch back to stable tier via `ldm upgrade`.

## [v2.4.26-beta.6] - 2026-04-27

### Added

- **Version Utility**: Initial implementation of the `ldm version` developer command for automated SemVer management.

## [v2.4.25] - 2026-04-27

### Fixed

- **Search Orchestration**: Standardized on `__` separator and fixed ES8 config volume mapping.
- **WSL Permissions**: Added `logs/` to the proactive permission fixer.
- **Host Mapping**: Standardized on explicit hosts entries and added `.local` TLD warnings for Windows.
- **Upgrades**: Resolved 'Invalid cross-device link' errors on Linux.
