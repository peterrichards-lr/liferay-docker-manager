# Workspace Import and Packaging Guide

Liferay Docker Manager (LDM) offers a portable mechanism to export, share, and import full development stacks—including code, configurations, database snapshots, and volume assets. This guide explains how to package your workspace and import it on any machine.

---

## Workspace Source vs. Hydrated Packages (.ldmp)

To avoid confusion, it is important to distinguish between developing inside a **Liferay Workspace** and importing a pre-built **LDM Package (`.ldmp`)**:

### A. Raw Liferay Workspaces (Source Code)

* **What it is**: A standard directory structure containing Java source files, client extension source code (`client-extension.yaml`), and Gradle scripts.
* **How it works**: You link it with LDM via `ldm link /path/to/workspace`.
* **Deployment**: LDM runs `gradlew deploy` to compile Java bundles and package client extensions into zip archives, then hot-deploys them into Liferay's `/deploy` directory.
* **Best For**: Active developer code writing and file editing.

### B. Hydrated LDM Packages (`.ldmp`)

* **What it is**: A portable compressed archive containing pre-built assets, database snapshots, and volume directories.
* **How it works**: You import it with `ldm import my-project.ldmp`.
* **Deployment**: The database schema and custom assets are **already compiled and pre-hydrated** inside Liferay's internal directories. No Gradle compilation or local Java SDK is needed to start running.
* **Best For**: Mirrored backups, distributing quickstart demos, and deploying final pre-configured states.

---

## 1. Unified Import Workflow (`ldm import`)

The `ldm import` command is a polymorphic entry point. It automatically detects the type of source you provide and configures the environment accordingly.

```bash
ldm import <source-path-or-url>
```

### Supported Source Types

> [!IMPORTANT]
> As of v2.15.16, `ldm import` is restricted to **data packages only** (`.ldmp` archives and remote URLs). To clone a Git repository or link a local workspace, use `ldm clone` or `ldm link` respectively.

| Source Type | Command | Behavior & Details |
| :--- | :--- | :--- |
| **Local LDM Package (`.ldmp`)** | `ldm import ~/Downloads/my-project.ldmp` | Extracts the archive, restores the database snapshot and named volumes, writes metadata, and boots the Docker stack. |
| **Remote `.ldmp` URL** | `ldm import https://example.com/assets/my-project.ldmp` | Downloads the remote archive, verifies the `.sha256` signature if available, and performs a local package import. |
| **GitHub Release Package** | `ldm import https://github.com/my-org/my-repo` | Automatically queries the GitHub Release API. If a `.ldmp` asset exists, it downloads and restores it directly. |
| **Git Repository URL** | `ldm clone https://github.com/my-org/my-repo.git` | Clones the repository to a local folder and sets up a standard workspace with hot-reload mounts. |
| **Local Liferay Workspace** | `ldm link /path/to/local/liferay-workspace` | Maps folders, executes `gradlew` build (if `--build` is specified), and configures the container paths. |
| **Local Cloud Workspace** | `ldm link /path/to/local/lcp-workspace` | Detects `LCP.json`, resolves the nested `liferay/` folder structure, and configures environment variables and properties. |

---

## 2. Packaging Workspaces (`ldm package`)

![Added in v2.11.31](https://img.shields.io/badge/Added%20in-v2.11.31-blue)

The `ldm package` command bundles a project's files, configurations, database, and volume assets into a single portable `.ldmp` tarball, complete with a `.sha256` signature.

### Step-by-Step: Creating an `.ldmp` Package

You can create `.ldmp` packages locally using three main packaging strategies:

#### Method A: Fresh Snapshot & Package (Standard)

If your Liferay Docker stack is currently running and contains the data state you want to distribute, you can build a new snapshot and package it in a single command:

1. Ensure your container stack is active: `ldm status`
2. Run the package command:

   ```bash
   ldm package --output ./dist
   ```

   *This command automatically triggers a fresh snapshot creation (`ldm snapshot`), generates the package tarball, calculates its SHA-256 hash, and saves both files into the `./dist` folder.*

#### Method B: Package the Latest Existing Snapshot

If you already took a snapshot earlier (e.g. using `ldm snapshot`) and want to package it without waiting to take a new one:

1. Run the package command with the `--use-latest` flag:

   ```bash
   ldm package --use-latest --output ./dist
   ```

   *LDM will query your project's `snapshots/` directory, locate the most recently created snapshot directory, and package it directly.*

#### Method C: Package a Specific Target Snapshot

If you want to package a specific snapshot directory from your historical backups:

1. List all available snapshots to find the target name:

   ```bash
   ldm snapshots
   ```

   *(e.g., output shows `20260626_114522`)*

2. Target the specific snapshot name using the `--snapshot` parameter:

   ```bash
   ldm package --snapshot 20260626_114522 --output ./dist
   ```

   *LDM will bundle only that specific snapshot, ignoring all other snapshot directories.*

---

### Integrity Verification (Checksum Files)

Every time you package a workspace, LDM automatically writes a companion `.sha256` checksum file (e.g. `my-project.ldmp.sha256`).

To verify the integrity of your `.ldmp` package manually on the target machine:

```bash
# Verify using system checksum utilities
shasum -a 256 -c ./dist/my-project.ldmp.sha256
```

When importing, LDM automatically looks for this checksum file to perform the check for you.

---

## 3. Scaffolding CI/CD Pipelines (`ldm system init-ci`)

To automate the generation of `.ldmp` packages on code changes or releases, you can scaffold a GitHub Actions workflow inside your Git repository.

```bash
ldm system init-ci [project] [--trigger release|tag|push|manual] [--repo owner/repo]
```

### Trigger Presets

* **`release` (Default)**: Triggers only when a GitHub Release is published. Useful for production-grade builds.
* **`tag`**: Triggers whenever a tag starting with `v*` is pushed.
* **`push`**: Triggers on every commit pushed to the `master` branch.
* **`manual`**: Only runs when triggered manually via the GitHub Actions tab (`workflow_dispatch`).

### Workflow Output

This scaffolds a `.github/workflows/ldm-package-release.yml` file which:

1. Provisions a clean Ubuntu environment.
2. Installs LDM from PyPI.
3. Boots up your local Liferay environment.
4. Waits for all OSGi modules and Client Extensions to deploy cleanly.
5. Bundles the database and volume into a `.ldmp` package.
6. Automatically attaches the package and its checksum as GitHub Release assets.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-16* | *Last Reviewed: 2026-07-10*
