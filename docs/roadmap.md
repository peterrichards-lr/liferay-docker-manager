# LDM Future Roadmap & Improvements

This document outlines potential enhancements to the Liferay Docker Manager (ldm) to further improve its utility as a high-velocity evaluation and demonstration sandbox.

> [!NOTE]
> **Compatibility**: All sample assets (Client Extensions, Snapshots) are designed for and require **Liferay 2025.Q3** or newer.

---

## 🚀 Future Roadmap: v2.4.0 (The Ecosystem Phase Continued)

While the v2.3.x release solidified the core architecture, **v2.4.0** will focus on implementing the remaining Ecosystem features.

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

## ✅ Completed Improvements (v2.3.1 - Hardening, Reliability & UX Restoration)

### **Core Stability & Verification**

- **Architectural Contract Verification**: Introduced a mock-free test suite (`test_architectural_contracts.py`) to verify that refactoring never silently drops mandatory Docker labels or domain trust settings.
- **Positional Disambiguation**: Implemented a powerful heuristic to distinguish between project names and service names (e.g., `ldm logs liferay` now correctly identifies the service even inside project folders).
- **Hardened Status Reporting**: Restored the `com.liferay.ldm.project` label to all Liferay services, ensuring `ldm status` correctly identifies and reports active projects.

### **UX & Reliability Refinements**

- **Domain Trust Recovery**: Re-implemented proactive `portal-ext.properties` updates for `web.server.display.node.name` and `redirect.url.ips.allowed` when using custom hostnames.
- **Improved Log Feedback**: Added `--no-wait` flag to `ldm logs` and implemented real-time user feedback while waiting for containers to become available.
- **Automation Hardening**: Verified and hardened all commands to bypass interactive prompts in non-interactive (`-y`) mode, preventing hangs in CI/CD environments.
- **Infrastructure Reliability**: Fixed missing environment variables (`LDM_CERTS_DIR`) during infrastructure teardown and log viewing.

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
