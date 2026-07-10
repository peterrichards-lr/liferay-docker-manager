# CLI Core Commands

Display a tabulated overview of all initialized LDM sandbox environments.

```bash
ldm list
ldm ls
```

## `run` (alias: `up`)

Initialize and start a project stack.

```bash
# Run with a specific tag and virtual hostname
ldm run --tag 2024.q4.0 --host-name demo.local

# Automatically grab the latest Quarterly Release
ldm run demo --tag-latest --release-type qr

# Run with a resource-optimized JVM profile (great for laptops with less RAM)
ldm run my-project --lean

# Inject an environment variable directly
ldm run my-project --env LIFERAY_COMPANY_DEFAULT_WEB_ID=my-domain.com

# Enable specific Liferay feature flags
ldm run demo --feature LPS-122920 dev beta

# Run on a custom port
ldm run my-project --port 8081

# Using the alias
ldm up demo

# Initialize with "Confidence Booster" samples
ldm run demo --samples

# Auto-open the browser after startup
ldm run demo --open

# Start an ngrok tunnel to expose Liferay to the public internet
ldm run demo --expose

# Boot a 2-node Liferay cluster in one command
ldm run demo --scale liferay=2

# Boot a scaled stack and open the browser
ldm run demo --scale liferay=2 --open

# Interactive run (will prompt for version and project name)
# Prompts are automatically pre-filled using the Cascading Defaults system
ldm run
```

### `--open` Switch

Use `--open` to automatically launch the Liferay URL in your system browser once the instance is ready. This is equivalent to running `ldm browser` immediately after startup, but in a single command.

### `--scale` Switch

Use `--scale SERVICE=N` to boot a scaled stack without having to run `ldm scale` as a separate step. Multiple services can be scaled at once:

```bash
ldm run demo --scale liferay=2 --scale my-ext=3
```

### `--vanilla` Switch ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue)

![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue)

Bypasses downloading the pre-warmed database seed from GitHub releases. Spawns the Liferay project stack with a pristine, empty database.

## `init`

Initialize project scaffolding (creating `.liferay-docker.meta`, `portal-ext.properties`, etc.) without actually starting the Docker containers. Accepts many of the same configuration flags as `run`.

```bash
ldm init my-project --tag 2024.q4.0 --db mysql
```

### External Database Integration

LDM supports connecting your local Liferay instance to an external database (e.g., a shared development database or a standalone local database server) instead of running a database container within the project's Docker Compose stack.

To initialize a project with an external database:

```bash
ldm init my-project --db external
```

When you use `--db external`, LDM will launch an interactive wizard to gather your JDBC connection details (Host, Port, Database Name, Username, Password) and automatically generate the necessary properties in your `portal-ext.properties`. The database service container will be entirely omitted from the generated stack.

### SSL Defaults (New Projects)

LDM uses smarter defaults for SSL based on your hostname. When a custom `--host-name` is used, SSL is enabled by default to support modern Liferay features like Client Extensions.

> [!TIP]
> **SSL Hostname Prompt**: If you explicitly pass the `--ssl` flag without providing a `--host-name`, LDM will interactively prompt you for a custom virtual hostname. This allows LDM to attempt to inject the custom domain into your `/etc/hosts` file automatically. (In non-interactive mode `-y`, it safely defaults to `localhost`).

| Command | Host Name | SSL Default | Access URL |
| :--- | :--- | :--- | :--- |
| `ldm run` | `localhost` | `False` | `http://localhost:8080` |
| `ldm run --ssl` | *Prompts User* | `True` | `https://<prompted-host>` |
| `ldm run --host-name my.local` | `my.local` | `True` | `https://my.local` |
| `ldm run --no-ssl` | `localhost` | `False` | `http://localhost:8080` |
| `ldm run --host-name my.local --no-ssl` | `my.local` | `False` | `http://my.local:8080` |

### 🛡️ Modern Liferay & JDK 17+ Standards

