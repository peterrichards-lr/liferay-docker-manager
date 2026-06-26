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

For detailed setup on Windows (including PowerShell instructions), see the **[Full Installation Guide](docs/INSTALLATION.md)**.

---

## 📋 Common Commands

Once installed, bootstrap or run your stacks instantly:

```bash
# 1. THE CONFIDENCE BOOSTER: Run Liferay with pre-configured samples
ldm run my-sample-project --samples

# 2. THE DEVELOPER FLOW: Initialize from a workspace and start monitoring
ldm init-from /path/to/workspace my-project

# 3. THE PREDEFINED QUICKSTART: Bootstrap an accelerator demo stack
ldm quickstart aica

# 4. THE PORTABLE PACKAGE: Export project snapshot into a .ldmp package
ldm package my-project
```

---

## 📚 Documentation Signposts

LDM is conventions-driven and highly customizable. Choose a topic below for detailed information:

### 1. Getting Started

- **[Installation Guide](docs/INSTALLATION.md)** — Setting up macOS (Colima/OrbStack), Linux, and Windows (WSL2).
- **[LDM Conventions & Features](docs/guides/CONVENTIONS_AND_FEATURES.md)** — Default stacks, ports, database options, and key features.
- **[Compatibility Matrix](docs/COMPATIBILITY_TABLE.md)** — Supported host OS, Docker providers, and engines.
- **[Troubleshooting & Diagnostics](docs/TROUBLESHOOTING.md)** — Logs, Docker deadlocks, port conflicts, and common fixes.

### 2. Core Operational Guides

- **[PaaS "Golden Path" Local Dev](docs/guides/PAAS_LOCAL_DEV.md)** — Fetching backups and replicating Liferay Cloud environments locally.
- **[Workspace Import & Packaging](docs/guides/WORKSPACE_IMPORT_AND_PACKAGING.md)** — Importing workspaces and exporting/restoring portable `.ldmp` packages.
- **[Properties Hierarchy & Precedence](docs/guides/PROPERTIES_HIERARCHY.md)** — The 5-layer cascading properties and `# !important` overrides.
- **[Sharing & Tunnels](docs/guides/SHARING_AND_TUNNELS.md)** — Securely sharing local stacks publicly using tunnels.
- **[Data Management](docs/guides/DATA_MANAGEMENT.md)** — Snapshots, pre-warmed seeds, and assets.
- **[Networking, DNS & Zero-Config SSL](docs/guides/NETWORKING_DNS.md)** — Traefik routing, hostname mappings, and trust certificates.

### 3. Developer & Integration Resources

- **[AI Command Center & LDM MCP Server](docs/guides/AI_MCP_GUIDE.md)** — Powering AI workflows with LDM FastMCP tools.
- **[Advanced CLI Overrides](docs/guides/ADVANCED_CLI.md)** — Colorless/ASCII outputs and global cli defaults.
- **[Micro-Architecture](docs/LDM_ARCHITECTURE.md)** — Core design principles, layers, and boundaries.
- **[Testing & Validation](docs/TESTING.md)** — Running unit and E2E test suites.
- **[Release Playbook](docs/PLAYBOOK.md)** — Pipeline standards and release workflow triggers.

---

For a complete structured table of contents, visit the **[Documentation Index](docs/README.md)**.
