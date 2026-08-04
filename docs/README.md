# LDM Documentation Index

Welcome to the Liferay Docker Manager (LDM) documentation index. Use the categorized guides below to explore installation, configuration, features, and developer tools.

---

## 🎥 Seeing is Believing (Showcase)

Check out our **[Video Showcase](showcase/README.md)** to see short demonstrations of LDM in action, including Fast Provisioning, Cloud Hydration, and instant Snapshots & Restoration!

---

## 📚 Categorized Guides

### 1. Getting Started & Core Reference

- **[Installation Guide](tutorials/quick_start.md)** — Step-by-step setup for macOS (Colima/OrbStack), Linux, and Windows (WSL2/Native).
- **[Conventions & Key Features](explanation/conventions.md)** — Out-of-the-box defaults, Postgres, global search settings, and system features.
- **[CLI Reference & Automation](reference/cli/core.md)** — Subcommands, global options, and scripting parameters.
- **[Compatibility Matrix](reference/compatibility.md)** — Verified host operating systems, Docker engines, and providers.
- **[Troubleshooting & Diagnostics](TROUBLESHOOTING.md)** — Docker locks, port collisions, volume lag, and diagnostic commands.

### 2. Local Development & Customization

- **[Fresh Vanilla Start](how-to/vanilla_start.md)** — Launching empty Liferay instances for quick tests.
- **[Liferay DXP Nightly & Master Builds Guide](how-to/nightly_master_builds.md)** — Orchestrating Liferay DXP nightly builds without compiling source code locally.
- **[PaaS "Golden Path" Local Dev](tutorials/paas_local_dev.md)** — Hydrating local setups using remote Liferay Cloud backups.
- **[Liferay Workspace Local Dev](tutorials/workspace_development.md)** — Linking workspaces with LDM for active source code development.
- **[Liferay Version Upgrades](how-to/version_upgrades.md)** — Safely upgrading Liferay Docker image tags, database backup snapshots, and schema auto-upgrades.
- **[Runtime Overrides & Fragments](how-to/runtime_overrides.md)** — Dynamic substitution and environment-aware client extension patching.
- **[Properties Hierarchy & Precedence](explanation/properties.md)** — Merging cascading properties and using `# !important` rules.

### 3. Environment & Remote Infrastructure Operations

- **[Multi-Node Orchestration & Remote Node Setup](how-to/multi_node_orchestration.md)** — Registering compute nodes, remote stack execution, and live workload migration (`ldm target`).
- **[Portable Packages & Remote Repositories (.ldmp)](how-to/workspace_import.md)** — Exporting, sharing, and importing compiled `.ldmp` packages and GitHub repositories.
- **[Data Management & Backup Snapshots](how-to/data_management.md)** — Snapshots, pre-warmed database seeds, and volume archives.
- **[Sharing & Tunnels](how-to/sharing_tunnels.md)** — Exposing local projects securely to public subdomains (lfr-tunnel, Ngrok).
- **[Networking, DNS & Zero-Config SSL](reference/networking.md)** — Traefik proxy configurations, virtual hostnames, and HTTPS cert trust.

### 4. Integration & Developer Resources

- **[AI Command Center & LDM MCP Server](how-to/ai_mcp_guide.md)** — Driving AI developer environments via FastMCP tools.
- **[Advanced CLI Tuning](reference/advanced_cli.md)** — Sudo policy, global defaults, colorless outputs, and custom environment variables.
- **[Development & Build Guide](how-to/development.md)** — Setting up local dev, packaging egg binaries, and contributing.
- **[Operational Playbook & CI Release Specs](PLAYBOOK.md)** — Build pipelines, branch workflows, and release tags.
- **[Testing & Validation](TESTING.md)** — Unit tests, mock suites, and multi-OS E2E validation.
- **[Architecture Overview](explanation/architecture.md)** — LDM micro-architecture, abstraction layers, and directory layouts.
- **[Security Posture & Disclosures](reference/security.md)** — Safe secrets handling and security policy.
- **[Third-Party Tools List](reference/third_party_tools.md)** — Internal and external dependencies (mkcert, Traefik, etc.).
- **[Future Roadmap](ROADMAP.md)** — Planned features and strategic milestones.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-04* | *Last Reviewed: 2026-07-31*
