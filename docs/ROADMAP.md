# LDM Future Roadmap & Improvements

This document outlines potential enhancements to the Liferay Docker Manager (ldm) to further improve its utility as a high-velocity evaluation and demonstration sandbox.

> [!NOTE]
> **Compatibility**: All sample assets (Client Extensions, Snapshots) are designed for and require **Liferay 2025.Q3** or newer.

---

## 🚀 Future Roadmap: v2.9.0+ (Polished Diagnostics & Self-Healing)

While the v2.4.0 release solidified the modular architecture, the focus will now shift to implementing the remaining Ecosystem features.

### 1. CLI Simplification & Automation Standards

- **Namespace Grouping**: Transition from flat commands to grouped namespaces (e.g., `ldm system`, `ldm infra`, `ldm cloud`).
- **Cloud Orchestration**: Introduce `ldm cloud push` and `ldm cloud fetch` as a unified, two-way bridge for PaaS migration.

### 2. AI-Assisted Orchestration

- **The `ldm ai` Command**: Integrate a specialized AI handler (Gemini-powered) for troubleshooting.

### 3. Project Self-Healing (`ldm repair`)

- **Inconsistency Recovery**: Implement a command to restore projects in "half-baked" states (e.g., missing `docker-compose.yml` but valid `meta`).
- **Permission Re-sync**: A dedicated trigger to re-apply the "Zero-Race" permission fixups across an entire existing project stack.

### 4. Interactive Configuration Management

- **TUI & Web Dashboard Editing**: Extend the Properties Inspector drawer in the diagnostics web interface to support inline property editing and toggle controls. Add a corresponding `ldm config edit --tui` terminal user interface.
  - **LOE**: M (Medium)
  - **Risk**: Medium (requires robust write validations)
  - **Business Value**: High (prevents manual filesystem edit errors)
  - **Priority**: P2

### 5. Config Integrity & Validation

- **Pre-Flight Properties Analyzer**: Add static verification rules during `rebuild-properties` to detect unclosed quotes, malformed JDBC URLs, conflicting overrides (e.g. Hypersonic and PostgreSQL active at the same time), and missing mount paths.
  - **LOE**: S (Small)
  - **Risk**: Low (static checking only)
  - **Business Value**: High (reduces debug cycles caused by bad configurations)
  - **Priority**: P1

### 6. Multi-Environment Target Profiles

- **Profile Switching**: Introduce a profile switching system (e.g. `ldm profile switch <profile>`) to support running LDM using environment-specific properties layers (matching Dev, QA, or Production configurations).
  - **LOE**: M (Medium)
  - **Risk**: Medium (adds layering resolution complexity)
  - **Business Value**: High (guarantees local-to-cloud profile parity testing)
  - **Priority**: P2

### 7. Smart Cache & Hydration Optimization

- **Selective Snapshot Hydration**: Introduce hash-based file change detection for heavy volume document library archives (`volume.tgz`) and lazy-loading document assets on-demand instead of blocking container boots.
  - **LOE**: L (Large)
  - **Risk**: High (sync-state alignment tracking)
  - **Business Value**: Critical (reduces developer setup/restore time from minutes to seconds)
  - **Priority**: P1

### 8. Real-Time Log Analytics & Troubleshooting Engine

- **Error-Pattern OSGi Matching**: Build a background log listener that streams Liferay logs to the Web Dashboard, matching stack traces (unresolved OSGi constraints, db deadlocks, JVM memory warning limits) against known resolution recipes.
  - **LOE**: M (Medium)
  - **Risk**: Low (passive analysis)
  - **Business Value**: High (tremendous UX value for junior developers resolving boot errors)
  - **Priority**: P2

## ✅ Completed Improvements (v2.11.x - Sequential Overrides & Web Diagnostics)

### **Visual Properties Cascade Hierarchy**

- **5-Layer Overrides Cascade**: Integrated a strict precedence hierarchy (Seed, LDMP overrides, Global Common, Workspace Common, and Project manual edits).
- **CSS-style Precedence**: Added support for inline and preceding `# !important` markers to override standard precedence cascading rules.
- **Diagnostics Web Dashboard Inspector**: Created a color-coded visual drawer in the UI displaying winning property origins, active values, and full cascade override history.
- **CLI Properties Management**: Implemented `--rebuild-properties`, `--reset-properties`, and `--revert-properties` configuration management subcommands.

### **Diagnostics Web Dashboard & Dry Run**

