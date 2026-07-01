# CLI Reference

> [!NOTE]
> **CLI Namespacing**: As of v2.11, commands are organized into logical namespaces (`infra`, `cloud`, `config`, `system`). All legacy flat-form commands (e.g. `ldm prune`, `ldm infra-setup`, `ldm doctor`) remain **fully supported as transparent aliases** and will continue to work indefinitely.

## Scripting & Automation

LDM is designed to be pipeline-friendly. The `ldm system doctor` command returns a non-zero exit code if critical environment issues are detected.

### Automating Interactive Prompts (Piped Input)

When running LDM in an automated environment (or if you just want to skip interactive menus quickly), you can pipe responses directly into the command using standard shell `echo` formatting.

*Note: Ensure your `echo` pipes into LDM directly. Due to shell precedence, `echo "y" | cd /tmp && ldm run` pipes to `cd` instead of LDM. Use `cd /tmp && echo "y" | ldm run` instead.*

#### Example: Automate starting a new project

```bash
# Provide 'n' to select 'new', 'my-project' for the name, and \n (enters) to accept default host/tag options
echo -e "n\nmy-project\n\n\n" | ldm run
```

#### Example: Automate project teardown

```bash
# Automatically confirm project deletion, and confirm removal of global search index
echo -e "y\ny" | ldm down my-project --delete
```

### Health Check Pipe

Ensure your environment is healthy before attempting to start a project:

```bash
ldm doctor --skip-project && ldm run my-project
```

### Automation Patterns

Check if services are running before executing operations:

```bash
# Start infrastructure only if it's not already running
ldm ps || ldm infra setup --search  # (or legacy: ldm infra-setup --search)
```

### CI/CD Integration

You can use LDM in automated scripts to verify infrastructure:

```bash
if ldm doctor --skip-project; then
  echo "Environment is healthy, proceeding..."
else
  echo "Critical environment failure!"
  exit 1
fi
```

---

## Interactive Mode Tips

- **Fuzzy Search Selection**: In any project selection menu, you can simply start typing to filter the list. The menu will update in real-time to match project names or version tags.
- **Smart Project Detection**: `ldm` resolves project locations using this priority:
    1. **Direct Path**: Absolute or relative path (e.g., `ldm logs ./my-proj` or `ldm logs /opt/ldm/proj`).
    2. **CWD**: If the current directory is an LDM project.
    3. **Global Workspaces**: Searches `LDM_WORKSPACE` (if set), `~/ldm`, and `/Volumes/SanDisk/ldm`.
    4. **Deep Search**: Scans the above directories for projects matching the name in their `.liferay-docker.meta`.
- **Quick Quit**: You can type `q` at any interactive prompt to safely abort the current command.
- **Initialization Overrides**: When a project already exists, you can choose:
  - `y` (Yes): Overwrite configuration and artifact files.
  - `n` (No): Continue initialization but **skip/keep** existing files.
  - `c` (Clean): Delete the entire project folder and start fresh.
  - `q` (Quit): Abort the process entirely.
- **Bypass Prompts**: Use the `-y` or `--non-interactive` flag to skip all confirmations and use default values. This is ideal for scripts and CI/CD pipelines.
- **Tag Prompt & Discovery**: When running `ldm run` without a tag, the interactive prompt will automatically fetch the latest release matching your default release type (usually LTS) and offer it as the default choice. You can simply press Enter to accept it, or type a specific release type (`lts`, `qr`, `u`), a prefix (`2025.q4`), or an exact tag name.
- **Automated Latest Tags**: In non-interactive environments, LDM will automatically discover and use the latest tag matching your default release type (LTS) if no tag is explicitly provided. You can also force specific discovery using `--tag-latest` or `--tag-prefix`.
- **Liferay Version Upgrades & Data Persistence**: If you have an existing LDM project running a specific Liferay version (e.g. `2026.q2.4-lts`) and want to upgrade it (e.g. to `2026.q2.5-lts`), you can safely do so by running `ldm run --tag 2026.q2.5-lts` (or choosing the new tag interactively). LDM's hybrid volume strategy keeps your database and file data intact inside persistent Docker volumes, so upgrading the image version **will not delete or reset** your local data and workspace files. (Note: Downgrades are blocked by default to prevent database schema corruption; use `--force-downgrade` only if explicitly required).
- **Advanced Flags**: For information on pipeline automation flags (`--no-captcha`, `--fast-login`), JVM tuning (`--lean`), and filesystem overrides (`--internal-state`), please see the [Advanced Usage & Flags](ADVANCED_CLI.md) guide.

