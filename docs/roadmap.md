# LDM Future Roadmap & Improvements

This document outlines potential enhancements to the Liferay Docker Manager (ldm) to further improve its utility as a high-velocity evaluation and demonstration sandbox.

> [!NOTE]
> **Compatibility**: All sample assets (Client Extensions, Snapshots) are designed for and require **Liferay 2025.Q3** or newer.

---

## 🚀 Potential Next Steps

As the core orchestration and isolation pillars are now robustly implemented, future efforts could focus on:

### 1. Enhanced Cloud Synchronization

**Objective**: Deepen the integration with Liferay Cloud environments.

- **Automated Fetch**: Implement `ldm cloud-fetch` to pull logs or metadata directly from an LCP project via CLI.
- **Environment Parity**: Add validation to ensure local `LCP.json` resource limits closely match the target Cloud environment.

### 2. Multi-Node Simulation

**Objective**: Test clustering and load-balancing behavior locally.

- **Implementation**: Allow `ldm scale liferay=2` to spin up a second Liferay container.
- **Routing**: Update the Traefik labels to support round-robin balancing between the nodes.

---

## ✅ Completed Improvements

### **Infrastructure & Core**

- **Modular Package Structure**: Refactored the monolithic script into a clean Python package (`ldm_core`) with specialized handlers.
- **Standardized Project Discovery**: Implemented a consistent 4-step discovery priority (Arg > Flag > CWD > Interactive Selector) across all commands.
- **Multi-Instance Traefik Isolation**: Implemented project-specific namespacing for all Traefik routers and services to prevent routing conflicts between parallel environments.
- **Universal Socket Detection**: Added support for Colima, WSL2, and native Linux by detecting and using the standard Docker socket when available.

### **Orchestration & Workflow**

- **Full Stack Import Engine**: Scaffolds projects from standard/Cloud workspaces or archives. Includes automatic Gradle builds and orchestrated backup restoration.
- **Managed Database Containers**: Automatic provisioning of PostgreSQL 16 and MySQL 5.7 containers to support local data restoration.
- **Orchestrated Search Snapshots**: Linked Elasticsearch 8.x snapshots to project backups, ensuring search indices stay in sync with the database and files.
- **Service-Specific Lifecycle**: Added support for targeting individual containers (e.g., `ldm logs [project] liferay` or `ldm restart [project] [service]`).

### **Management & Diagnostics**

- **Project Visibility (`ldm list`)**: Tabulated overview of all initialized environments, versions, and current statuses.
- **Resource Guard (`ldm doctor`)**: proactive verification of Docker host CPU and Memory allocations to prevent runtime failures.
- **Global Maintenance (`ldm prune`)**: Reliable identification and removal of orphaned containers from deleted projects.
- **Shell Shortcuts**: Added `ldm shell` for instant bash access and `ldm gogo` for direct OSGi console interaction.
- **Dynamic Logging**: Manage internal Log4j2 levels without restarts via `ldm log-level`.
- **Automatic Browser Launch**: Smart URL detection based on `browser.launcher.url` project property.
