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

### **Orchestration & Automation**

- **Fail-Fast Architecture**: Proactive environment and dependency verification (Compose, Volume Mounts, SSL) before any stack operation.
- **Strict Environment Uniqueness**: Dictionary-based environment generation to prevent "non-unique items" errors in Compose files.
- **Pipeline-Ready Exit Codes**: Standardized return codes (0 for success, 1 for error, 130 for abort) across the entire command suite.
- **Comprehensive Non-Interactive Support**: Added `-y / --non-interactive` support to all commands, including `prune`, `env`, `run`, and `log-level`.
- **Intel Mac Hardening**: Specialized exception for `x86_64` macOS architecture to prefer legacy `docker-compose` when v2 plugin is misidentified.

### **Infrastructure & Core**

- **Universal Socket & Provider Detection**: Support for Colima, OrbStack, WSL2, and native Linux by dynamically detecting active endpoints.
- **Secure Self-Management**: Implemented `ldm upgrade` with SHA-256 verification and `--repair` mode.
- **Modular Package Structure**: Refactored the monolithic script into a clean Python package (`ldm_core`) with specialized handlers.
- **Visible Infrastructure Store**: Centralized SSL certificates and routing configs in `~/liferay-docker-certs`.
- **UI Spacing Polish**: Standardized icon spacing with double-space padding for professional terminal output.

### **Management & Diagnostics**

- **Enhanced `ldm doctor`**: Added the `--skip-project` flag for pure environmental health checks and automated pipeline integration.
- **Security Posture Disclosure**: Created `docs/SECURITY.md` to document intentional `0.0.0.0` bindings and security scan tradeoffs.
- **Professional CI/CD Pipeline**: Implemented GitHub Actions for security scanning (Bandit), multi-language linting, and automated smoke testing.
- **Standalone Distribution**: Automated packaging into architecture-specific executables distributed through GitHub Releases.
- **Global Maintenance (`ldm prune`)**: Reliable identification and removal of orphaned containers and search snapshots.