---

## Liferay Version Upgrades

LDM supports changing Liferay Docker image tags on existing projects seamlessly. If you run a project with a newer version tag (e.g., `ldm run --tag 2026.q2.5-lts` on a project that was previously running `2026.q2.4-lts`), LDM will orchestrate the transition safely.

### How it Works & Data Persistence

1. **Volume Preservation**: LDM's hybrid volume strategy keeps your database data and document library assets in named Docker volumes, ensuring that changing the Liferay Docker image version tag **does not delete or reset** your database or files.
2. **Upgrade Detection**: LDM automatically detects version upgrades during startup by comparing the new tag with `last_run_liferay_version` stored in the project's metadata.
3. **Automated Database Backup**: If an upgrade is detected and neither `--backup-on-upgrade` nor `--no-backup-on-upgrade` is specified, LDM will ask if you want to take a database backup snapshot first. If you confirm (or pass `--backup-on-upgrade`), LDM will temporarily start the database service (if stopped) and execute an orchestrated SQL dump backup (`Pre-upgrade snapshot to {tag}`) before Liferay starts.
4. **Database Auto-Upgrade**: New Liferay versions often make underlying database schema changes. If an upgrade is detected and neither `--upgrade-db` nor `--no-upgrade-db` is specified, LDM will ask: `"Do you want to run Liferay's database auto-upgrade tool on startup?"`. If you confirm (or pass `--upgrade-db`), LDM will inject the `LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true` environment variable, enabling Liferay to perform a schema upgrade on boot. LDM will omit this environment variable on subsequent runs to prevent repeating the upgrade.
5. **Downgrade Safety**: Downgrading Liferay or PostgreSQL versions can cause database corruption. By default, LDM blocks version downgrades. To override this protection, you must explicitly pass the `--force-downgrade` flag.

---

## Command Reference

### Global Flags

The following flags can be passed to almost any command:

- **`-v`, `--verbose`**: Enable verbose debug logging to trace exact shell commands, API calls, and Docker interactions.
- **`--info`**: Show informational logging (a middle tier between standard output and debug).
- **`-y`, `--non-interactive`**: Accept all defaults and skip confirmation prompts.
- **`--upgrade-db`**: Force-enables Liferay's database auto-upgrade tool on startup (`LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true`).
- **`--no-upgrade-db`**: Force-disables Liferay's database auto-upgrade tool.
- **`--backup-on-upgrade`**: Force-enables automatic database backup snapshot creation before running version upgrades.
- **`--no-backup-on-upgrade`**: Force-disables automatic database backup snapshot creation before running version upgrades.

### `list` (alias: `ls`)

Display a tabulated overview of all initialized LDM sandbox environments.

```bash
ldm list
ldm ls
```

### `run` (alias: `up`)

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

#### `--open` Switch

Use `--open` to automatically launch the Liferay URL in your system browser once the instance is ready. This is equivalent to running `ldm browser` immediately after startup, but in a single command.

#### `--scale` Switch

Use `--scale SERVICE=N` to boot a scaled stack without having to run `ldm scale` as a separate step. Multiple services can be scaled at once:

```bash
ldm run demo --scale liferay=2 --scale my-ext=3
```

### `init`

Initialize project scaffolding (creating `.liferay-docker.meta`, `portal-ext.properties`, etc.) without actually starting the Docker containers. Accepts many of the same configuration flags as `run`.