- **Lightweight Diagnostics Dashboard**: Built a responsive visual web dashboard showing high-level RAG status, container states, and system information.
- **Dry-Run Mode Configuration**: Added simulated change preview outputs (`--dry-run`) across properties sync and resetting utilities.
- **Shared Scenario Bundles**: Fully standardized `.ldmp` package archives to restore workspace snapshots and Liferay properties baselines.

## ✅ Completed Improvements (v2.10.x - Liferay Cloud Golden Path & Hardening)

### **Liferay Cloud Golden Path**

- **Smart Workspace Recognition**: Added automatic heuristics in `init-from` and `import` to detect Liferay Cloud Git repositories.
- **Guided Hydration Wizard**: Built an interactive setup wizard to retrieve and synchronize LCP backup databases and document library assets.
- **Robust Backup Organization**: Programmatically flattens Cloud nested folder structures and automatically detects `AdvancedFileSystemStore` vs `FileSystemStore`.
- **Database Scrubbing & Wipes**: Strips proprietary Cloud commands (e.g. `\restrict`) and implements schema wipe loops to ensure clean database imports without superuser privileges.
- **Volume Extraction Permissions**: Hardened volume permission adjustments via tar pipelines, resolving macOS VirtioFS hypervisor synchronization lag (Sync Wait).

### **Platform Hardening & Diagnostics**

- **Self-Tuning JVM scaling**: Scales JVM resource settings proactively during search reindexing.
- **CLI Automation Standardization**: Implemented standard exit codes (0-4, 126) and non-interactive (`-y`) mode flags across all developer utilities.
- **PTY safety on macOS**: Refactored elevated privilege tasks to bypass sub-process allocation, ensuring macOS terminal stability.
- **Documentation Restructure**: Decoupled monolithic README into specialized guides in `docs/guides/` and documented optional/mandatory third-party tool dependencies.

## ✅ Completed Improvements (v2.8.0 - Stability & Automation)

### **Advanced Orchestration**

- **3-Phase Readiness Gating**: Introduced a high-precision `ldm wait` command that combines log markers, HTTP probing, and **CPU Idle detection** (< 15% load) to guarantee Liferay is fully stabilized before proceeding.
- **Local Cloud Hydration**: Implemented the `ldm hydrate` command, allowing developers to recreate full project environments from local Liferay Cloud backup archives (`database.gz` and `volume.tgz`).
- **Lean JVM Profile**: Added a resource-optimized JVM profile for 7GB runners, with **automatic GHA detection** to prevent OOM kills in CI/CD pipelines.
- **Atomic Initialization & Rollback**: Hardened project creation with a commit/rollback pattern that automatically cleans up failed initialization attempts.

### **Cross-Platform Verification**

- **Automated Multi-OS Verification Matrix**: Refactored CI to run the full E2E suite on Ubuntu and Fedora (via explicit Docker orchestration).
- **Non-Interactive Sudo**: Enabled `sudo -n` support for elevated tasks when running in `-y` mode, ensuring unattended scripts never hang on password prompts.

## ✅ Completed Improvements (v2.4.0 - StackHandler Modularization)

### **Architectural Modularity**

- **Specialized Handlers**: Decomposed the monolithic 1,700+ line `StackHandler` into focused, testable components: `ComposerHandler` (generation), `RuntimeHandler` (lifecycle), and `AssetHandler` (discovery/offline-first).
- **Improved Testability**: Enabled mocking of specific subsystems (e.g., Orchestration vs. Assets) without requiring the entire manager instance.
- **Automated Version Management**: Introduced the `ldm version` command for logical version bumping (`--bump beta|patch|minor|major`) and release tier promotion (`--promote`). This ensures synchronization across all source files, automatically maintains `CHANGELOG.md` headers, and includes atomic writes with safety guardrails for development environments.

### **Integrity & Compliance**

- **Project Registry Restoration**: Re-implemented the global project registry to provide robust name and hostname collision detection across the entire filesystem.
- **SHA-256 Integrity Verification**: Introduced mandatory checksumming for all snapshots and pre-warmed seeds to ensure data validity during `restore` and `import` operations.
- **Memory Limit Hardening**: Updated `ComposerHandler` to automatically enforce Megabyte (`M`) units for Docker resource limits, ensuring cross-platform compatibility.
- **Synchronized Environment Updates**: The `ldm env` command now triggers an automatic `docker-compose.yml` synchronization, ensuring environment changes are immediately reflected in the infrastructure layer.

---

## ✅ Completed Improvements (v2.3.6 - Release Control & Final Hardening)

