# LDM Future Roadmap & Improvements

This document outlines potential enhancements to the Liferay Docker Manager (ldm) to further improve its utility as a high-velocity evaluation and demonstration sandbox.

> [!NOTE]
> **Compatibility**: All sample assets (Client Extensions, Snapshots) are designed for and require **Liferay 2025.Q3** or newer.

---

## 🚀 Future Roadmap: v2.5.0 (The Ecosystem Phase Continued)

While the v2.4.0 release solidified the modular architecture, **v2.5.0** will focus on implementing the remaining Ecosystem features.

### 1. Guided Onboarding & Scaffolding

- **Interactive Scaffolding**: Implement `ldm init` with guided templates for common use cases (e.g., "Commerce Demo," "Headless React Integration").
- **Documentation Injection**: Automatically generate `README.md` and `setup.md` within new projects.

### 2. CLI Simplification (Namespacing)

- **Namespace Grouping**: Transition from flat commands to grouped namespaces (e.g., `ldm system`, `ldm infra`).

### 3. Visual Health Dashboard

- **Local Monitoring UI**: A lightweight, read-only dashboard accessible via `http://localhost:19000`.

### 4. Shared Scenario Packs

- **Portable Scenarios**: A formal specification for bundling Snapshots and Client Extensions into single distributable archives.

### 5. AI-Assisted Orchestration

- **The `ldm ai` Command**: Integrate a specialized AI handler (Gemini-powered) for troubleshooting.

---

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

- **Track-Based Implementation**: Established a formal [Conductor Registry](../conductor/index.md) for feature planning, providing detailed implementation paths for upcoming v2.3 features.
- **Deep OSGi Seeding**: Integrated `osgi/state` folder into the core seeding engine (v2 Seeds). LDM now pre-resolves bundle dependencies during the seeding phase, reducing secondary boot times by an additional 2-3 minutes.
- **Selective Seeding Control**: Introduced the `--no-osgi-seed` flag to allow developers to opt-out of state seeding when performing low-level bundle development.
- **Infrastructure Idempotency**: Fixed Docker conflict errors where LDM would fail if infrastructure containers existed in a stopped state. The orchestrator now correctly identifies and starts them.
- **Project Naming Integrity**: Resolved a bug where bootstrapped projects would inherit generic container names from seed metadata; user-chosen names are now strictly enforced.
- **CLI Robustness**: Fixed a critical regression where `ldm run` on non-existent projects would fail during pre-flight path detection. Correctly handles initialization for all new projects.

---

## ✅ Completed Improvements (v2.1.x)