```bash
ldm init my-project --tag 2024.q4.0 --db mysql
```

#### External Database Integration

LDM supports connecting your local Liferay instance to an external database (e.g., a shared development database or a standalone local database server) instead of running a database container within the project's Docker Compose stack.

To initialize a project with an external database:

```bash
ldm init my-project --db external
```

When you use `--db external`, LDM will launch an interactive wizard to gather your JDBC connection details (Host, Port, Database Name, Username, Password) and automatically generate the necessary properties in your `portal-ext.properties`. The database service container will be entirely omitted from the generated stack.

#### SSL Defaults (New Projects)

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

#### 🛡️ Modern Liferay & JDK 17+ Standards

LDM automatically hardens modern environments (DXP 2024+ and modern Quarterly Releases) to ensure stable startup:

- **JVM Module Exports**: Automatically injects mandatory `--add-opens` flags for JDK 17+ (covering `java.net`, `java.lang.reflect`, `security`, and more).
- **Hardened MySQL 8.4 (LTS)**:
  - Standardized on the **MariaDB JDBC Driver** and `MariaDB103Dialect` to mirror **Liferay Cloud (LXC)** environments.
  - Forces `mysql_native_password` authentication for CI compatibility.
  - Includes performance-optimized connection parameters (e.g., `rewriteBatchedStatements`, `prepStmtCacheSize`).
  - **Redline Configuration**: Explicitly sets `hibernate.dialect` and `jdbc.default.*` properties in `portal-ext.properties` to ensure reliable interpretation of mixed-case keys (like `driverClassName`).
  - Prioritizes `LIFERAY_JDBC_DEFAULT_*` environment variables ONLY for runtime user overrides; LDM baseline always uses `portal-ext.properties`.
- **Proactive Boot Sequencing**: Configures `depends_on` with healthchecks to ensure Liferay only starts once the database is fully ready to accept connections.

### `init-from` (Live Link)

Initialize a project from a source workspace and establish a **persistent link**. This command records the workspace path in the project metadata and automatically starts the `monitor` process to sync your code changes in real-time. If a Liferay Cloud Workspace is detected, it will also launch an interactive wizard to hydrate the data from the remote environment.

```bash
# ldm init-from <source_path> [project_name] [--host-name custom.local]
ldm init-from ~/repos/my-workspace my-project --host-name forge.demo

# Initialize with the latest tag and disable CAPTCHAs for CI testing
ldm init-from ~/repos/my-workspace my-ci-project -y --tag-latest --no-captcha

# Manually bind a Liferay Cloud project ID to the local workspace
ldm init-from ~/repos/my-workspace my-project --cloud-project lctintranet
```

### `import` (Static Snapshot)

Scaffold a new project by taking a **one-time static import** of an existing workspace. This project is detached from the source; changes to the source workspace will not be synced. Follows the same internal deployment sequence as `init-from`. If a Liferay Cloud Workspace is detected, it will also launch an interactive wizard to hydrate the data from the remote environment.

```bash
# ldm import <source_path> [project_name] [--host-name custom.local]
ldm import ~/repos/my-workspace my-static-project

# Import using a specific release type filter
ldm import ~/repos/my-workspace my-static-project --tag-latest --release-type qr

# Manually bind a Liferay Cloud project ID to the local workspace
ldm import ~/repos/my-workspace my-project --cloud-project lctintranet
```

### `quickstart`

Bootstrap and start a predefined accelerator demo stack in one command. Downloads target repositories, configures metadata, and automatically starts the environment.

```bash
# Bootstrap the AICA (AI Commerce Accelerator) template
ldm quickstart aica

# Bootstrap and expose the stack dynamically using lfr-tunnel
ldm quickstart aica --share --share-subdomain my-custom-demo
```

Custom templates and repository mappings can be configured by defining overrides in `~/.ldm_templates.json`.

### `package`

Package a project snapshot (code elements, database, and volumes) into a portable LDM package (`.ldmp` archive) alongside a SHA-256 checksum signature (`.ldmp.sha256`).