### **CI/CD & Delivery Control**

- **Explicit Release Gating**: Re-engineered the GitHub Release workflow to support intentional full releases. All version tags now default to **Pre-release** status unless the commit message explicitly includes the `[release]` keyword.
- **Manual Workflow Triggers**: Added `workflow_dispatch` support to the CI pipeline for on-demand build and verification runs.
- **E2E Verification Suite**: Established a comprehensive end-to-end verification script (`scripts/verify_e2e_refactor.sh`) that validates global infrastructure, project orchestration, and CLI disambiguation in a live Docker environment.

### **Core Stability & UX Restoration**

- **Architectural Contract Verification**: Introduced a mock-free test suite to ensure mandatory Docker labels and domain trust settings are never dropped during refactoring.
- **Refined Project Disambiguation**: Standardized project name resolution across all CLI commands to correctly distinguish between project IDs and service names (e.g., `liferay`, `db`, `proxy`).
- **Instance Isolation & Port Probing**: Implemented IP-specific port binding and enhanced pre-flight checks to allow multiple Liferay instances to coexist via loopback IPs.
- **Improved Log Feedback**: Added `--no-wait` and `--tail` support to `ldm logs` and implemented multi-service tailing.
- **Hardened Data Integrity**: Implemented Atomic ("Safe") Writes for all configuration files and added schema validation for project metadata.
- **Productivity Boosts**: Integrated Fuzzy Project Selection into interactive prompts and added the `ldm edit` command for rapid configuration management.
- **Automation Hardening**: Ensured all commands correctly bypass interactive prompts in non-interactive (`-y`) mode.

---

## ✅ Completed Improvements (v2.3.0 - Workspace-Aware Seeding & Refactoring)

### **High-Velocity Orchestration**

- **Workspace-Aware Seeding Boost**: Extended the high-performance seeding engine to `import`, `init-from`, and `cloud-fetch` commands. LDM now automatically detects the required Liferay version and bootstraps matching v2 seeds, reducing "first boot" for imported projects from 15 minutes to under 60 seconds.
- **Cloud-Native Seeding**: `ldm cloud-fetch` now probes remote Liferay Cloud environments to identify the exact image tag and leverages version-matched seeds for local restoration.

### **Architectural Cleanup & Reliability**

- **Infrastructure Extraction**: Extracted global service orchestration (Traefik, Search, Proxy) into a dedicated `InfraHandler` mixin, decoupling global infra from project-specific logic.
- **Centralized Utilities**: Consolidated metadata handling and project discovery into `ldm_core/utils.py`, ensuring a single source of truth for the entire application.
- **Expanded Verification Suite**: Added dedicated unit tests for the new Infrastructure and Utility modules, increasing total coverage to 95 verified test cases.

---

## ✅ Completed Improvements (v2.2.0 - The Ecosystem Phase)

### **Ecosystem Planning & Orchestration**

- **Track-Based Implementation**: Established a formal [Conductor Registry](https://github.com/liferay/liferay-portal/tree/master/modules/util/portal-tools-bundle-builder) for feature planning, providing detailed implementation paths for upcoming v2.3 features.
- **Deep OSGi Seeding**: Integrated `osgi/state` folder into the core seeding engine (v2 Seeds). LDM now pre-resolves bundle dependencies during the seeding phase, reducing secondary boot times by an additional 2-3 minutes.
- **Selective Seeding Control**: Introduced the `--no-osgi-seed` flag to allow developers to opt-out of state seeding when performing low-level bundle development.
- **Infrastructure Idempotency**: Fixed Docker conflict errors where LDM would fail if infrastructure containers existed in a stopped state. The orchestrator now correctly identifies and starts them.
- **Project Naming Integrity**: Resolved a bug where bootstrapped projects would inherit generic container names from seed metadata; user-chosen names are now strictly enforced.
- **CLI Robustness**: Fixed a critical regression where `ldm run` on non-existent projects would fail during pre-flight path detection. Correctly handles initialization for all new projects.

---

## ✅ Completed Improvements (v2.1.x & v2.5.x)

- **Extensible Stack Archetypes & External DB (v2.5.x)**: Replaced application scaffolding with a declarative overlay architecture via `ldm init -a <archetype>`. Includes full topology generation for `keycloak-sso` (OIDC injection) and `clustered` (JGroups TCPPING and Traefik sticky sessions), alongside a decoupled `--db external` workflow.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-16* | *Last Reviewed: 2026-07-02*