LDM automatically hardens modern environments (DXP 2024+ and modern Quarterly Releases) to ensure stable startup:

- **JVM Module Exports**: Automatically injects mandatory `--add-opens` flags for JDK 17+ (covering `java.net`, `java.lang.reflect`, `security`, and more).
- **Hardened MySQL 8.4 (LTS)**:
  - Standardized on the **MariaDB JDBC Driver** and `MariaDB103Dialect` to mirror **Liferay Cloud (LXC)** environments.
  - Forces `mysql_native_password` authentication for CI compatibility.
  - Includes performance-optimized connection parameters (e.g., `rewriteBatchedStatements`, `prepStmtCacheSize`).
  - **Redline Configuration**: Explicitly sets `hibernate.dialect` and `jdbc.default.*` properties in `portal-ext.properties` to ensure reliable interpretation of mixed-case keys (like `driverClassName`).
  - Prioritizes `LIFERAY_JDBC_DEFAULT_*` environment variables ONLY for runtime user overrides; LDM baseline always uses `portal-ext.properties`.
- **Proactive Boot Sequencing**: Configures `depends_on` with healthchecks to ensure Liferay only starts once the database is fully ready to accept connections.

## `init-from` (Live Link)

Initialize a project from a source workspace and establish a **persistent link**. This command records the workspace path in the project metadata and automatically starts the `monitor` process to sync your code changes in real-time. If a Liferay Cloud Workspace is detected, it will also launch an interactive wizard to hydrate the data from the remote environment.

```bash
# ldm init-from <source_path> [project_name] [--host-name custom.local]
ldm init-from ~/repos/my-workspace my-project --host-name forge.demo

# Initialize with the latest tag and disable CAPTCHAs for CI testing
ldm init-from ~/repos/my-workspace my-ci-project -y --tag-latest --no-captcha

# Manually bind a Liferay Cloud project ID to the local workspace
ldm init-from ~/repos/my-workspace my-project --cloud-project lctintranet
```

## `import` (Static Snapshot)

Scaffold a new project by taking a **one-time static import** of an existing workspace. This project is detached from the source; changes to the source workspace will not be synced. Follows the same internal deployment sequence as `init-from`. If a Liferay Cloud Workspace is detected, it will also launch an interactive wizard to hydrate the data from the remote environment.

```bash
# ldm import <source_path> [project_name] [--host-name custom.local]
ldm import ~/repos/my-workspace my-static-project

# Import using a specific release type filter
ldm import ~/repos/my-workspace my-static-project --tag-latest --release-type qr

# Manually bind a Liferay Cloud project ID to the local workspace
ldm import ~/repos/my-workspace my-project --cloud-project lctintranet
```

## `quickstart`

Bootstrap and start a predefined accelerator demo stack in one command. Downloads target repositories, configures metadata, and automatically starts the environment.

```bash
# Bootstrap the AICA (AI Commerce Accelerator) template
ldm quickstart aica

# Bootstrap and expose the stack dynamically using lfr-tunnel
ldm quickstart aica --share --share-subdomain my-custom-demo
```

Custom templates and repository mappings can be configured by defining overrides in `~/.ldm_templates.json`.

## `package`

Package a project snapshot (code elements, database, and volumes) into a portable LDM package (`.ldmp` archive) alongside a SHA-256 checksum signature (`.ldmp.sha256`).

```bash
# Package the current project
ldm package

# Package a specific project, outputting to a custom directory
ldm package my-project -o /tmp/packages

# Package using a specific repository manifest identifier and the latest snapshot
ldm package my-project --repo my-owner/my-repo --use-latest
```

## Data Management Commands

LDM includes powerful commands for managing your project's database, OSGi state, and Elasticsearch indices. For full details on the following commands, please see the [Data Management Guide](../../how-to/data_management.md).

