# Liferay Docker Manager documentation

LDM runs, manages and develops Liferay DXP and Portal environments in Docker from a single CLI: declarative stacks, instant snapshots, and local workspaces linked to running containers.

> 📰 **As featured on the Liferay Blog:** [*Effortless Liferay Orchestration: Advanced Docker Scripts for Local Development*](https://liferay.dev/b/liferay-docker-automation-python-scripts) — Peter Richards, March 2026.

<!-- MD028: the blog callout above and the notice below are SEPARATE
     blockquotes. Without this comment markdownlint reads the blank line
     between them as a blank line inside one quote, and some renderers
     merge the two into a single block. -->
> [!NOTE]
> ℹ️ **Community project** — not an official Liferay product and not covered by Liferay Support. [About this project](about.md)

---

## I want to…

| I want to… | Go to |
| :--- | :--- |
| Get Liferay running for the first time | [The First 5 Minutes](tutorials/first_5_minutes.md) |
| Install LDM, or fix a broken setup | [Installation & Setup](tutorials/quick_start.md) |
| Do a specific task — import a workspace, move data, expose a demo | [How-to guides](how-to/index.md) |
| Look something up — a flag, a setting, a port | [Reference](reference/index.md) |
| Understand why LDM behaves as it does | [Explanation](explanation/index.md) |
| Fix something that is broken | [Troubleshooting](TROUBLESHOOTING.md) |
| Contribute to LDM itself | [Playbook](PLAYBOOK.md) · [Testing](TESTING.md) |

Each section above opens on a page that groups its own contents and says when to
pick each one. The categorised index further down lists every page in one place,
organised by subject rather than by what you are trying to do — use whichever
suits how you are looking.

---

## By subject

### Getting started and core reference

- **[The First 5 Minutes](tutorials/first_5_minutes.md)** — **Start here.** From nothing to a running Liferay, with Client Extensions attached and logs streaming.
- **[Installation & Setup](tutorials/quick_start.md)** — Step-by-step setup for macOS (Colima/OrbStack), Linux, and Windows (WSL2/Native). Reach for this if LDM is not installed yet, or something is broken.
- **[Key Features](explanation/conventions.md)** — What LDM gives you out of the box: session isolation, zero-config SSL, snapshots, fail-fast checks.
- **[Conventions & Configuration](explanation/conventions_and_config.md)** — The default stack, and how an LDM setting is resolved.
- **[CLI Reference & Automation](reference/cli/core.md)** — Subcommands, global options, and scripting parameters.
- **[Compatibility Matrix](reference/compatibility.md)** — Verified host operating systems, Docker engines, and providers.
- **[Troubleshooting & Diagnostics](TROUBLESHOOTING.md)** — Docker locks, port collisions, volume lag, and diagnostic commands.

### Local development and customisation

- **[Fresh Vanilla Start](how-to/vanilla_start.md)** — Launching empty Liferay instances for quick tests.
- **[Liferay DXP Nightly & Master Builds Guide](how-to/nightly_master_builds.md)** — Orchestrating Liferay DXP nightly builds without compiling source code locally.
- **[PaaS "Golden Path" Local Dev](tutorials/paas_local_dev.md)** — Hydrating local setups using remote Liferay Cloud backups.
- **[Liferay Workspace Local Dev](tutorials/workspace_development.md)** — Linking workspaces with LDM for active source code development.
- **[Liferay Version Upgrades](how-to/version_upgrades.md)** — Safely upgrading Liferay Docker image tags, database backup snapshots, and schema auto-upgrades.
- **[Runtime Overrides & Fragments](how-to/runtime_overrides.md)** — Dynamic substitution and environment-aware client extension patching.
- **[Patching Core Portal JARs](how-to/portal_patches.md)** — Applying patched core JARs from `portal-patches/`, with version-drift protection.
- **[Properties Hierarchy & Precedence](explanation/properties.md)** — Merging cascading properties and using `# !important` rules.

### Environment and remote infrastructure

- **[Multi-Node Orchestration & Remote Node Setup](how-to/multi_node_orchestration.md)** — Registering compute nodes, remote stack execution, and live workload migration (`ldm target`).
- **[Portable Packages & Remote Repositories (.ldmp)](how-to/workspace_import.md)** — Exporting, sharing, and importing compiled `.ldmp` packages and GitHub repositories.
- **[Data Management & Backup Snapshots](how-to/data_management.md)** — Snapshots, pre-warmed database seeds, and volume archives.
- **[Sharing & Tunnels](how-to/sharing_tunnels.md)** — Exposing local projects securely to public subdomains (lfr-tunnel, Ngrok).
- **[Networking, DNS & Zero-Config SSL](reference/networking.md)** — Traefik proxy configurations, virtual hostnames, and HTTPS cert trust.

### Integration and developer resources

- **[AI Command Center & LDM MCP Server](how-to/ai_mcp_guide.md)** — Driving AI developer environments via FastMCP tools.
- **[Advanced CLI Tuning](reference/advanced_cli.md)** — Sudo policy, global defaults, colorless outputs, and custom environment variables.
- **[Development & Build Guide](how-to/development.md)** — Setting up local dev, packaging egg binaries, and contributing.
- **[Operational Playbook & CI Release Specs](PLAYBOOK.md)** — Build pipelines, branch workflows, and release tags.
- **[Testing & Validation](TESTING.md)** — Unit tests, mock suites, and multi-OS E2E validation.
- **[Architecture Overview](explanation/architecture.md)** — LDM micro-architecture, abstraction layers, and directory layouts.
- **[Remote Node Architecture: Target Resolution](explanation/remote-node-architecture.md)** — How `--node`/`--target` precedence, pinning, and conflict handling work under the hood.
- **[Security Posture & Disclosures](reference/security.md)** — Safe secrets handling and security policy.
- **[Third-Party Tools List](reference/third_party_tools.md)** — Internal and external dependencies (mkcert, Traefik, etc.).
- **[Future Roadmap](ROADMAP.md)** — Planned features and strategic milestones.

---

## Showcase

Short demonstrations of LDM in action -- fast provisioning, cloud hydration, and snapshots and restoration: **[Video Showcase](showcase/README.md)**.

---

---

## Related Liferay tools

Three community tools from the same maintainer. None is an official Liferay product or covered by Liferay Support.

| Tool | What it does |
| :--- | :--- |
| Liferay Docker Manager | You are here. Run, manage and develop Liferay DXP and Portal environments in Docker. |
| [Liferay AI Commerce Accelerator](https://peterrichards-lr.github.io/liferay-ai-commerce-accelerator/) | Generate and manage AI-driven commerce data in Liferay DXP. |
| [Liferay Tunnel](https://peterrichards-lr.github.io/lfr-tunnel/) | Securely expose a local Liferay environment on a public HTTPS subdomain. |
<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-10-16* | *Last Reviewed: 2026-10-16*