```bash
# Package the current project
ldm package

# Package a specific project, outputting to a custom directory
ldm package my-project -o /tmp/packages

# Package using a specific repository manifest identifier and the latest snapshot
ldm package my-project --repo my-owner/my-repo --use-latest
```

### Data Management Commands

LDM includes powerful commands for managing your project's database, OSGi state, and Elasticsearch indices. For full details on the following commands, please see the [Data Management Guide](DATA_MANAGEMENT.md).

- **`snapshot` / `restore`**: Backup and recover exact project states.
- **`package`**: Export a project snapshot into a portable `.ldmp` package.
- **`hydrate`**: Create or restore a project from a local Liferay Cloud backup.
- **`cloud-fetch`**: Sync an existing local project directly with a live Liferay Cloud (LCP) environment.
- **`reset` / `re-seed`**: Surgically clear data folders or completely wipe a project back to its original vanilla state.

### `monitor`

Restarts the background watch process for a project linked to a Liferay workspace. This command can **only be used for projects created with `init-from`**. It automatically syncs built artifacts (`.jar`, `.war`, `.zip`) whenever they are updated in the workspace.

```bash
ldm monitor [project_name] --delay 2.0
```

### `logs`

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
ldm logs --infra          # Show logs for all global infrastructure (ES, Proxy, etc.)
ldm logs --infra es       # Show logs only for Global Elasticsearch
ldm logs --infra proxy    # Show logs only for Global SSL Proxy
ldm logs demo liferay     # Only Liferay logs for 'demo'
ldm logs demo liferay my-ext # Multi-service tailing (all replicas)
```

#### Targeting a Specific Scaled Replica (`--instance N` / `-i N`)

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

### `stop`, `restart`, `down` (alias: `rm`)

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

### `status`

View the status of all projects in the current workspace.

```bash
ldm status
```

> [!TIP]
> Projects marked with a 🌱 (seedling) emoji were initialized from a **Seeded State**, meaning they started with a pre-calculated database and OSGi cache for near-instant boot times.

---

### `deploy`

Hot-deploy built artifacts or rebuild extension images.

```bash
ldm deploy [project] [service] --rebuild

# Examples:
ldm deploy                # Sync all artifacts and refresh stack
ldm deploy demo my-ext --rebuild  # Rebuild and restart one extension
```

### `scale`

Scale services within a project for multi-node simulation and clustering tests.

```bash
ldm scale [project] service=count

# Examples:
ldm scale demo liferay=2  # Scale Liferay to 2 nodes (enables clustering)
ldm scale demo my-ext=3   # Scale a client extension to 3 nodes
```

### `shell` & `gogo`

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

### `config env` (legacy: `env`)

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

### `config feature` (legacy: `feature`)

Quickly toggle Liferay feature flags without manually editing `portal-ext.properties`. Requires a project restart to take effect.

```bash
ldm config feature [project] --enable LPS-122920
ldm config feature [project] --disable LPS-111111 LPS-222222

# Legacy flat form (still works):
ldm feature [project] --enable LPS-122920
```

### `config edit` (legacy: `edit`)

Rapidly modify project configuration files in your system's `$EDITOR` (defaults to `vi` or `notepad`).

```bash
ldm config edit [project]                         # Edit .liferay-docker.meta
ldm config edit [project] --target properties     # Edit portal-ext.properties

# Legacy flat form (still works):
ldm edit [project]
```

### `config log-level` (legacy: `log-level`)

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

### `system doctor` (legacy: `doctor`)

Verify host environment health, Docker resources (CPUs/Memory), disk space (warns on dangling volumes), and project dependencies. Includes checks for required tools: `mkcert`, `telnet`, `nc`, `lcp`, and the Docker Compose V2 plugin.

```bash
ldm system doctor          # Health check for current/selected project
ldm system doctor --all    # Batch validate every project in your workspace
ldm system doctor --detailed  # Show detailed troubleshooting hints and automatic fixes
ldm system doctor --fix       # Automatically apply recommended fixes
ldm system doctor --bundle    # Generate a sanitized zip bundle of logs and config
ldm system doctor --slug      # Output a machine-readable environment identifier string
ldm system doctor --fix-hosts # Add missing domains to /etc/hosts (will prompt for sudo)

