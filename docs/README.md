# Liferay Docker Manager (ldm)

A professional command-line orchestrator for quickly standing up Liferay Portal and DXP environments using Docker Compose.

> [!NOTE]
> **Project History:** This tool was originally born as part of the [liferay-docker-scripts](https://github.com/peterrichards-lr/liferay-docker-scripts) repository. It has since evolved into a standalone application to provide better modularity and multi-instance stability.

---

## 🎥 Seeing is Believing (Showcase)

Check out our [Video Showcase](./showcase/README.md) to see short demonstrations of LDM in action, including Fast Provisioning, Cloud Hydration, and instant Snapshots & Restoration!

---

## Why use LDM?

`ldm` is designed to be both fast for power users and helpful for newcomers. It follows a consistent usage pattern:

1. **Sensible Defaults**: Whenever a standard Liferay convention exists, `ldm` uses it automatically (e.g., port `8080`, managed DB name `lportal`).
2. **Smart Context**: If you run a command from inside a project folder, `ldm` automatically detects the project context.
3. **Interactive Fallback**: If a required piece of information (like a project name or a Liferay tag) is missing from your command and cannot be detected, `ldm` will **prompt you interactively** or show you a list of choices.
4. **Graceful Abort**: You can type `q` at any interactive prompt to safely cancel the operation.

---

## 📋 Default Stack & Conventions (Quick Reference)

When running `ldm run <project-name>` (or creating a fresh stack targeting the latest LTS) with default options, LDM sets up the environment using the following out-of-the-box profile:

| Component | Default Configuration / Convention |
| :--- | :--- |
| **Liferay Version** | Automatically fetches and runs the latest **LTS** version if no specific tag is specified. |
| **Database** | **PostgreSQL** running as a dedicated service container in the project's Docker network (configured with DB/user/password: `lportal`). <!-- pragma: allowlist secret --> |
| **Search Engine** | **Shared Global Search** (Elasticsearch 8.x) running as a shared background service (minimizes CPU/RAM overhead compared to sidecar containers). |
| **Routing & Proxy** | **Traefik** routing HTTP traffic (port `8080`) and HTTPS traffic (port `443` via auto-generated `mkcert` local trust certificates). |
| **JVM Settings** | Self-Tuning JVM with optimal dev-mode settings (e.g., bytecode verification disabled via `-Xverify:none` to speed up start times). |
| **Volumes & Mounts** | **Hybrid Volume Strategy**: POSIX-lock sensitive directories (`osgi/state`, `data`) use Named Docker Volumes to prevent locking deadlocks; hot-reloading directories (`deploy`, `modules`, `client-extensions`) are bind-mounted to the host. |
| **Default Hostname** | Resolves to `localhost` (or `<project-name>.local`). |

---

## 🛡️ Compatibility (Verified Environments)

The badges below represent our verified support for various Docker providers. Environments marked as **Hardened** have received specific logic refinements to handle complex file-sharing and permission scenarios.

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.4.0` | ![Colima](https://img.shields.io/badge/Colima-Hardening-FFAB00?style=flat-square&logo=apple) | `2.11.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-colima-pass.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-colima-pass.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **OrbStack** `v2.1.1` | `29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo=apple) | `2.11.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-orbstack-pass.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-orbstack-pass.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** `v4.35.0` | `29.4.0` | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardening-00C853?style=flat-square&logo=windows) | `2.11.2` | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** `WSL 2.4.4` | `29.3.0` | ![WSL2](https://img.shields.io/badge/WSL2-Hardening-blue?style=flat-square&logo=windows) | `2.11.2` | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support. ES 8.17.x+ required for Liferay 2025.Q2+ (ES 7 deprecated). |
<!-- COMPATIBILITY_END -->

---

## Key Features

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

---

## Documentation

### 1. Getting Started & Core Reference

- [Installation Guide](INSTALLATION.md) — Detailed environment setup for macOS (Colima/OrbStack), Linux, and Windows (WSL2).
- [CLI Reference & Automation](guides/CLI_REFERENCE.md) — CLI commands, global options, and scripting parameters.
- [Troubleshooting & Diagnostics](TROUBLESHOOTING.md) — Core error handling guides, Docker locks, and common overrides.
- [Compatibility Table](COMPATIBILITY_TABLE.md) — Matrix of tested Docker engines, providers, and host OS versions.

### 2. Operational & Feature Guides

- [PaaS "Golden Path" & Demo Rescue](guides/PAAS_LOCAL_DEV.md) — Replicating and debugging remote Liferay Cloud environments locally.
- [Workspace Import & Portable Packaging](guides/WORKSPACE_IMPORT_AND_PACKAGING.md) — Exporting, sharing, and restoring portable `.ldmp` release packages.
- [Properties Override Hierarchy](guides/PROPERTIES_HIERARCHY.md) — Understanding the 5-layer cascading properties and `# !important` override rules.
- [Sharing & Tunnels (lfr-tunnel & Ngrok)](guides/SHARING_AND_TUNNELS.md) — Exposing local projects securely to public subdomains for client reviews.
- [Data Management (Snapshots, Seeds, Hydration)](guides/DATA_MANAGEMENT.md) — Database/volume backup structures and remote asset fetching.
- [Networking, DNS & Zero-Config SSL](guides/NETWORKING_DNS.md) — Traefik configurations, custom domains, and local cert trust.

### 3. Integration & Developer Guides

- [Development & Building](guides/DEVELOPMENT.md) — Local building, egg info packaging, and developer setup.
- [AI Command Center & LDM MCP Server](guides/AI_MCP_GUIDE.md) — Extending AI developer tooling with LDM FastMCP tools.
- [Advanced CLI Tuning](guides/ADVANCED_CLI.md) — Tuning global switches, terminal coloring, and ASCII formatting overrides.
- [Operational Playbook & Automation Specs](PLAYBOOK.md) — Pipelines, release procedures, and issue management.
- [Testing & E2E Validation Strategy](TESTING.md) — Running unit, mock, and full integration E2E test suites.
- [Architecture Overview](LDM_ARCHITECTURE.md) — Micro-architecture, layers of abstraction, and boundary constraints.
- [Security Posture & Disclosures](SECURITY.md) — Restricted secrets management.
- [Third-Party Tools list](THIRD_PARTY_TOOLS.md) — Internal utilities, mkcert, and docker dependencies.
- [Future Roadmap](ROADMAP.md) — Planned features and strategic GTM tools.

## Installation (Quick Start)

The standalone binary is the recommended way to use LDM. Copy and run the block specific to your environment:

### macOS (Apple Silicon)

```bash
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos-arm64 -o /usr/local/bin/ldm
sudo chmod +x /usr/local/bin/ldm
```

### macOS (Apple Intel)

```bash
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos-x86_64 -o /usr/local/bin/ldm
sudo chmod +x /usr/local/bin/ldm
```

### Linux / WSL2

```bash
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-linux -o /usr/local/bin/ldm
sudo chmod +x /usr/local/bin/ldm
```

For detailed instructions and Windows setup, see the **[Full Installation Guide](installation.md)**.

---

## Quick Start

> [!IMPORTANT]
> **Binary vs Script**: If you have installed the **Standalone Binary**, use `ldm` in your commands. If you are using the **Manual Installation**, use `./ldm` (on Linux/macOS) or `ldm.bat` (on Windows) from the root of this repository.

```bash
# 1. THE CONFIDENCE BOOSTER: Run Liferay with pre-configured samples
ldm run my-sample-project --samples

# 2. THE SCAFFOLDING FLOW: Initialize a project folder without starting containers
ldm init my-new-project --tag 2024.q4.0 --db mysql

# 3. THE DEVELOPER FLOW: Initialize from a workspace and start monitoring
ldm init-from /path/to/workspace my-project

# 4. THE ARCHIVE FLOW: Import a static snapshot of a workspace
ldm import /path/to/workspace my-static-project

# 5. THE RECOVERY FLOW: Re-create a deleted project from a snapshot folder
ldm run my-recovered-project --snapshot ~/Desktop/old-baseline-snapshot

# 6. THE SURGICAL FLOW: Instantly edit project metadata
ldm edit my-project

# 7. THE PREDEFINED QUICKSTART: Bootstrap an accelerator demo stack
ldm quickstart aica

# 8. THE PORTABLE PACKAGE: Export project snapshot into a .ldmp package
ldm package my-project

# Monitor an existing project (manually)
ldm monitor /path/to/workspace
```

## 🛡️ Sudo & Elevation Policy

To protect the integrity of the application cache (`~/.shiv`) and ensure consistent file permissions, **never run LDM with the `sudo` prefix.**

LDM is designed to run as a standard user and will **automatically request elevation** (prompting for your password) only for specific tasks that require it:

- **`ldm fix-hosts`**: Requires elevation to append entries to `/etc/hosts`. Can be called manually or triggered automatically during `ldm run`.
- **`ldm upgrade`**: Automatically handles cross-device file systems (e.g. Fedora /tmpfs) by using a `cp` + `rm` pattern for system binary replacement. Requires elevation to replace the binary in system paths like `/usr/local/bin`. On Windows, it utilizes PowerShell's User Account Control (UAC) to seamlessly request administrative privileges when replacing binaries in protected directories like `System32`.

> [!NOTE]
> **Non-Interactive Sudo**: When running with the `-y` or `--non-interactive` flag, LDM uses `sudo -n` to perform elevated tasks. If a password is required, the command will fail fast and cleanly instead of hanging the terminal. This is essential for CI/CD pipeline stability.

If you are using `sudo` because of Docker "Permission Denied" errors, do not use `sudo ldm`. Instead, add your user to the `docker` group:

```bash
sudo usermod -aG docker $USER
```

Then restart your terminal session.

---

## Prerequisites

- **Docker Engine**: Docker Desktop, Colima, or native WSL2.
- **Docker Compose**: **v2 (Plugin)** is mandatory. Legacy v1 standalone is not supported.
- **Resources**: Recommended **4 CPUs and 8GB RAM** allocated to Docker.
  - *Note*: `ldm doctor` expects these minimums. If you allocate exactly 8GB, Docker may report ~7.7GB due to system overhead; the tool accounts for this by allowing a 7.5GB threshold.
- **Python**: 3.10+ (if not using binary)
- **mkcert**: (Optional) For automated local SSL.

### Increasing Resources in Colima

If `ldm doctor` reports insufficient resources in Colima, you can increase them with these commands:

```bash
colima stop
colima start --cpu 4 --memory 8
```

---

## License

MIT © Peter Richards
