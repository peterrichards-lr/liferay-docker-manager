# CLI Reference

## Scripting & Automation

LDM is designed to be pipeline-friendly. The `ldm doctor` command returns a non-zero exit code if critical environment issues are detected.

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
ldm ps || ldm infra-setup --search
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
- **Omni-Admin Captcha**: During testing or CI workflows, you can use the `--no-captcha` flag during initialization or run to automatically disable Liferay's mandatory Omni-Admin CAPTCHA checks. This is strictly opt-in and reversible; running without the flag will automatically re-enable CAPTCHA enforcement.
- **Fast Login**: Use the `--fast-login` flag to automatically bypass typical post-startup prompts, such as the Terms of Use acceptance and the initial password reset screen. *Note: The password policy bypass component does not fully function if you explicitly use the embedded Hypersonic database (`--db hypersonic`). It works perfectly with the default PostgreSQL database.*
- **Filesystem Resilience**: If your project is stored on an external SSD (common on macOS `/Volumes/` paths), Liferay's OSGi container can fail due to bind-mount locking limitations. LDM **automatically detects** these paths and uses a high-performance internal volume for the OSGi state to prevent these errors. You can also force this behavior using the `--internal-state` flag.

---

## Command Reference

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

# Disable Omni-Admin Captchas for automated testing
ldm run demo --tag-latest --no-captcha

# Bypass typical startup prompts (Terms of Use, Password Reset)
# Note: Password policy bypass works best with an external database (--db mysql)
ldm run demo --fast-login --db mysql

# Enable specific Liferay feature flags
ldm run demo --feature LPS-122920 dev beta

# Run on a custom port
ldm run my-project --port 8081

# Using the alias
ldm up demo

# Initialize with "Confidence Booster" samples
ldm run demo --samples

# Interactive run (will prompt for version and project name)
# Prompts are automatically pre-filled using the Cascading Defaults system
ldm run
```

#### SSL Defaults (New Projects)

LDM uses smarter defaults for SSL based on your hostname. When a custom `--host-name` is used, SSL is enabled by default to support modern Liferay features like Client Extensions.

| Command | Host Name | SSL Default | Access URL |
| :--- | :--- | :--- | :--- |
| `ldm run` | `localhost` | `False` | `http://localhost:8080` |
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

#### ⚡️ Performance Tuning (Startup Optimizations)

... [Previous content kept] ...

### `init-from` (Live Link)

Initialize a project from a source workspace and establish a **persistent link**. This command records the workspace path in the project metadata and automatically starts the `monitor` process to sync your code changes in real-time.

```bash
# ldm init-from <source_path> [project_name] [--host-name custom.local]
ldm init-from ~/repos/my-workspace my-project --host-name forge.demo

# Initialize with the latest tag and disable CAPTCHAs for CI testing
ldm init-from ~/repos/my-workspace my-ci-project -y --tag-latest --no-captcha
```

### `import` (Static Snapshot)

Scaffold a new project by taking a **one-time static import** of an existing workspace. This project is detached from the source; changes to the source workspace will not be synced. Follows the same internal deployment sequence as `init-from`.

```bash
# ldm import <source_path> [project_name] [--host-name custom.local]
ldm import ~/repos/my-workspace my-static-project

# Import using a specific release type filter
ldm import ~/repos/my-workspace my-static-project --tag-latest --release-type qr
```

### `monitor`

Restarts the background watch process for a project linked to a Liferay workspace. This command can **only be used for projects created with `init-from`**. It automatically syncs built artifacts (`.jar`, `.war`, `.zip`) whenever they are updated in the workspace.

```bash
ldm monitor [project_name] --delay 2.0
```

### `logs`

View real-time logs. Supports filtering by project, specific services, or global infrastructure components.

```bash
ldm logs [project] [service1] [service2] ...

# Examples:
ldm logs                  # All logs for current project
ldm logs demo             # All logs for 'demo' project
ldm logs -n 250           # Show last 250 lines (default: 100)
ldm logs -t               # Show timestamps
ldm logs --since 1h       # Show logs from the last hour
ldm logs --until 10m       # Show logs until 10 minutes ago
ldm logs --infra          # Show logs for all global infrastructure (ES, Proxy, etc.)
ldm logs --infra es       # Show logs only for Global Elasticsearch
ldm logs --infra proxy    # Show logs only for Global SSL Proxy
ldm logs demo liferay     # Only Liferay logs for 'demo'
ldm logs demo liferay my-ext # Multi-service tailing
```

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

### `env`

Manage persistent environment variables in project metadata.

```bash
ldm env [project] KEY=VALUE
ldm env [project] --remove KEY
ldm env                   # Interactive manager (view and edit all)
```

### `edit`

Rapidly modify project configuration files in your system's `$EDITOR` (defaults to `vi` or `notepad`).

```bash
ldm edit [project]              # Edit .liferay-docker.meta
ldm edit [project] --target properties # Edit portal-ext.properties
```

### `log-level`

Manage Liferay internal logging levels (Log4j2) without restarts.

```bash
# List current custom levels
ldm log-level --list

# Set a specific category to DEBUG
ldm log-level [project] --bundle portal --category com.liferay.portal --level DEBUG

# Interactive configuration
ldm log-level
```

