# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.7.2-beta.5] - 2026-05-15

### Fixed

- **Sidecar Search Enforcement**: LDM now explicitly sets `operationMode=EMBEDDED` in `portal-ext.properties` when in Sidecar mode. This ensures that Liferay ignores any conflicting `.config` files (e.g. from a shared 'common/' directory) and correctly initializes its internal search engine, resolving the "Blank Fragment" deployment issue.
- **Clean Search Isolation**: Removed the `indexNamePrefix` for Sidecar projects to align with standard Liferay behavior and avoid potential UI incompatibilities with prefixed indices.

## [v2.7.2-beta.4] - 2026-05-15

### Fixed

- **Sidecar Search Orchestration**: Explicitly configures both Elasticsearch 7 and 8 ports (9201/9301) for Sidecar mode. This ensures Liferay always finds its search engine regardless of the version being used.
- **Legacy Path Alignment**: Added volume mount for `/opt/liferay/modules` to match standard Liferay Docker expectations and ensure fragment persistence.

## [v2.7.2-beta.3] - 2026-05-15

### Fixed

- **Sidecar Search Orchestration**: LDM now explicitly informs Liferay of its Sidecar Elasticsearch ports (defaulting to 9201/9301). This resolves critical "Blank Fragment" issues caused by Liferay attempting to index fragments using the default port 9200 when the Sidecar engine was shifted to avoid collisions.
- Enhanced Persistence: Added persistent host-mounts for `osgi/modules` and `osgi/client-extensions` to align with standard Liferay Docker practices. This ensures that any dynamically processed or extracted fragment content is correctly persisted and accessible.

## [v2.7.2-beta.2] - 2026-05-15

### Added

- **Project Visibility**: The `ldm list` command now includes a **Path** column by default, making it easier to locate sandboxes on the host machine.
- **Regression Testing**: Added new automated tests to verify volume mapping accuracy, JVM flag injection, and tabulated project listing output.

### Fixed

- **Liferay Data Persistence**: Corrected the internal data volume mount point from `/storage/liferay/data` to `/opt/liferay/data`. This ensures that Liferay and its sidecar Elasticsearch correctly use the host-side `data/` directory, preventing data loss and fixing search index initialization issues.
- Fragment Deployment Resilience: Added `-Djdk.util.zip.disableZip64ExtraFieldValidation=true` to the default JVM options. This resolves issues where fragment collections appear empty after auto-deployment due to Zip64 metadata validation failures.

## [v2.5.0] - 2026-05-12

### Added

- **Snapshot Integrity Verification**: Mandatory SHA-256 checksum validation for all project snapshots and workspace imports.
- **Environment Overrides**: Support for `LDM_COMMON_DIR` environment variable to share configurations across different project roots.

### Changed

- **God Object Decomposition**: Complete architectural refactor of the core orchestration engine into specialized services (Snapshot, Workspace, Runtime, etc.).
- **Improved Test Coverage**: Reached 50% test coverage threshold with comprehensive unit and E2E interactivity tests.
- **Hardened Resource Resolution**: Centralized and architecture-aware resource path discovery.
- **Documentation Overhaul**: Updated technical design documents, security posture, and compatibility tables.

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

## [v2.4.26-beta.20] - 2026-04-27

### Added

- Resolved CI linting failures and hardened test suite.

## [v2.4.26-beta.19] - 2026-04-27

### Added

- Proactive log monitoring for errors during health checks.
- Dynamic JDBC driver and dialect resolution based on Liferay version.

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