- **`snapshot` / `restore`**: Backup and recover exact project states.
- **`package`**: Export a project snapshot into a portable `.ldmp` package.
- **`hydrate`**: Create or restore a project from a local Liferay Cloud backup.
- **`cloud-fetch`**: Sync an existing local project directly with a live Liferay Cloud (LCP) environment.
- **`reset` / `re-seed`**: Surgically clear data folders or completely wipe a project back to its original vanilla state.

## `monitor`

Restarts the background watch process for a project linked to a Liferay workspace. This command can **only be used for projects created with `init-from`**. It automatically syncs built artifacts (`.jar`, `.war`, `.zip`) whenever they are updated in the workspace.

```bash
ldm monitor [project_name] --delay 2.0
```

## `logs`

View real-time logs. Supports filtering by project, specific services, global infrastructure, or individual scaled replicas.

```bash
ldm logs [project] [service1] [service2] ...

# Examples:
ldm logs                  # All logs for current project
ldm logs demo             # All logs for 'demo' project
ldm logs -f               # Follow logs continuously
ldm logs -n 250           # Show last 250 lines (default: 100)
ldm logs -t               # Show timestamps
ldm logs --since 1h       # Show logs from the last hour
ldm logs --until 10m      # Show logs until 10 minutes ago
ldm logs --no-wait        # Tailing usually waits for containers to be ready; use this to tail immediately
ldm logs --export         # Export logs to a local file
ldm logs --include-infra  # Include global infrastructure logs when viewing/exporting project logs
ldm logs --infra          # Show logs for all global infrastructure (ES, Proxy, etc.)
ldm logs --infra es       # Show logs only for Global Elasticsearch
ldm logs --infra proxy    # Show logs only for Global SSL Proxy
ldm logs demo liferay     # Only Liferay logs for 'demo'
ldm logs demo liferay my-ext # Multi-service tailing (all replicas)
```

### Targeting a Specific Scaled Replica (`--instance N` / `-i N`)

When a service is scaled to multiple replicas (e.g. `ldm scale demo liferay=3`), `ldm logs` streams from **all instances simultaneously** by default. Use `--instance N` to isolate a single replica:

```bash
# Stream logs from replica 2 of the liferay service only
ldm logs demo liferay --instance 2

# Short form, following in real time
ldm logs demo liferay -i 2 -f

# Tail the last 50 lines of replica 3
ldm logs demo liferay -i 3 -n 50
```

> [!NOTE]
> `--instance` routes to `docker logs` directly (bypassing Compose) so it targets the exact container. The container name is resolved from project metadata using the standard naming convention `{project}-{service}-{index}` (e.g. `demo-liferay-2`). This pattern is stored automatically when you run `ldm scale`, making subsequent lookups instant.
>
> [!TIP]
> If you request an out-of-range instance (e.g. `--instance 5` when only 3 replicas are running), LDM will report the valid range and exit cleanly.

## `stop`, `restart`, `down` (alias: `rm`)

Manage the lifecycle of a project or a specific service.

```bash
ldm stop [project] [service]      # Stop containers gracefully
ldm restart [project] [service]   # Stop and then start
ldm down [project] [service]      # Remove containers (and optionally -v volumes)
ldm rm [project]                  # Alias for 'down'

# Examples:
ldm stop --all            # Stop all running projects in the workspace
ldm restart --all         # Restart all running projects
ldm restart               # Full stack restart (graceful stop + run)
ldm down --volumes        # Tear down stack and clear all database/data state
ldm down --infra          # Also tear down the global infrastructure (Proxy, Search)
ldm down --clean-hosts    # Remove project entries from your /etc/hosts file upon deletion
```

## `status`

View the status of all projects in the current workspace.

```bash
ldm status
```

> [!TIP]
> Projects marked with a 🌱 (seedling) emoji were initialized from a **Seeded State**, meaning they started with a pre-calculated database and OSGi cache for near-instant boot times.

---

## `deploy`

Hot-deploy built artifacts or rebuild extension images.