# Legacy flat form (still works):
ldm doctor --fix
```

### `system fix-hosts` (legacy: `fix-hosts`)

Manually append missing project hostnames to your system's `/etc/hosts` file. This command is automatically triggered by `ldm run` if a resolution failure is detected, but can also be called surgically.

```bash
# Fix all hostnames for a project (including extension subdomains)
ldm system fix-hosts my-project

# Add a specific raw hostname
ldm system fix-hosts custom.local

# Run a full fix for all projects via doctor
ldm system doctor --fix-hosts

# Legacy flat form (still works):
ldm fix-hosts my-project
```

### `wait` (Readiness Gating)

Blocks execution until a Liferay instance is genuinely ready for work. This is highly recommended for CI/CD pipelines and complex deployment scripts. Unlike basic Docker healthchecks, `ldm wait` performs a **3-Phase Verification**:

1. **Log Readiness**: Scans Docker logs for the Tomcat `"Server startup"` marker.
2. **HTTP Availability**: Polls the instance until it responds with an `HTTP 200` or `302` on its primary port.
3. **CPU Idle State**: Actively monitors the container's CPU usage, blocking until it drops below 15% for three consecutive checks. This ensures background OSGi initialization (like `BundleSiteInitializer`) is truly finished.

```bash
# Wait for the current project to be fully idle (up to 10 minutes)
ldm wait

# Wait for a specific project with a custom timeout
ldm wait my-project --timeout 300
```

### `status` (alias: `ps`)

Lightweight summary of all active global services and running projects.

```bash
ldm status          # Show active global services and running projects
ldm status --all    # Show all managed projects (including stopped ones)
ldm ps
```

### `info`

Displays a user-friendly, formatted view of a project's internal metadata (`.liferay-docker.meta`). This is incredibly useful for diagnosing configuration issues or verifying project state without opening the file manually.

```bash
ldm info [project]
```

### `browser` (alias: `open`)

Launch the project URL in your system browser. If no project is specified, LDM will present a list of currently running projects to select from.

```bash
ldm browser [project]
ldm open [project]
ldm browser [project] -u /path # Open a specific path (e.g., /web/guest)
ldm browser --list             # List available URLs without opening
ldm browser --remove           # Remove saved custom URLs from history
```

### `system upgrade` (legacy: `upgrade`)

Automatically download and install the latest version of LDM for your architecture. Includes integrity verification. If the automatic process fails, LDM will provide a manual `curl` or `PowerShell` command to complete the installation.

```bash
ldm system upgrade                   # Standard upgrade to latest stable
ldm system upgrade --pre-release     # Upgrade to the latest pre-release/beta
ldm system upgrade --repair          # Re-download current version to fix integrity issues
ldm system upgrade --check           # Check for updates without installing

# Legacy flat form (still works):
ldm upgrade --beta
```

### `system completion` (legacy: `completion`)

Configure shell autocompletion for `ldm`. Supports **Bash**, **Zsh**, **Fish**, and **PowerShell**.

```bash
ldm system completion           # Auto-detect your shell
ldm system completion zsh       # Generate setup for Zsh specifically
ldm system completion bash      # Generate setup for Bash
ldm system completion fish      # Generate setup for Fish

# Legacy flat form (still works):
ldm completion
```

**Setup Summary:**

1. Run `ldm system completion` to get the command for your shell.
2. Add the provided command to your shell profile (`.zshrc`, `.bashrc`, or `config.fish`).
3. Restart your terminal.

This enables TAB completion for all commands, namespaces, subcommands, and project names.

### `system man` (legacy: `man`)

Display the comprehensive manual page for LDM. This provides an offline reference for all commands, options, and architecture details.

```bash
ldm system man

