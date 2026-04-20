# LDM Future Roadmap & Improvements

This document outlines potential enhancements to the Liferay Docker Manager (ldm) to further improve its utility as a high-velocity evaluation and demonstration sandbox.

> [!NOTE]
> **Compatibility**: All sample assets (Client Extensions, Snapshots) are designed for and require **Liferay 2025.Q3** or newer.

---

## 🚀 Future Roadmap: v2.0.0 (The Ecosystem Phase)

While the v1.x "Hardened Edition" focused on cross-platform stability, **v2.0.0** will shift focus toward developer onboarding and "Scenario-First" orchestration.

### 1. Guided Onboarding & Scaffolding

- **Interactive Scaffolding**: Implement `ldm init` with guided templates for common use cases (e.g., "Commerce Demo," "Headless React Integration").
- **Documentation Injection**: Automatically generate `README.md` and `setup.md` within new projects to onboard team members to that specific project's capabilities.

### 2. CLI Simplification (Namespacing)

- **Namespace Grouping**: Transition from flat commands to grouped namespaces to reduce cognitive load.
  - `ldm system renew-ssl` / `ldm system prune`
  - `ldm infra setup` / `ldm infra down`
- **Legacy Compatibility**: Maintain flat aliases for 1.x power users.

### 3. Visual Health Dashboard

- **Local Monitoring UI**: A lightweight, read-only dashboard accessible via `http://localhost:19000` to visualize:
  - Container health using real-time SVG status indicators.
  - Route mapping and SSL expiry countdowns.
  - Quick-access logs for each extension.

### 4. High-Performance Boot

- **OSGi State Persistence**: Optionally persist `osgi/state` to skip bundle resolution on subsequent starts.
- **Smart Bundle Blacklisting**: Performance-tuned baseline to disable non-essential development services.
- **Pre-computed Search Indexes**: Persistent global search volumes to avoid full re-indexing on first boot.

### 5. Shared Scenario Packs

- **Portable Scenarios**: A formal specification for bundling a Snapshot + Client Extensions + LCP Metadata into a single "Pack" for easy distribution across Sales Engineering teams.

### 6. AI-Assisted Orchestration

- **The `ldm ai` Command**: Integrate a specialized AI handler (Gemini-powered) to provide interactive help and troubleshooting.
- **Context-Aware Support**: Automatically inject `ldm doctor` reports and project metadata into AI queries to provide zero-copy troubleshooting for SSL, networking, and deployment failures.
- **Recipe Generation**: Ask the AI to generate complex `portal-ext.properties` or `LCP.json` configurations based on natural language descriptions.

### 7. Strategic Hardening & Fleet Management

- **Snapshot Integrity Verification**: Implement SHA-256 checksumming for Project Snapshots to guarantee data validity when shared across teams.
- **Bulk Stack Management**: Introduce workspace-wide commands (e.g., `ldm stop --all`, `ldm status --workspace`) for bird's-eye fleet control.
- **Auto-Healing DNS**: Add an optional `--fix-dns` flag to `ldm doctor` that automatically appends missing subdomains to `/etc/hosts` using standard OS elevation.

---

## ✅ Completed Improvements (v2.1.x)

### **High-Performance Boot & Hardening (v2.1.2)**

- **Database "Fast-Forward"**: Added support for downloading pre-initialized, version-matched "Seed" volumes (Database + Search Index) from GitHub. Reduces first-run wait times significantly.
- **Resilient Tag Discovery**: Upgraded the discovery engine to support both HTML (`releases.liferay.com`) and JSON (Docker Hub) listings, ensuring stability against upstream API changes.
- **TLD Scanning Optimization**: Automatically skips Tomcat TLD scanning for known non-UI JARs to accelerate boot times.
- **Volume Consistency Tuning**: Native support for `:cached` and `:delegated` mounts on macOS and Windows to improve disk I/O performance.
- **JVM Dev-Mode Tuning**: Optional `--no-jvm-verify` flag to disable bytecode verification for faster class loading in demo environments.
- **Shell Completion (Python 3.13)**: Resolved critical `argcomplete` compatibility issues and optimized Zsh initialization for faster terminal performance.
- **Cloud-Fetch Hardening**: Improved LCP CLI integration to support legacy versions lacking JSON output.
- **DNS Self-Healing**: Restored `check_hostname` logic to provide proactive validation and instructions for local `/etc/hosts` alignment.

