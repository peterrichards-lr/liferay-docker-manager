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

For detailed setup on Windows (including PowerShell instructions), see the **[Full Installation Guide](docs/tutorials/quick_start.md)**.

---

## 📋 Common Commands

Once installed, bootstrap or run your stacks instantly:

```bash
# 1. THE CONFIDENCE BOOSTER: Run Liferay with pre-configured samples
ldm run my-sample-project --samples

# 2. THE VANILLA FLOW: Run a fresh vanilla Liferay instance (LTS release)
ldm run my-vanilla-project --vanilla --tag 2026.q1.7-lts

# 3. THE DEVELOPER FLOW: Initialize from a workspace and start monitoring
ldm init-from /path/to/workspace my-project

# 4. THE PREDEFINED QUICKSTART: Bootstrap an accelerator demo stack
ldm quickstart aica

# 5. THE TIME MACHINE: Take a snapshot of your database and volumes, and restore them later
ldm snapshot my-project
ldm restore my-project

# 6. THE PORTABLE PACKAGE: Export project snapshot into a .ldmp package
ldm package my-project
```

> [!NOTE]
> **Headless & Seeding Prompt Behaviors**:
>
> - By default, LDM checks for a cached pre-warmed database seed. If not cached, it will prompt you interactively to download it.
> - **CI/CD / Headless Scripts**: To prevent interactive prompts from blocking headless environments, pass `-y` / `--yes` / `--non-interactive` to automatically confirm seed downloads, or pass `--vanilla` / `--no-seed` to skip seeding entirely and start a clean baseline database.

---

## 📚 Documentation Signposts

LDM is conventions-driven and highly customizable. Choose a topic below for detailed information:

### 1. Getting Started

- **[Installation Guide](docs/tutorials/quick_start.md)** — Setting up macOS (Colima/OrbStack), Linux, and Windows (WSL2).
- **[LDM Conventions & Features](docs/explanation/conventions.md)** — Default stacks, ports, database options, and key features.
- **[Compatibility Matrix](docs/reference/compatibility.md)** — Supported host OS, Docker providers, and engines.
- **[Troubleshooting & Diagnostics](docs/TROUBLESHOOTING.md)** — Logs, Docker deadlocks, port conflicts, and common fixes.

### 2. Core Operational Guides

- **[Fresh Vanilla Start](docs/how-to/vanilla_start.md)** — Launching empty Liferay instances for quick tests.
- **[PaaS "Golden Path" Local Dev](docs/tutorials/paas_local_dev.md)** — Fetching backups and replicating Liferay Cloud environments locally.
- **[Workspace Import & Packaging](docs/how-to/workspace_import.md)** — Importing workspaces and exporting/restoring portable `.ldmp` packages.
- **[Runtime Overrides & Fragments](docs/how-to/runtime_overrides.md)** — Dynamic substitution and environment-aware client extension patching.
- **[Properties Hierarchy & Precedence](docs/explanation/properties.md)** — The 5-layer cascading properties and `# !important` overrides.
- **[Sharing & Tunnels](docs/how-to/sharing_tunnels.md)** — Securely sharing local stacks publicly using tunnels.
- **[Liferay Version Upgrades](docs/how-to/version_upgrades.md)** — Safely upgrading Liferay Docker image tags, database backup snapshots, and schema auto-upgrades.
- **[Data Management](docs/how-to/data_management.md)** — Snapshots, pre-warmed seeds, and assets.
- **[Networking, DNS & Zero-Config SSL](docs/reference/networking.md)** — Traefik routing, hostname mappings, and trust certificates.

### 3. Developer & Integration Resources

- **[AI Command Center & LDM MCP Server](docs/how-to/ai_mcp_guide.md)** — Powering AI workflows with LDM FastMCP tools.
- **[Advanced CLI Overrides](docs/reference/advanced_cli.md)** — Colorless/ASCII outputs and global cli defaults.
- **[End-to-End Testing with LDM](docs/how-to/e2e_testing.md)** — Using LDM as an orchestration layer for automated CI/CD and local tests.
- **[Architecture Diagrams & Overview](docs/explanation/architecture.md)** — Visual environment diagrams, hybrid volumes, routing, and lifecycles.
- **[Testing & Validation](docs/TESTING.md)** — Running unit and E2E test suites.
- **[Release Playbook](docs/PLAYBOOK.md)** — Pipeline standards and release workflow triggers.

---

For a complete structured table of contents, visit the **[Documentation Index](docs/README.md)**.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-10* | *Last Reviewed: 2026-07-09*