```bash
ldm deploy [project] [service] --rebuild

# Examples:
ldm deploy                # Sync all artifacts and refresh stack
ldm deploy demo my-ext --rebuild  # Rebuild and restart one extension
```

## `scale`

Scale services within a project for multi-node simulation and clustering tests.

```bash
ldm scale [project] service=count

# Examples:
ldm scale demo liferay=2  # Scale Liferay to 2 nodes (enables clustering)
ldm scale demo my-ext=3   # Scale a client extension to 3 nodes
```

## `shell` & `gogo`

Jump into a container shell for deep inspection or connect to the OSGi Gogo console for runtime management.

**Interactive Shell Examples:**

```bash
# Enter bash in the Liferay container
ldm shell demo

# Common Shell Tasks (inside container):
# 1. View live Tomcat logs
cd tomcat/logs && tail -f catalina.out

# 2. Check injected environment variables
env | grep LIFERAY

# 3. Verify mounted OSGi configurations
ls osgi/configs
```

**Gogo Shell Examples:**

```bash
# Connect to the Gogo shell (requires --gogo-port during run)
ldm gogo demo

# Common Gogo Commands:
# 1. List all active bundles
lb

# 2. Check for unresolved dependencies
diag

# 3. List declarative services (SCR)
scr:list
```

## `config env` (legacy: `env`)

Manage persistent environment variables in project metadata.

```bash
ldm config env [project] KEY=VALUE
ldm config env [project] --remove KEY
ldm config env [project] -s liferay KEY=VALUE  # Target a specific service instead of global
ldm config env [project] --import              # Import variables from a local .env file
ldm config env                                 # Interactive manager (view and edit all)

# Legacy flat form (still works):
ldm env [project] KEY=VALUE
```

## `config feature` (legacy: `feature`)

Quickly toggle Liferay feature flags without manually editing `portal-ext.properties`. Requires a project restart to take effect.

```bash
ldm config feature [project] --enable LPS-122920
ldm config feature [project] --disable LPS-111111 LPS-222222

# Legacy flat form (still works):
ldm feature [project] --enable LPS-122920
```

## `config edit` (legacy: `edit`)

Rapidly modify project configuration files in your system's `$EDITOR` (defaults to `vi` or `notepad`).

```bash
ldm config edit [project]                         # Edit .liferay-docker.meta
ldm config edit [project] --target properties     # Edit portal-ext.properties

# Legacy flat form (still works):
ldm edit [project]
```

## `config log-level` (legacy: `log-level`)

Manage Liferay internal logging levels (Log4j2) without restarts.

```bash
# List current custom levels
ldm config log-level --list

# Set a specific category to DEBUG
ldm config log-level [project] --bundle portal --category com.liferay.portal --level DEBUG

# Interactive configuration
ldm config log-level

# Legacy flat form (still works):
ldm log-level [project] --list
```

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-10* | *Last Reviewed: 2026-07-10*

## Global Flags

The following flags can be passed to almost any command:

- **`-v`, `--verbose`**: Enable verbose debug logging to trace exact shell commands, API calls, and Docker interactions.
- **`--info`**: Show informational logging (a middle tier between standard output and debug).
- **`-y`, `--non-interactive`**: Accept all defaults and skip confirmation prompts.
- **`--upgrade-db`**: Force-enables Liferay's database auto-upgrade tool on startup (`LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true`).
- **`--no-upgrade-db`**: Force-disables Liferay's database auto-upgrade tool.
- **`--backup-on-upgrade`**: Force-enables automatic database backup snapshot creation before running version upgrades.
- **`--no-backup-on-upgrade`**: Force-disables automatic database backup snapshot creation before running version upgrades.
- **`--tag-prefix`**: Force specific tag discovery prefix when resolving latest tags.
- **`--skip-project`**: Skips project discovery. Useful for global diagnostics like `ldm doctor --skip-project`.
- **`--delete`**: Specifically removes global infrastructure components or bypasses safety prompts.
