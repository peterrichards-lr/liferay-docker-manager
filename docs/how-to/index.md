# How-To Guides

**You have a task and roughly know what you want.** Each guide here solves one
problem and assumes LDM is already installed. If you are new and want to be
walked through from the start, begin with
[The First 5 Minutes](../tutorials/first_5_minutes.md) instead.

## Install

- **[macOS](install_macos.md)** · **[Windows](install_windows.md)** ·
  **[Linux](install_linux.md)** — per-platform installation.

## Get a project running

- **[Starting a Fresh Vanilla Liferay](vanilla_start.md)** — a clean stack with
  no existing project or data.
- **[Workspace Import and Packaging](workspace_import.md)** — bring an existing
  Liferay Workspace or an `.ldmp` package under LDM.
- **[Nightly & Master Builds](nightly_master_builds.md)** — run unreleased DXP
  builds rather than a published tag.
- **[Version Upgrades](version_upgrades.md)** — move an existing project to a
  newer Liferay version.

## Develop

- **[Client Extensions](client_extensions.md)** — deploy and iterate on Client
  Extensions, including deployment ordering.
- **[Patching Core Portal JARs](portal_patches.md)** — apply patches to the
  portal itself.
- **[Runtime Overrides & Fragment Substitutions](runtime_overrides.md)** —
  substitute fragments and override runtime behaviour.
- **[Development & Building](development.md)** — working on LDM itself.

## Data and environments

- **[Data Management](data_management.md)** — snapshots, backups, and moving
  real datasets between environments.
- **[Custom Containers](custom_containers.md)** — run your own services
  alongside Liferay.
- **[Multi-Compose Architecture](multi_compose.md)** — decoupled compose stacks
  and shared networks.

## Share and connect

- **[Sharing & Tunnels](sharing_tunnels.md)** — expose a local Liferay to the
  internet for a demo or a webhook.
- **[AI & MCP](ai_mcp_guide.md)** — drive LDM from an AI agent over MCP.

## Operate at scale

- **[Multi-Node Orchestration](multi_node_orchestration.md)** — run projects on
  remote compute nodes.
- **[Cloud Deployment](cloud_deployment.md)** — deploy to Liferay Cloud PaaS.
- **[AWS IAM Least Privilege](target_node_iam_least_privilege.md)** — the
  minimum policy a target node's power control needs.
- **[End-to-End Testing](e2e_testing.md)** — run the E2E verification suite.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-09-19* | *Last Reviewed: 2026-09-19*
