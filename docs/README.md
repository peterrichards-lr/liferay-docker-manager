# LDM Documentation Index

Welcome to the Liferay Docker Manager (LDM) documentation index. Use the categorized guides below to explore installation, configuration, features, and developer tools.

---

## 🎥 Seeing is Believing (Showcase)

Check out our **[Video Showcase](showcase/README.md)** to see short demonstrations of LDM in action, including Fast Provisioning, Cloud Hydration, and instant Snapshots & Restoration!

---

## 📚 Categorized Guides

### 1. Getting Started & Core Reference

- **[Installation Guide](INSTALLATION.md)** — Step-by-step setup for macOS (Colima/OrbStack), Linux, and Windows (WSL2/Native).
- **[Conventions & Key Features](guides/CONVENTIONS_AND_FEATURES.md)** — Out-of-the-box defaults, Postgres, global search settings, and system features.
- **[CLI Reference & Automation](guides/CLI_REFERENCE.md)** — Subcommands, global options, and scripting parameters.
- **[Compatibility Matrix](COMPATIBILITY_TABLE.md)** — Verified host operating systems, Docker engines, and providers.
- **[Troubleshooting & Diagnostics](TROUBLESHOOTING.md)** — Docker locks, port collisions, volume lag, and diagnostic commands.

### 2. Operational & Feature Guides

- **[PaaS "Golden Path" Local Dev](guides/PAAS_LOCAL_DEV.md)** — Hydrating local setups using remote Liferay Cloud backups.
- **[Workspace Import & Portable Packaging](guides/WORKSPACE_IMPORT_AND_PACKAGING.md)** — Working with workspaces and exporting `.ldmp` packages.
- **[Properties Hierarchy & Precedence](guides/PROPERTIES_HIERARCHY.md)** — Merging cascading properties and using `# !important` rules.
- **[Sharing & Tunnels](guides/SHARING_AND_TUNNELS.md)** — Exposing local projects securely to public subdomains (lfr-tunnel, Ngrok).
- **[Liferay Version Upgrades](guides/VERSION_UPGRADES.md)** — Conceptual workflow, database safety, auto-upgrades, and CLI flags.
- **[Data Management](guides/DATA_MANAGEMENT.md)** — Snapshots, pre-warmed database seeds, and backups.
- **[Networking, DNS & Zero-Config SSL](guides/NETWORKING_DNS.md)** — Traefik proxy configurations, virtual hostnames, and HTTPS cert trust.

### 3. Integration & Developer Resources

- **[AI Command Center & LDM MCP Server](guides/AI_MCP_GUIDE.md)** — Driving AI developer environments via FastMCP tools.
- **[Advanced CLI Tuning](guides/ADVANCED_CLI.md)** — Sudo policy, global defaults, colorless outputs, and custom environment variables.
- **[Development & Build Guide](guides/DEVELOPMENT.md)** — Setting up local dev, packaging egg binaries, and contributing.
- **[Operational Playbook & CI Release Specs](PLAYBOOK.md)** — Build pipelines, branch workflows, and release tags.
- **[Testing & Validation](TESTING.md)** — Unit tests, mock suites, and multi-OS E2E validation.
- **[Architecture Overview](LDM_ARCHITECTURE.md)** — LDM micro-architecture, abstraction layers, and directory layouts.
- **[Security Posture & Disclosures](SECURITY.md)** — Safe secrets handling and security policy.
- **[Third-Party Tools list](THIRD_PARTY_TOOLS.md)** — Internal and external dependencies (mkcert, Traefik, etc.).
- **[Future Roadmap](ROADMAP.md)** — Planned features and strategic milestones.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-06* | *Last Reviewed: 2026-07-02*