# Legacy flat form (still works):
ldm man
```

#### Native Integration (`man ldm`)

To support the native system `man ldm` command, add this to your shell profile (`.zshrc` or `.bashrc`):

```bash
export MANPATH="$MANPATH:$HOME/.ldm/man"
```

### `infra renew-ssl` (legacy: `renew-ssl`)

Refresh project-specific SSL certificates immediately.

```bash
ldm infra renew-ssl           # Interactive selector
ldm infra renew-ssl demo      # Renew for 'demo' specifically
ldm infra renew-ssl --all     # Renew certificates for every project

# Legacy flat form (still works):
ldm renew-ssl demo
```

### `infra init-common` (legacy: `init-common`)

Initialize or recreate the baseline global configuration (`common/` folder) from internal resources.

```bash
ldm infra init-common

# Legacy flat form (still works):
ldm init-common
```

### `infra setup` / `infra down` / `infra restart` (legacy: `infra-setup`, `infra-down`, `infra-restart`)

Independently manage global infrastructure services (Traefik proxy, Search sidecar, Bridge).

```bash
ldm infra setup            # Start global services manually
ldm infra setup --search   # Also initialize the Global Search container
ldm infra setup --es7      # Force Global Search to use legacy Elasticsearch 7
ldm infra down             # Stop and remove global services
ldm infra restart          # Reset all global services in one go
ldm infra restart --search # Restart and also initialize/restart Global Search

# Legacy flat forms (still work):
ldm infra-setup --search
ldm infra-down
ldm infra-restart
```

> [!TIP]
> **Sidecar Fallback**: If the Global Search (ES8) container is not running, `ldm` will automatically default to Liferay's internal **Sidecar** search. It also cleans up global ES configurations in your project to ensure the Sidecar initializes correctly.

### `infra migrate-search` (legacy: `migrate-search`)

Migrates a project from using the internal Sidecar search to the shared **Global Search container**.

```bash
ldm infra migrate-search [project]

# Legacy flat form (still works):
ldm migrate-search [project]
```

**What it does:**

1. Verifies the project is stopped.
2. Ensures the Global Search container is running (offers to start it).
3. Deletes internal indices (`data/elasticsearch7` or `data/elasticsearch8`).
4. Re-syncs Global ES configurations from `common/`.
5. Offers to restart the project immediately.

### `system prune` (legacy: `prune`)

Identify and reclaim disk space by safely removing orphaned resources. This command scans your Docker environment for containers and global search snapshots that no longer have a matching project folder on your disk, as well as cleaning up temporary files and large asset caches. If `ldm system doctor` warns you about low disk space, run this along with `docker system prune --volumes`.

```bash
ldm system prune
ldm system prune --seeds --samples   # Also clear large pre-warmed asset caches
ldm system prune --all               # Run all pruning operations without asking
ldm system prune --clean-hosts       # Remove all LDM-tagged entries from /etc/hosts

# Legacy flat form (still works):
ldm prune --seeds --samples
```

**What it cleans:**

- **Orphaned Containers**: Any container with the `com.liferay.ldm.managed` label whose project folder was manually deleted.
- **Orphaned Search Snapshots**: Leftover Elasticsearch 8.x snapshots in the global vault from deleted projects.
- **Pre-warmed Seeds**: (Optional) Large Database + Search + OSGi state archives used for instant project initialization.
- **Sample Extensions**: (Optional) Cached sample client extensions.
- **Temporary Files**: Residual `.*.tmp` files left behind by interrupted sync or build operations.

### `clear-cache`

Clears the local Docker Hub tag cache. LDM caches Liferay tags for 24 hours to improve performance; use this command to force a fresh fetch from the registry.

```bash
ldm clear-cache
```

### `system relocate`

Safely move your LDM global configuration, Docker volumes, and cached assets to an external drive (e.g., an external SSD). This is highly recommended for macOS users to save internal disk space and bypass filesystem locking issues.

```bash
ldm system relocate /Volumes/SanDisk
```

### `config` (get / set / remove)

View or set generic custom environment variables inside a project's metadata. The `config` command now has explicit subcommands for clarity, while the legacy positional form is still supported.

```bash
# New namespaced form:
ldm config get MY_VAR           # Get a project-level variable
ldm config set MY_VAR "value"   # Set a project-level variable
ldm config remove MY_VAR        # Remove a variable

