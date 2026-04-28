# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<<<<<<< HEAD
<<<<<<< HEAD
=======
=======
## [v2.4.26-beta.29] - 2026-04-28

### Added

- Automated compatibility table updates from verification reports via 'scripts/sync_compatibility.py'.
- Standardized documentation sync markers across README.md and COMPATIBILITY_TABLE.md.

## [v2.4.26-beta.28] - 2026-04-27

### Added

- Enhanced E2E verification scripts to generate portable 'ldm-verification-results.txt' reports.
- Reports now include environment metadata (via 'ldm doctor') and full test logs.

## [v2.4.26-beta.27] - 2026-04-27

### Added

- Native PowerShell E2E verification script ('scripts/verify_e2e_refactor.ps1') for Windows testing parity.

## [v2.4.26-beta.26] - 2026-04-27

### Added

- Created 'docs/TROUBLESHOOTING.md' with instructions for moving Docker data to external drives.
- Added proactive 'ldm doctor' hints that link to migration tips when disk pressure is detected.

## [v2.4.26-beta.25] - 2026-04-27

### Added

- Resolved path.repo bug enabling Global Search snapshots.
- Suppressed transient ES8 startup warnings in diagnostics.

## [v2.4.26-beta.24] - 2026-04-27

### Added

- Formalized compatibility matrix as a release asset.
- Bundled 'compatibility.json' for offline baseline support.

## [v2.4.26-beta.23] - 2026-04-27

### Added

- Automated cleanup in E2E verification script.
- Hardened global search initialization and resolved WSL connectivity errors.

## [v2.4.26-beta.22] - 2026-04-27

### Added

- Orchestrated database snapshots via docker exec.
- Suppressed browser launching during automated testing.

## [v2.4.26-beta.21] - 2026-04-27

### Added

- Resolved CI linting failures and hardened test suite.

>>>>>>> 13d6ab2 (feat: environmental hardening, snapshots, and automated verification suite [pre-release])
## [v2.4.26-beta.20] - 2026-04-27

### Added

- Resolved CI linting failures and hardened test suite.

## [v2.4.26-beta.19] - 2026-04-27

### Added

- Proactive log monitoring for errors during health checks.
- Dynamic JDBC driver and dialect resolution based on Liferay version.

>>>>>>> d37f0cb (fix: resolve CI linting failures and harden test suite [pre-release])
## [v2.4.26-beta.18] - 2026-04-27

### Added

- Dynamic compatibility metadata for dependency versions (ES, MySQL, PSQL).
- Hardened version management utility.

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
