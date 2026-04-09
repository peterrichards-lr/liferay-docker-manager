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

### 4. High-Velocity Boot (Pre-Seeded DBs)

- **Database "Fast-Forward"**: Support for downloading pre-initialized, empty database volumes.
- **Objective**: Reduce the "First Run" wait time from 15 minutes to under 2 minutes by bypassing the initial Liferay schema generation.

### 5. Shared Scenario Packs

- **Portable Scenarios**: A formal specification for bundling a Snapshot + Client Extensions + LCP Metadata into a single "Pack" for easy distribution across Sales Engineering teams.

---

## ✅ Completed Improvements (v1.5.x)

### **Infrastructure & Core**

- **Modular Package Structure**: Refactored the monolithic script into a clean Python package (`ldm_core`) with specialized handlers.
- **Standardized Project Discovery**: Implemented a consistent 4-step discovery priority (Arg > Flag > CWD > Interactive Selector) across all commands.
- **Multi-Instance Traefik Isolation**: Implemented project-specific namespacing and SNI-based routing to prevent collisions between parallel environments.
- **Universal Socket & Provider Detection**: Added support for Colima, OrbStack, WSL2, and native Linux by dynamically detecting and using the active Docker socket path.
- **Secure Self-Management**: Implemented `ldm upgrade` with SHA-256 integrity verification and `--repair` mode for fixing tampered binaries.
- **Visible Infrastructure Store**: Centralized SSL certificates and routing configs in a non-hidden host directory (`~/liferay-docker-certs`) for cross-provider reliability.

### **Orchestration & Workflow**

- **Multi-Node Simulation**: Implement `ldm scale [service]=N` to spin up multiple Liferay or extension containers with automated Traefik routing and Liferay clustering.
- **Full Stack Import Engine**: Scaffolds projects from standard/Cloud workspaces or archives. Includes automatic Gradle builds and orchestrated backup restoration.
- **Managed Database Containers**: Automatic provisioning of PostgreSQL 16 and MySQL 5.7 containers to support local data restoration.
- **Orchestrated Search Snapshots**: Linked Elasticsearch 8.x snapshots to project backups, ensuring search indices stay in sync with the database and files.
- **Service-Specific Lifecycle**: Added support for targeting individual containers (e.g., `ldm logs [project] liferay` or `ldm restart [project] [service]`).

### **Management & Diagnostics**

- **Professional CI/CD Pipeline**: Implemented GitHub Actions for security scanning (Bandit, pip-audit), multi-language linting, and automated smoke testing.
- **Standalone Distribution**: Automated packaging of LDM into architecture-specific executables (`ldm-macos`, `ldm-linux`, `ldm-windows.exe`) via `shiv` and `PyInstaller`, distributed through GitHub Releases.
- **Project Visibility (`ldm list`)**: Tabulated overview of all initialized environments, versions, and current statuses.
- **Resource Guard (`ldm doctor`)**: proactive verification of Docker host CPU and Memory allocations to prevent runtime failures.
- **Global Maintenance (`ldm prune`)**: Reliable identification and removal of orphaned containers from deleted projects.
- **Infrastructure Lifecycle**: Added `ldm infra-setup` and `ldm infra-down` for independent management of global services (Traefik, Search, Bridge).
- **Shell Shortcuts**: Added `ldm shell` for instant bash access and `ldm gogo` for direct OSGi console interaction.
- **Dynamic Logging**: Manage internal Log4j2 levels without restarts via `ldm log-level`.
- **Automatic Browser Launch**: Smart URL detection based on `browser.launcher.url` project property.