# Legacy positional form (still works):
ldm config MY_VAR "value"       # Detected as 'set'
ldm config MY_VAR --remove      # Detected as 'remove'
```

### `config defaults` (legacy: `defaults`)

View or manage LDM's Cascading Configuration Defaults. This system resolves settings (like the default DB type, search mode, or host name) using a hierarchy: Convention -> Global -> User -> Project.

```bash
# View the resolved configuration tree and their sources
ldm config defaults

# Set a custom default just for your local user (~/.ldmrc)
ldm config defaults db_type mysql

# Remove a local user default to fall back to the convention
ldm config defaults --remove db_type

# Set a system-wide global default (requires permissions, writes to /etc/ldmrc)
sudo ldm config defaults port 9090 --global

# Legacy flat form (still works):
ldm defaults db_type mysql
```

---

## Backward Compatibility Reference

All legacy flat-form commands are automatically translated to their namespaced equivalents by the `preprocess_args` layer. Both forms are valid and permanent:

| Legacy Command | New Canonical Form |
| :--- | :--- |
| `ldm prune` | `ldm system prune` |
| `ldm doctor` | `ldm system doctor` |
| `ldm upgrade` | `ldm system upgrade` |
| `ldm completion` | `ldm system completion` |
| `ldm man` | `ldm system man` |
| `ldm fix-hosts` | `ldm system fix-hosts` |
| `ldm dev-setup` | `ldm system dev-setup` |
| `ldm infra-setup` | `ldm infra setup` |
| `ldm infra-down` | `ldm infra down` |
| `ldm infra-restart` | `ldm infra restart` |
| `ldm init-common` | `ldm infra init-common` |
| `ldm renew-ssl` | `ldm infra renew-ssl` |
| `ldm migrate-search` | `ldm infra migrate-search` |
| `ldm cloud-fetch` | `ldm cloud fetch` |
| `ldm env` | `ldm config env` |
| `ldm feature` | `ldm config feature` |
| `ldm log-level` | `ldm config log-level` |
| `ldm edit` | `ldm config edit` |
| `ldm defaults` | `ldm config defaults` |

---

## All CLI Options Reference

The following is a comprehensive index of all registered CLI option flags and their descriptions:

- **`--archetype`**: Apply an Extensible Stack Archetype (e.g. 'keycloak-sso', 'clustered')
- **`--ascii`**: Enable ASCII-safe output translation.
- **`--auto-install-lfr-tunnel`**: Automatically install lfr-tunnel if not found in PATH.
- **`--background`**: Run dashboard in background.
- **`--backup-dir`**: Directory path to backup archives.
- **`--benchmark`**: Display performance benchmark on execution.
- **`--build-info`**: Inject build metadata into the source.
- **`--bump`**: Increment the version logically.
- **`--clone-only`**: Force cloning the Git repository instead of downloading the LDM package (.ldmp).
- **`--container`**: Show detailed Docker container diagnostic checks.
- **`--docker`**: Show detailed Docker diagnostic checks.
- **`--domain`**: Custom domain prefix (e.g. lfr-demo.online, lfr-demo.se).
- **`--download`**: Force downloading of dependencies.
- **`--dry-run`**: Preview execution without mutations.
- **`--files-only`**: Extract or backup files/folders only.
- **`--force-boot`**: Force a container reboot instead of immediate runtime reindexing.
- **`--force-downgrade`**: Force a version downgrade (bypassing safety validations).
- **`--grep`**: Grep search pattern for filtering log lines.
- **`--grep-i`**: Case-insensitive grep search.
- **`--grep-v`**: Inverted grep search (select non-matching lines).
- **`--hydrate-from`**: Automatically hydrate data from a Liferay Cloud environment.
- **`--image`**: Custom Docker image to use for the sharing tunnel sidecar.
- **`--index`**: Force indexing check.
- **`--inspector`**: Expose the lfr-tunnel local inspector dashboard on port 4040.
- **`--keep-config`**: Retain global config file ~/.ldmrc.
- **`--keep-last`**: Keep only the specified number of most recent snapshots.
- **`--latest`**: Restore the most recent snapshot.
- **`--leave-running`**: Keep the running project active and abort the import if it is currently running.
- **`--list-backups`**: List backups in project work-folders.
- **`--list-envs`**: List all cloud environments.
- **`--logs`**: Stream container logs.
- **`--name`**: Specify target name.
- **`--no-color`**: Disable ANSI color codes in output.
- **`--no-env-sync`**: Skip syncing environment variables from Liferay Cloud.
- **`--no-home-warn`**: Suppress warning when running LDM from the root of the user's home directory.
- **`--tunnel-managed-cors`**: Skip local CORS patching and defer entirely to the tunnel gateway's dynamic header injection.
- **`--no-move`**: Skip moving existing data (just create symlinks).
- **`--no-restart`**: Do not automatically stop and restart the containers.
- **`--no-run`**: Update the metadata without automatically restarting the stack.
- **`--no-unicode`**: Disable Unicode characters in output and force ASCII safe-replacements.
- **`--older-than`**: Delete snapshots older than the specified number of days.
- **`--output`**: Directory path to save the generated package.
- **`--overwrite-registry`**: Automatically overwrite existing project registry entries in case of collisions.
- **`--ports`**: Comma-separated ports to expose (defaults to 8080).
- **`--print`**: Output current version string only.
- **`--project`**: Show detailed Project diagnostic checks.
- **`--project-id`**: Specific project ID to rescue.
- **`--promote`**: Promote the current beta to a stable release.
- **`--provider`**: Tunnel provider (defaults to lfr-tunnel).
- **`--quiet`**: Quiet mode (suppress info logs).
- **`--reboot`**: Force a container reboot instead of immediate runtime reindexing.
- **`--reindex`**: Force a full search reindex on startup.
- **`--reset`**: Reset cumulative ROI metrics back to zero.
- **`--restore`**: Restore project backup/snapshot.
- **`--service`**: Specify specific container service.
- **`--set`**: Directly set the version string.
- **`--share-domain`**: Custom domain to use when sharing the instance.
- **`--share-image`**: Custom Docker image to use for the sharing tunnel sidecar.
- **`--share-inspector`**: Expose the lfr-tunnel local inspector dashboard on port 4040.
- **`--share-provider`**: Sharing provider to use (defaults to lfr-tunnel).
- **`--status`**: Check for updates without performing the upgrade.
- **`--stop-running`**: Automatically stop the project if it is currently running.
- **`--subdomain`**: Custom subdomain prefix (defaults to machine hostname).
- **`--sync-env`**: Sync configuration env vars.
- **`--system`**: Show detailed system diagnostic checks.
- **`--tail`**: Number of lines to show from the end of the logs.
- **`--timestamps`**: Show timestamps.
- **`--trigger`**: Event trigger for the release package workflow.
- **`--tui`**: Launch interactive terminal menu to configure property overrides.
- **`--up`**: Automatically start the project after reseeding.
- **`--url`**: Remote packages download URL.
- **`--version`**: Target a specific version of LDM (e.g. v2.11.53).
- **`--wait-for-bundles`**: Comma-separated list of expected OSGi bundle symbolic names to wait for.
- **`--wait-for-deployables`**: Scan local workspace for JARs/YAMLs and block until they are deployed in Liferay.
- **`--workflow-name`**: Name of the workflow file.
- **`-V`**: Show LDM version.
- **`-q`**: Quiet mode.
