# Liferay Docker Manager (ldm)

A professional command-line orchestrator for quickly standing up Liferay Portal and DXP environments using Docker Compose.

---

## 🎥 Showcase

Check out our **[Video Showcase](docs/showcase/README.md)** to see short demonstrations of LDM in action, including Fast Provisioning, Cloud Hydration, and instant Snapshots & Restoration!

---

## 🚀 Quick Start

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

### Windows (PowerShell)

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\bin"
Invoke-WebRequest -Uri "https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-windows.exe" -OutFile "$HOME\bin\ldm.exe"
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$HOME\bin", "User")
```

For detailed platform-specific prerequisites, see the **[Full Installation Guide](docs/tutorials/quick_start.md)**.

---

## 📋 Common Commands

Once installed, bootstrap or run your stacks instantly:

```bash
# 1. THE CONFIDENCE BOOSTER: Run Liferay with pre-configured samples
# For more information see LDM Conventions & Features
ldm run my-sample-project --samples

# 2. THE VANILLA FLOW: Run a fresh vanilla Liferay instance (LTS release)
# For more information see Fresh Vanilla Start
ldm run my-vanilla-project --vanilla --tag 2026.q1.7-lts

# 3. THE DEVELOPER FLOW: Link a local workspace and start monitoring
# For more information see Liferay Workspace Local Dev
ldm link /path/to/workspace my-project

# 4. THE PREDEFINED QUICKSTART: Bootstrap an accelerator demo stack
# For more information see LDM Conventions & Features
ldm quickstart aica

# 5. THE TIME MACHINE: Take a snapshot of your database and volumes, and restore them later
# For more information see Data Management & Backup Snapshots
ldm snapshot my-project
ldm restore my-project

# 6. THE CLONE FLOW: Clone and setup a remote Git workspace repository
# For more information see Liferay Workspace Local Dev
ldm clone https://github.com/my-org/my-workspace.git my-project

# 7. THE PORTABLE PACKAGE: Export and Import compiled project snapshots (.ldmp)
# For more information see Portable Packages & Remote Repositories (.ldmp)
ldm package my-project
ldm import /path/to/my-project.ldmp

# 8. THE MULTI-NODE FLOW: Register compute targets and live-migrate workloads
# For more information see Multi-Node Orchestration & Remote Node Setup
ldm target add prod-aws --host 34.200.10.5 --user ubuntu --key ~/.ssh/aws-key.pem
ldm target migrate local prod-aws
```

> [!NOTE]
> **Headless & Seeding Prompt Behaviors**:
>
> - By default, LDM checks for a cached pre-warmed database seed. If not cached, it will prompt you interactively to download it.
> - **CI/CD / Headless Scripts**: To prevent interactive prompts from blocking headless environments, pass `-y` / `--yes` / `--non-interactive` to automatically confirm seed downloads, or pass `--vanilla` ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue) / `--no-seed` to skip seeding entirely and start a clean baseline database.

**Legacy Commands Note**:
`ldm init-from` has been **deprecated** and is superseded by the `ldm link` command.

---

## 📚 Documentation Signposts

LDM is conventions-driven and highly customizable. Choose a topic below for detailed information:

### 1. Getting Started

- **[Installation Guide](docs/tutorials/quick_start.md)** — Setting up macOS (Colima/OrbStack), Linux, and Windows (WSL2).
- **[LDM Conventions & Features](docs/explanation/conventions.md)** — Default stacks, ports, database options, and key features.
- **[Compatibility Matrix](docs/reference/compatibility.md)** — Supported host OS, Docker providers, and engines.
- **[Troubleshooting & Diagnostics](docs/TROUBLESHOOTING.md)** — Logs, Docker deadlocks, port conflicts, and common fixes.

### 2. Local Development & Customization

- **[Multi-Node Orchestration & Remote Node Setup](docs/how-to/multi_node_orchestration.md)** — Registering compute nodes, remote stack execution, and live workload migration (`ldm target`).
- **[Fresh Vanilla Start](docs/how-to/vanilla_start.md)** — Launching empty Liferay instances for quick tests.
- **[PaaS "Golden Path" Local Dev](docs/tutorials/paas_local_dev.md)** — Fetching backups and replicating Liferay Cloud environments locally.
- **[Liferay Workspace Local Dev](docs/tutorials/workspace_development.md)** — Linking local workspaces with LDM for active source code development.
- **[Liferay Version Upgrades](docs/how-to/version_upgrades.md)** — Safely upgrading Liferay Docker image tags, database backup snapshots, and schema auto-upgrades.
- **[Runtime Overrides & Fragments](docs/how-to/runtime_overrides.md)** — Dynamic substitution and environment-aware client extension patching.
- **[Properties Hierarchy & Precedence](docs/explanation/properties.md)** — The 5-layer cascading properties and `# !important` overrides.

### 3. Environment & Remote Infrastructure Operations

- **[Multi-Node Orchestration & Remote Node Setup](docs/how-to/multi_node_orchestration.md)** — Registering compute nodes, remote stack execution, and live workload migration (`ldm target`).
- **[Portable Packages & Remote Repositories (.ldmp)](docs/how-to/workspace_import.md)** — Exporting, sharing, and importing compiled `.ldmp` packages and GitHub repositories.
- **[Data Management & Backup Snapshots](docs/how-to/data_management.md)** — Snapshots, pre-warmed seeds, and volume archives.
- **[Sharing & Tunnels](docs/how-to/sharing_tunnels.md)** — Securely sharing local stacks publicly using tunnels.
- **[Networking, DNS & Zero-Config SSL](docs/reference/networking.md)** — Traefik routing, hostname mappings, and trust certificates.

### 4. Developer & Integration Resources

- **[AI Command Center & LDM MCP Server](docs/how-to/ai_mcp_guide.md)** — Powering AI workflows with LDM FastMCP tools.
- **[Advanced CLI Overrides](docs/reference/advanced_cli.md)** — Colorless/ASCII outputs and global cli defaults.
- **[End-to-End Testing with LDM](docs/how-to/e2e_testing.md)** — Using LDM as an orchestration layer for automated CI/CD and local tests.
- **[Architecture Diagrams & Overview](docs/explanation/architecture.md)** — Visual environment diagrams, hybrid volumes, routing, and lifecycles.
- **[Release Playbook](docs/PLAYBOOK.md)** — Pipeline standards and release workflow triggers.

---

For a complete structured table of contents, visit the **[Documentation Index](docs/README.md)**.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-31* | *Last Reviewed: 2026-07-26*
