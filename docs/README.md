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

### 2. Operational & Feature Guides

- **[PaaS "Golden Path" Local Dev](tutorials/paas_local_dev.md)** — Hydrating local setups using remote Liferay Cloud backups.
- **[Workspace Import & Portable Packaging](how-to/workspace_import.md)** — Working with workspaces and exporting `.ldmp` packages.
- **[Runtime Overrides & Fragments](how-to/runtime_overrides.md)** — Dynamic substitution and environment-aware client extension patching.
- **[Properties Hierarchy & Precedence](explanation/properties.md)** — Merging cascading properties and using `# !important` rules.
- **[Sharing & Tunnels](how-to/sharing_tunnels.md)** — Exposing local projects securely to public subdomains (lfr-tunnel, Ngrok).
- **[Liferay Version Upgrades](how-to/version_upgrades.md)** — Conceptual workflow, database safety, auto-upgrades, and CLI flags.
- **[Data Management](how-to/data_management.md)** — Snapshots, pre-warmed database seeds, and backups.
- **[Networking, DNS & Zero-Config SSL](reference/networking.md)** — Traefik proxy configurations, virtual hostnames, and HTTPS cert trust.

### 3. Integration & Developer Resources

- **[AI Command Center & LDM MCP Server](how-to/ai_mcp_guide.md)** — Driving AI developer environments via FastMCP tools.
- **[Advanced CLI Tuning](reference/advanced_cli.md)** — Sudo policy, global defaults, colorless outputs, and custom environment variables.
- **[Development & Build Guide](how-to/development.md)** — Setting up local dev, packaging egg binaries, and contributing.
- **[Operational Playbook & CI Release Specs](PLAYBOOK.md)** — Build pipelines, branch workflows, and release tags.
- **[Testing & Validation](TESTING.md)** — Unit tests, mock suites, and multi-OS E2E validation.
- **[Architecture Overview](explanation/architecture.md)** — LDM micro-architecture, abstraction layers, and directory layouts.
- **[Security Posture & Disclosures](reference/security.md)** — Safe secrets handling and security policy.
- **[Third-Party Tools list](reference/third_party_tools.md)** — Internal and external dependencies (mkcert, Traefik, etc.).
- **[Future Roadmap](ROADMAP.md)** — Planned features and strategic milestones.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-09* | *Last Reviewed: 2026-07-02*
