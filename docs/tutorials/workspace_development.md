# Local Development with Liferay Workspaces & LDM

This tutorial guides you through setting up a brand new Liferay Workspace and linking it with Liferay Docker Manager (LDM) to build, deploy, hot-reload, and test custom assets (Client Extensions, OSGi modules, themes) locally.

---

## 1. Developer Workflow Overview

When developing customizations for Liferay, you typically work in a **Liferay Workspace**. To run and test these customizations, LDM provides a seamless integration:

- **Liferay Workspace**: Houses your Gradle build script, configuration files, custom modules, and Client Extensions.
- **LDM Stack**: Spawns and manages the underlying Docker containers (Liferay, Postgres, Elasticsearch).
- **Workspace Link & Monitor (`ldm link`)**: Bridges the two by watching your workspace for changes, compiling files, and auto-deploying build outputs to the Docker container.

---

## 2. Step-by-Step Setup

Follow one of the two routes below to bootstrap your local developer environment:

### Route A: Link an Existing Local Workspace

Use this route if you already have a Liferay Workspace directory on your machine (either freshly initialized using Blade CLI, or previously cloned manually).

1. If you need to bootstrap a brand new workspace first, you can use Liferay's **Blade CLI**:

   ```bash
   # Initialize a new workspace targeting a specific version
   blade init my-workspace -v dxp-2025.q1.0
   ```

2. Link your workspace to LDM and start the stack:

   ```bash
   # Link the workspace and boot the local runtime stack
   ldm link ./my-workspace
   ```

### Route B: Clone a Remote Workspace (Git)

Use this route if the workspace is hosted in a remote Git repository (e.g. GitHub). LDM's `clone` command combines cloning and linking in a single step.

```bash
# Clone the repository and initialize the LDM project stack
ldm clone https://github.com/my-org/my-workspace.git my-project
```

---

### What LDM Does During Link/Clone

When you run `ldm link` or `ldm clone`, LDM automatically:

1. Resolves the workspace path and registers the project.
2. Sets up folders mapping links from your workspace `deploy/` and build directories directly into LDM's hot-reload mounts.
3. Boots up the Docker stack and launches the background file monitoring daemon.

### Step 3: Run the Monitor Daemon (Hot-Reloads)

The `link` and `clone` commands immediately launch the file monitor. If you restart your system or shell, you can resume monitoring at any time by running:

```bash
ldm monitor ./my-workspace
```

As long as the monitor daemon is running:

- Any changes made inside `./my-workspace/client-extensions/` or `./my-workspace/modules/` are detected.
- LDM automatically triggers the Gradle build (e.g. `./gradlew deploy`) locally on your host machine.
- The compiled zip files or jars are copied directly to LDM's internal deploy folder, triggering a hot-reload inside Liferay.

---

## 3. Creating and Testing Customizations

Now that your workspace is linked, you can develop assets using standard Liferay patterns.

### Client Extensions

1. Generate or drop a new Client Extension project into `my-workspace/client-extensions/`:

   ```text
   my-workspace/
   ├── client-extensions/
   │   └── my-custom-element/
   │       ├── client-extension.yaml
   │       └── ...
   ```

2. Save your changes. LDM will detect the change, build the extension, and deploy it to the running stack.
3. Access your Liferay instance at `http://localhost:8080` (or the configured Traefik host) to test it.

### OSGi Modules

1. Create a custom Java module under `my-workspace/modules/` (e.g. a portlet or service override).
2. Saving changes will compile the jar and deploy it to the OSGi container, reloading the bundle on the fly.

---

## 4. Useful Commands Reference

| Action | Command | Description |
| :--- | :--- | :--- |
| **Link Workspace** | `ldm link ./my-workspace` | Integrates workspace mounts and boots containers. |
| **Start Stack** | `ldm run` | Starts the Docker containers (without running monitor). |
| **Start Monitor** | `ldm monitor ./my-workspace` | Watches workspace and compiles/deploys on save. |
| **Stop Stack** | `ldm down` | Stops the running Docker containers. |

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-20* | *Last Reviewed: 2026-07-13*
