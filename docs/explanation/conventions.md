# LDM Conventions & Key Features

> [!NOTE]
> This guide details Liferay Docker Manager (LDM) conventions, default profiles, and core architectural features that optimize local Liferay Portal/DXP development stacks.

---

## 📋 Default Stack & Conventions (Quick Reference)

When running `ldm run <project-name>` (or creating a fresh stack targeting the latest LTS) with default options, LDM sets up the environment using the following out-of-the-box profile:

| Component | Default Configuration / Convention |
| :--- | :--- |
| **Liferay Version** | Automatically fetches and runs the latest **LTS** version if no specific tag is specified. |
| **Database** | **PostgreSQL** running as a shared Global Infrastructure container (`liferay-db-global`), minimizing memory consumption across multiple projects (configured with DB/user/password: `lportal`). Prior to `v2.14.0`, LDM defaulted to an isolated sidecar container for every project, prioritizing isolation over resource consumption. <!-- pragma: allowlist secret --> |
| **Search Engine** | **Shared Global Search** (Elasticsearch 8.x) running as a shared background service (minimizes CPU/RAM overhead). Prior to `v2.14.0`, this also defaulted to an isolated sidecar container per project. Custom remote search clusters and sidecar instances are dynamically supported via interactive prompt conflict resolution. |
| **Routing & Proxy** | **Traefik** routing HTTP traffic (port `8080`) and HTTPS traffic (port `443` via auto-generated `mkcert` local trust certificates). |
| **JVM Settings** | Self-Tuning JVM with optimal dev-mode settings (e.g., bytecode verification disabled via `-Xverify:none` to speed up start times). |
| **Volumes & Mounts** | **Hybrid Volume Strategy**: POSIX-lock sensitive directories (`osgi/state`, `data`) use Named Docker Volumes to prevent locking deadlocks; hot-reloading directories (`deploy`, `modules`, `client-extensions`) are bind-mounted to the host. |
| **Default Hostname** | Resolves to `localhost` (or `<project-name>.local`). |

---

## 🛠️ Key Features

- **Multi-Instance Session Isolation**: Run multiple demos side-by-side on the same machine without session cross-talk. `ldm` automatically manages unique session cookie names and virtual hostnames.
- **Strict Infrastructure Isolation**: Projects using the `--sidecar` search mode are cryptographically isolated from the global infrastructure. LDM guarantees that sidecar projects will not start, stop, or reconfigure the shared Global Search container, even if global search flags are provided.
- **Orchestrated Search Snapshots**: Save and restore the exact state of a demonstration, including the database, files, and **Elasticsearch 8.x index state**.
- **Service-Specific Lifecycle**: Manage individual components within a project surgically. Restart or view logs for a single extension without affecting the rest of the stack.
- **Client Extension Lifecycle**: Automatically detects and builds Server-Side Client Extensions (SSCE). Subdomains are automatically generated, and traffic is routed based on `LCP.json`.
- **Zero-Config SSL**: Automated HTTPS using `mkcert` and a global Traefik proxy. Works on Docker Desktop, **Colima**, and **WSL2**.
- **Proactive License Verification**: Automatically detects Liferay XML licenses in `common/`, `deploy/`, or `osgi/modules/` folders. Warns you before boot if a DXP license is missing or expired.
- **Fail-Fast Design**: Proactive environment checking. LDM verifies Docker reachability, volume mounts, resource allocations (CPU/RAM), and **Compose functionality** before execution, providing clean, actionable error messages instead of tracebacks.
- **Port Conflict Detection**: Proactively verifies that required host ports (80, 443, 9200, etc.) are available before starting, preventing cryptic Docker errors.
- **Atomic Configuration**: All project metadata and property updates use safe atomic writes to prevent file corruption during interruptions.
- **Integrity Verification**: As of v2.5.0, all project snapshots and pre-warmed bootstrap seeds include mandatory **SHA-256 checksums**. LDM automatically verifies these during recovery and import to ensure data validity. Users can bypass this check using the `--no-verify` flag if necessary for legacy or manually modified snapshots.
- **Global Project Registry**: Proactively detects project and hostname collisions across the entire filesystem, preventing infrastructure conflicts.
- **Lean JVM Profile**: Includes a resource-optimized JVM profile (`-Xms1536m -Xmx2048m`) designed for constrained environments. LDM **automatically detects** GitHub Actions and applies this profile to ensure reliable boots on 7GB runners. Use the `--lean` flag to trigger this manually.
- **Atomic Project Initialization**: Employs a "Commit/Rollback" pattern for new projects. If initialization fails (e.g. DNS errors or image pull failures), LDM automatically cleans up the half-baked project directory and unregisters it to prevent zombie states.
- **Zero-Race Atomic Deployments**: All file synchronizations (via `ldm deploy` or the `deploy/` bind mount) are staged, permission-fixed (`chmod 666`, `chown 1000:1000`), and atomically moved. This definitively eliminates `AutoDeployScanner` "Unable to write" errors.
- **Architecture-Aware**: The tool detects your OS automatically to fetch the correct optimized binary during self-updates.
- **Shell Autocompletion**: TAB completion for commands and project names across Bash, Zsh, and Fish.
- **Fuzzy Interactive Selection**: Quickly filter through dozens of projects by typing a few characters in any interactive menu.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-23* | *Last Reviewed: 2026-07-02*