### `doctor`

Verify host environment health, Docker resources (CPUs/Memory), disk space (warns on dangling volumes), and project dependencies. Now includes checks for required tools: `mkcert`, `telnet`, `nc`, `lcp`, and the Docker Compose V2 plugin.

```bash
ldm doctor          # Health check for current/selected project
ldm doctor --all    # Batch validate every project in your workspace
ldm doctor --fix-hosts # Automatically add missing domains to /etc/hosts (will prompt for sudo)
```

### `fix-hosts`

Manually append missing project hostnames to your system's `/etc/hosts` file. This command is automatically triggered by `ldm run` if a resolution failure is detected, but can also be called surgically.

```bash
# Fix all hostnames for a project (including extension subdomains)
ldm fix-hosts my-project

# Add a specific raw hostname
ldm fix-hosts custom.local

# Run a full fix for all projects via doctor
ldm doctor --fix-hosts
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

### `browser` (alias: `open`)

Launch the project URL in your system browser. If no project is specified, LDM will present a list of currently running projects to select from.

```bash
ldm browser [project]
ldm open [project]
### `upgrade`

Automatically download and install the latest version of LDM for your architecture. Includes integrity verification. If the automatic process fails, LDM will provide a manual `curl` or `PowerShell` command to complete the installation.

```bash
ldm upgrade             # Standard upgrade to latest stable
ldm upgrade --beta      # Upgrade to the latest pre-release/beta
ldm upgrade --repair    # Re-download current version to fix integrity issues
```

### `update-check`

Check for available updates without installing them.

```bash
ldm update-check        # Check for stable updates
ldm update-check --beta # Check for beta/pre-release versions
```

### `completion`

Configure shell autocompletion for `ldm`. Supports **Bash**, **Zsh**, and **Fish**.

```bash
ldm completion
```

**Setup Summary:**

1. Run `ldm completion` to get the command for your shell.
2. Add the provided command to your shell profile (`.zshrc`, `.bashrc`, or `config.fish`).
3. Restart your terminal.

This enables TAB completion for all commands and project names.

### `man`

Display the comprehensive manual page for LDM. This provides an offline reference for all commands, options, and architecture details.

```bash
ldm man
```

#### Native Integration (`man ldm`)

To support the native system `man ldm` command, add this to your shell profile (`.zshrc` or `.bashrc`):

```bash
export MANPATH="$MANPATH:$HOME/.ldm/man"
```

### `renew-ssl`

Refresh project-specific SSL certificates immediately.

```bash
ldm renew-ssl           # Interactive selector
ldm renew-ssl demo      # Renew for 'demo' specifically
ldm renew-ssl --all     # Renew certificates for every project
```

### `init-common`

Initialize or recreate the baseline global configuration (`common/` folder) from internal resources.

```bash
ldm init-common
```

### `infra-setup`, `infra-down`, `infra-restart`

Independently manage global infrastructure services (Traefik proxy, Search sidecar, Bridge).

```bash
ldm infra-setup            # Start global services manually
ldm infra-setup --search   # Also initialize the Global Search container
ldm infra-down             # Stop and remove global services
ldm infra-restart          # Reset all global services in one go
ldm infra-restart --search # Restart and also initialize/restart Global Search
```

> [!TIP]
> **Sidecar Fallback**: If the Global Search (ES8) container is not running, `ldm` will automatically default to Liferay's internal **Sidecar** search. It also cleans up global ES configurations in your project to ensure the Sidecar initializes correctly.

### `migrate-search`

Migrates a project from using the internal Sidecar search to the shared **Global Search container**.

```bash
ldm migrate-search [project]
```

**What it does:**

1. Verifies the project is stopped.
2. Ensures the Global Search container is running (offers to start it).
3. Deletes internal indices (`data/elasticsearch7` or `data/elasticsearch8`).
4. Re-syncs Global ES configurations from `common/`.
5. Offers to restart the project immediately.

### `prune`

Identify and reclaim disk space by safely removing orphaned resources. This command scans your Docker environment for containers and global search snapshots that no longer have a matching project folder on your disk, as well as cleaning up temporary files and large asset caches. If `ldm doctor` warns you about low disk space, run this along with `docker system prune --volumes`.

```bash
ldm prune
ldm prune --seeds --samples   # Also clear large pre-warmed asset caches
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

### `config`

View or set generic custom environment variables inside a project's metadata. (For core LDM settings, see `ldm defaults`).

```bash
ldm config                  # Interactive manager (view and edit all)
ldm config MY_VAR "value"   # Set a project-level environment variable
ldm config MY_VAR --remove  # Remove a custom environment variable
```

### `defaults`

View or manage LDM's Cascading Configuration Defaults. This system resolves settings (like the default DB type, search mode, or host name) using a hierarchy: Convention -> Global -> User -> Project.

```bash
# View the resolved configuration tree and their sources
ldm defaults

# Set a custom default just for your local user (~/.ldmrc)
ldm defaults db_type mysql

# Remove a local user default to fall back to the convention
ldm defaults --remove db_type

# Set a system-wide global default (requires permissions, writes to /etc/ldmrc)
sudo ldm defaults port 9090 --global
```

---