## ✅ Completed Improvements (v1.8.x)

### **Fleet Management & DNS Automation (v1.8.0)**

- **Bulk Lifecycle Commands**: Introduced `--all` support for `stop`, `restart`, `down`, and `logs` to manage your entire local fleet in one command.
- **Global Fleet Status**: `ldm status --all` now displays an overview of all managed projects, including stopped ones, providing a bird's-eye view of your workspaces.
- **Auto-Healing DNS**: Added `ldm doctor --fix-hosts` to automatically detect and append missing virtual host entries to your system's `/etc/hosts` file (supports macOS, Linux, and Windows).
- **Consolidated SSL Renewal**: `ldm renew-ssl --all` for rapid, workspace-wide certificate refreshes.

## ✅ Completed Improvements (v1.7.x)

### **Hardening & UX Refinements (v1.7.0)**

- **Atomic Configuration Writes**: All file operations (metadata, properties, compose) use a safe write-to-tmp-then-rename pattern to prevent corruption.
- **Pre-Flight Port Conflict Detection**: Proactively verifies host port availability (80, 443, 9200, etc.) before orchestration to provide clear error messages.
- **Metadata Schema Validation**: Automatic integrity checks for `.liferay-docker.meta` files with user-friendly warnings for missing keys.
- **Fuzzy Project Selection**: The interactive project menu now supports real-time filtering—just start typing to narrow down large project lists.
- **Multi-Service Log Tailing**: Added support for concurrently streaming logs from multiple containers (e.g., `ldm logs liferay my-extension`).
- **Interactive Configuration Editor**: New `ldm edit` command to instantly modify project metadata or portal properties in your preferred `$EDITOR`.
- **Non-Blocking Update Checks**: Update discovery now runs in a daemonized background thread, eliminating startup network latency.

## ✅ Completed Improvements (v1.6.x)

### **Orchestration & Stability (v1.6.11 Logic Audit)**

- **Self-Healing Infrastructure**: Restored dynamic Docker socket discovery for macOS with automated bridge recovery and fallback paths.
- **Reliable Configuration**: Moved critical infrastructure settings (SSL, Search, Clustering) to `portal-ext.properties` to ensure compatibility with modern Liferay property decoding.
- **Version-Aware Formatting**: Automatic switching between modern (`_`) and legacy (`__`) environment variable separators based on Liferay version.
- **Unified UTC Logs**: Aligned all health check timestamps with Liferay container logs (UTC) for seamless debugging.
- **Traefik v3 Optimization**: Explicit network labels (`traefik.docker.network`) to ensure reliable routing in modern environments.

### **Management & Diagnostics**

- **Proactive License Verification**: Automatically detects and parses Liferay XML licenses in `common/`, `deploy/`, and `osgi/modules/` folders with expiration alerts.
- **Enhanced `ldm doctor`**: Added deep-probes for macOS bridge network integrity, Elasticsearch API reachability, project metadata health, portal properties structural validity, OSGi Search configs, and infrastructure log health.
- **Proactive Pruning**: `ldm prune` now cleans up orphaned SSL certificates and Traefik configurations from the global cert store.
- **Standardized UI**: 2-space icon padding for a cleaner terminal experience across all platforms.
- **Pipeline-Ready Exit Codes**: Standardized return codes (0 for success, 1 for error, 130 for abort) across the entire command suite.

### **Developer Experience & Tooling**

- **Local Building Scripts**: Automated `scripts/package-*.sh` utilities for generating standalone binaries with injected build timestamps.
- **Secure Self-Management**: Implemented `ldm upgrade` with SHA-256 verification and `--repair` mode.
- **Modular Package Structure**: Refactored the monolithic script into a clean Python package (`ldm_core`) with specialized handlers.
- **Comprehensive Non-Interactive Support**: Added `-y / --non-interactive` support to all commands, including `prune`, `env`, `run`, and `log-level`.
