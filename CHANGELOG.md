# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.7.2-beta.16] - 2026-05-15

### Added

- **Improved Log-Level UX**: Made the `--bundle` parameter optional in the `ldm log-level` command. It now defaults to `portal`, allowing users to set log levels by category only (e.g. `ldm log-level --level DEBUG --category com.liferay.fragments`).

## [v2.7.2-beta.15] - 2026-05-15

### Added

- **Port-less Log Level Management**: Refactored `ldm log-level` to use file-based hot-reloading via `portal-log4j-ext.xml`. By adding `monitorInterval="5"` to the configuration, Liferay now picks up log level changes from the disk within 5 seconds, removing the need for Gogo shell, Telnet, or any administrative ports.

## [v2.7.2-beta.14] - 2026-05-15

### Fixed

- **Property Recursion Crash**: Surgically removed the `include-and-override` property from `portal-ext.properties` when disabling developer mode. This definitively resolves the `java.lang.StackOverflowError` caused by infinite property loading loops.
- **Enhanced Configuration Control**: Implemented a new `remove_portal_ext` utility in the configuration service to allow for safer, non-additive cleanup of portal properties.

## [v2.7.2-beta.13] - 2026-05-15

### Fixed

- **Transaction Race Prevention**: Explicitly disabled the automatic inclusion of `portal-developer.properties`. This restores Liferay's internal caches, preventing "Duplicate Key" race conditions and transaction rollbacks during bulk fragment deployments.
- **Database Resource Hardening**: Increased PostgreSQL container resources to 1GB shared buffers and 200 max connections. This provides the necessary IO overhead for heavy concurrent write operations.

## [v2.7.2-beta.12] - 2026-05-15

### Fixed

- **Absolute Parity**: Removed explicit `LIFERAY_HOME` environment variable and implemented JVM argument deduplication to ensure a clean container profile that matches standard manual Docker scripts.
- **Volume Re-alignment**: Restored host-mapped `osgi/state` and `logs` for standard instances to maintain performance while preserving scaling isolation.

## [v2.7.2-beta.11] - 2026-05-15

### Fixed

- **Performance Optimization Restriction**: Restricted the `TieredStopAtLevel=1` and `Xverify:none` JVM flags to macOS/Windows VM environments only. This prevents potential race conditions in background indexing on native Linux (Fedora).
- **Volume Mount Parity**: Removed the direct `osgi/configs` bind-mount and aligned the `modules` mount to match the successful manual script. This ensures Liferay uses internal defaults for search configuration and avoids metadata precedence conflicts.
- **Missing Scripts Mount**: Re-added the missing `scripts` volume mount to ensure project-specific initialization scripts are correctly executed.

## [v2.7.2-beta.10] - 2026-05-15

### Fixed

- **Memory Guardrails**: Reduced the default Liferay heap allocation from 75% to 50% of available RAM. This prevents system starvation on 16GB machines, ensuring Sidecar Elasticsearch and the host OS have enough overhead to operate reliably.
- **Sidecar Connectivity**: Forced Sidecar Elasticsearch to bind to `0.0.0.0` (world-writable) for guaranteed internal reachability and added exhaustive transport port property keys.
- **Auto-Deploy Stability**: Slowed down the default auto-deploy interval to 5 seconds to prevent deadlocks and indexing failures during large batch fragment deployments.

## [v2.7.2-beta.9] - 2026-05-15

### Fixed

- **Recursive Permission Reclamation**: LDM now performs a recursive `chmod 777` on the `data/` directory during project startup on non-Windows systems. This ensures that Liferay has write access to critical subfolders like `data/license/`, breaking the infinite license loop and enabling background tasks like search indexing.
- **Improved JDBC Stability**: Removed the redundant `jdbc.default.enabled=true` property from the default configuration. This resolves a `NullPointerException` in modern Liferay versions using HikariCP and provides a cleaner startup log.

## [v2.7.2-beta.8] - 2026-05-15

### Fixed

- **Exhaustive Sidecar Configuration**: LDM now explicitly injects both `sidecarTransportTcpPort` and `transportTcpPort` property keys into `portal-ext.properties`. This ensures that Liferay correctly initializes its internal search engine regardless of minor version differences in configuration property names.
- **Accurate Diagnostic Reporting**: Updated `ldm doctor` to correctly detect and report 'SIDECAR mode active' for local projects, preventing misleading 'REMOTE' configuration warnings.

## [v2.7.2-beta.7] - 2026-05-15

### Fixed

- **Precise Config Suppression**: Refined the Sidecar isolation logic to target exact Liferay configuration filenames. This ensures that only specific conflicting search settings are suppressed for Sidecar projects, while leaving other related configurations untouched.

## [v2.7.2-beta.6] - 2026-05-15

### Fixed

- **Aggressive Sidecar Isolation**: LDM now explicitly unlinks (deletes) any conflicting `ElasticsearchConfiguration.config` files from `osgi/configs` when in Sidecar mode. This prevents 'REMOTE' configurations from the shared `common/` folder from overriding local settings, guaranteeing that Liferay initializes its internal search engine and indexes fragments correctly.

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
- **Enhanced Persistence**: Added persistent host-mounts for `osgi/modules` and `osgi/client-extensions` to align with standard Liferay Docker practices. This ensures that any dynamically processed or extracted fragment content is correctly persisted and accessible.

## [v2.7.2-beta.2] - 2026-05-15

### Added

- **Project Visibility**: The `ldm list` command now includes a **Path** column by default, making it easier to locate sandboxes on the host machine.
- **Regression Testing**: Added new automated tests to verify volume mapping accuracy, JVM flag injection, and tabulated project listing output.

### Fixed

- **Liferay Data Persistence**: Corrected the internal data volume mount point from `/storage/liferay/data` to `/opt/liferay/data`. This ensures that Liferay and its sidecar Elasticsearch correctly use the host-side `data/` directory, preventing data loss and fixing search index initialization issues.
- **Fragment Deployment Resilience**: Added `-Djdk.util.zip.disableZip64ExtraFieldValidation=true` to the default JVM options. This resolves issues where fragment collections appear empty after auto-deployment due to Zip64 metadata validation failures.

## [v2.5.0] - 2026-05-12

### Added

- **Snapshot Integrity Verification**: Mandatory SHA-256 checksum validation for all project snapshots and workspace imports.
- **Environment Overrides**: Support for `LDM_COMMON_DIR` environment variable to share configurations across different project roots.

### Changed

- **God Object Decomposition**: Complete architectural refactor of the core orchestration engine into specialized services (Snapshot, Workspace, Runtime, etc.).
- **Improved Test Coverage**: Reached 50% test coverage threshold with comprehensive unit and E2E interactivity tests.
- **Hardened Resource Resolution**: Centralized and architecture-aware resource path discovery.
- **Documentation Overhaul**: Updated technical design documents, security posture, and compatibility tables.
