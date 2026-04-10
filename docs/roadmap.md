# LDM Future Roadmap & Improvements

This document outlines potential enhancements to the Liferay Docker Manager (ldm) to further improve its utility as a high-velocity evaluation and demonstration sandbox.

> [!NOTE]
> **Compatibility**: All sample assets (Client Extensions, Snapshots) are designed for and require **Liferay 2025.Q3** or newer.

---

- Route mapping and SSL expiry countdowns.
- Quick-access logs for each extension.

## 4. High-Velocity Boot (Pre-Seeded DBs)

- **Database "Fast-Forward"**: Support for downloading pre-initialized, empty database volumes.
- **Objective**: Reduce the "First Run" wait time from 15 minutes to under 2 minutes by bypassing the initial Liferay schema generation.

## 5. Shared Scenario Packs

- **Portable Scenarios**: A formal specification for bundling a Snapshot + Client Extensions + LCP Metadata into a single "Pack" for easy distribution across Sales Engineering teams.

## 6. AI-Assisted Orchestration

- **The `ldm ai` Command**: Integrate a specialized AI handler (Gemini-powered) to provide interactive help and troubleshooting.
- **Context-Aware Support**: Automatically inject `ldm doctor` reports and project metadata into AI queries to provide zero-copy troubleshooting for SSL, networking, and deployment failures.
- **Recipe Generation**: Ask the AI to generate complex `portal-ext.properties` or `LCP.json` configurations based on natural language descriptions.

---

## ✅ Completed Improvements (v1.6.x)

### **Orchestration & Stability (v1.6.11 Logic Audit)**

- **Self-Healing Infrastructure**: Restored dynamic Docker socket discovery for macOS with automated bridge recovery and fallback paths.
- **Reliable Configuration**: Moved critical infrastructure settings (SSL, Search, Clustering) to `portal-ext.properties` to ensure compatibility with modern Liferay property decoding.
- **Version-Aware Formatting**: Automatic switching between modern (`_`) and legacy (`__`) environment variable separators based on Liferay version.
- **Unified UTC Logs**: Aligned all health check timestamps with Liferay container logs (UTC) for seamless debugging.
- **Traefik v3 Optimization**: Explicit network labels (`traefik.docker.network`) to ensure reliable routing in modern environments.

### **Management & Diagnostics**

- **Enhanced `ldm doctor`**: Added deep-probes for macOS bridge network integrity, Elasticsearch API reachability, project metadata health, portal properties structural validity, OSGi Search configs, and infrastructure log health.
- **Proactive Pruning**: `ldm prune` now cleans up orphaned SSL certificates and Traefik configurations from the global cert store.
- **Standardized UI**: 2-space icon padding for a cleaner terminal experience across all platforms.
- **Pipeline-Ready Exit Codes**: Standardized return codes (0 for success, 1 for error, 130 for abort) across the entire command suite.

### **Developer Experience & Tooling**

- **Local Building Scripts**: Automated `scripts/package-*.sh` utilities for generating standalone binaries with injected build timestamps.
- **Secure Self-Management**: Implemented `ldm upgrade` with SHA-256 verification and `--repair` mode.
- **Modular Package Structure**: Refactored the monolithic script into a clean Python package (`ldm_core`) with specialized handlers.
- **Comprehensive Non-Interactive Support**: Added `-y / --non-interactive` support to all commands, including `prune`, `env`, `run`, and `log-level`.
