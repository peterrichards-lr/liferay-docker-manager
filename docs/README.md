# Liferay Docker Manager (ldm)

A professional command-line orchestrator for quickly standing up Liferay Portal and DXP environments using Docker Compose.

> [!NOTE]
> **Project History:** This tool was originally born as part of the [liferay-docker-scripts](https://github.com/peterrichards-lr/liferay-docker-scripts) repository. It has since evolved into a standalone application to provide better modularity and multi-instance stability.

---

## Why use LDM?

`ldm` is designed to be both fast for power users and helpful for newcomers. It follows a consistent usage pattern:

1. **Sensible Defaults**: Whenever a standard Liferay convention exists, `ldm` uses it automatically (e.g., port `8080`, managed DB name `lportal`).
2. **Smart Context**: If you run a command from inside a project folder, `ldm` automatically detects the project context.
3. **Interactive Fallback**: If a required piece of information (like a project name or a Liferay tag) is missing from your command and cannot be detected, `ldm` will **prompt you interactively** or show you a list of choices.
4. **Graceful Abort**: You can type `q` at any interactive prompt to safely cancel the operation.

---

## 🛡️ Compatibility (Verified Environments)

The badges below represent our verified support for various Docker providers. Environments marked as **Hardened** have received specific logic refinements to handle complex file-sharing and permission scenarios.

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardening-FFAB00?style=flat-square&logo=apple) | `2.7.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-colima-pass-e1b5b25c.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-colima-pass-e1b5b25c.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **OrbStack** `v2.1.1` | `29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo=apple) | `2.7.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-orbstack-pass-6c4fb01e.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-orbstack-pass-6c4fb01e.txt) |
| **Linux Workstation** | Fedora 43 | **Native Docker** | `29.4.1` | ![Linux](https://img.shields.io/badge/Linux-Hardening-success?style=flat-square&logo=linux) | `2.7.2` | ✅ | [verify-linux-workstation-fedora-43-native-docker-pass-a6ac5304.txt](../references/verification-results/verify-linux-workstation-fedora-43-native-docker-pass-a6ac5304.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** `v4.69.0` | `29.4.0` | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardening-00C853?style=flat-square&logo=windows) | `2.7.2` | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass-9d1ea613.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass-9d1ea613.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** `WSL 2.4.4` | `29.3.0` | ![WSL2](https://img.shields.io/badge/WSL2-Hardening-blue?style=flat-square&logo=windows) | `2.7.2` | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass-e7efaaf5.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-e7efaaf5.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |
<!-- COMPATIBILITY_END -->

---

## Key Features

- **Multi-Instance Session Isolation**: Run multiple demos side-by-side on the same machine without session cross-talk. `ldm` automatically manages unique session cookie names and virtual hostnames.
- **Strict Infrastructure Isolation**: Projects using the `--sidecar` search mode are cryptographically isolated from the global infrastructure. LDM guarantees that sidecar projects will not start, stop, or reconfigure the shared Global Search container, even if global search flags are provided.
- **Orchestrated Search Snapshots**: Save and restore the exact state of a demonstration, including the database, files, and **Elasticsearch 8.x index state**.
- **Service-Specific Lifecycle**: Manage individual components within a project surgically. Restart or view logs for a single extension without affecting the rest of the stack.
- **Client Extension Lifecycle**: Automatically detects and builds Server-Side Client Extensions (SSCE). Subdomains are automatically generated, and traffic is routed based on `LCP.json`.
- **Zero-Config SSL**: Automated HTTPS using `mkcert` and a global Traefik proxy. Works on Docker Desktop, **Colima**, and **WSL2**.
- **Proactive License Verification**: Automatically detects Liferay XML licenses in `common/`, `deploy/`, or `osgi/modules/` folders. Warns you before boot if a DXP license is missing or expired.
- **Fail-Fast Design**: Proactive environment checking. LDM verifies Docker reachability, volume mounts, resource allocations (CPU/RAM), and **Compose functionality** before execution, providing clean, actionable error messages instead of tracebacks.
- **Port Conflict Detection**: Proactively verifies that required host ports (80, 443, 9200, etc.) are available before starting, preventing cryptic Docker errors.
- **Atomic Configuration**: All project metadata and property updates use safe atomic writes to prevent file corruption during interruptions.
- **Integrity Verification**: As of v2.5.0, all project snapshots and pre-warmed bootstrap seeds include mandatory **SHA-256 checksums**. LDM automatically verifies these during recovery and import to ensure data validity. Users can bypass this check using the `--no-verify` flag if necessary for legacy or manually modified snapshots.
- **Global Project Registry**: Proactively detects project and hostname collisions across the entire filesystem, preventing infrastructure conflicts.
- **Lean JVM Profile**: Includes a resource-optimized JVM profile (`-Xms1536m -Xmx2048m`) designed for constrained environments. LDM **automatically detects** GitHub Actions and applies this profile to ensure reliable boots on 7GB runners. Use the `--lean` flag to trigger this manually.
- **Atomic Project Initialization**: Employs a "Commit/Rollback" pattern for new projects. If initialization fails (e.g. DNS errors or image pull failures), LDM automatically cleans up the half-baked project directory and unregisters it to prevent zombie states.
- **Zero-Race Atomic Deployments**: All file synchronizations (via `ldm deploy` or the `deploy/` bind mount) are staged, permission-fixed (`chmod 666`, `chown 1000:1000`), and atomically moved. This definitively eliminates `AutoDeployScanner` "Unable to write" errors.
- **Architecture-Aware**: The tool detects your OS automatically to fetch the correct optimized binary during self-updates.
- **Shell Autocompletion**: TAB completion for commands and project names across Bash, Zsh, and Fish.
- **Fuzzy Interactive Selection**: Quickly filter through dozens of projects by typing a few characters in any interactive menu.

---

## Documentation

- [Installation Guide](installation.md)
- [Architecture Overview](LDM_ARCHITECTURE.md)
- [Test & Validation Strategy](TESTING.md)
- [Security Posture & Disclosures](SECURITY.md)
- [Future Roadmap](roadmap.md)

---

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

## Installation (Quick Start)

The standalone binary is the recommended way to use LDM.

```bash
# For macOS (Apple Silicon)
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos-arm64 -o /usr/local/bin/ldm

# For macOS (Apple Intel)
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos-x86_64 -o /usr/local/bin/ldm

# For Linux / WSL2
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-linux -o /usr/local/bin/ldm

# Make it executable
sudo chmod +x /usr/local/bin/ldm
```

For detailed instructions and Windows setup, see the **[Full Installation Guide](installation.md)**.

---

## Quick Start

> [!IMPORTANT]
> **Binary vs Script**: If you have installed the **Standalone Binary**, use `ldm` in your commands. If you are using the **Manual Installation**, use `./ldm` (on Linux/macOS) or `ldm.bat` (on Windows) from the root of this repository.

```bash
# 1. THE CONFIDENCE BOOSTER: Run Liferay with pre-configured samples
ldm run my-sample-project --samples

# 2. THE DEVELOPER FLOW: Initialize from a workspace and start monitoring
ldm init-from /path/to/workspace my-project

# 3. THE ARCHIVE FLOW: Import a static snapshot of a workspace
ldm import /path/to/workspace my-static-project

# 4. THE RECOVERY FLOW: Re-create a deleted project from a snapshot folder
ldm run my-recovered-project --snapshot ~/Desktop/old-baseline-snapshot

# 5. THE SURGICAL FLOW: Instantly edit project metadata
ldm edit my-project

# Monitor an existing project (manually)
ldm monitor /path/to/workspace
```

## 🛡️ Sudo & Elevation Policy

To protect the integrity of the application cache (`~/.shiv`) and ensure consistent file permissions, **never run LDM with the `sudo` prefix.**

LDM is designed to run as a standard user and will **automatically request elevation** (prompting for your password) only for specific tasks that require it:

- **`ldm fix-hosts`**: Requires elevation to append entries to `/etc/hosts`. Can be called manually or triggered automatically during `ldm run`.
- **`ldm upgrade`**: Automatically handles cross-device file systems (e.g. Fedora /tmpfs) by using a `cp` + `rm` pattern for system binary replacement. Requires elevation to replace the binary in system paths like `/usr/local/bin`.

> [!NOTE]
> **Non-Interactive Sudo**: When running with the `-y` or `--non-interactive` flag, LDM uses `sudo -n` to perform elevated tasks. If a password is required, the command will fail fast and cleanly instead of hanging the terminal. This is essential for CI/CD pipeline stability.

If you are using `sudo` because of Docker "Permission Denied" errors, do not use `sudo ldm`. Instead, add your user to the `docker` group:

```bash
sudo usermod -aG docker $USER
```

Then restart your terminal session.

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

#### 🌱 Seeding (Instant Boot)

For new projects, LDM automatically attempts to download a **Seeded State** matching your specific configuration (Liferay version, Database type, and Search mode).

- **Database**: Pre-initialized schema for Postgres, MySQL (8.4), or HSQL.
- **OSGi Cache**: Pre-resolved bundle state to skip the resolution phase.
- **Search Index**: Pre-warmed Elasticsearch indices.

| Option | Effect |
| :--- | :--- |
| **`--no-seed`** | Disable automatic seeding and start with a completely fresh, un-initialized project. |
| **`ldm re-seed`** | Wipe all data for an existing project and re-apply the vanilla seed for that version. |

**How Seed Selection Works:**
LDM prioritizes an **exact match** for your environment (e.g., `mysql` + `sidecar`). If an exact match isn't available on GitHub, it falls back to the **High-Performance Baseline** (`postgresql` + `shared`).

---

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

#### Unified Host & SSL Rules (run, init-from, import)

All project initialization commands follow these security and naming rules:

1. **Interactive Hostname**: If no `--host-name` is provided, LDM will prompt you (defaulting to `localhost`).
2. **SSL Auto-Enable**: If a custom hostname is used (anything other than `localhost`), LDM **automatically enables SSL** and routes traffic via port 443.
3. **Explicit Control**: You can override the auto-SSL behavior using `--ssl` or `--no-ssl`.
4. **Port Mapping**: When SSL is active, the direct port `8080` mapping is removed to ensure all traffic passes through the secure Traefik proxy.

#### Client Extension Routing & Wildcard SSL

LDM automates the routing and SSL orchestration for both the main Liferay instance and its related Client Extensions using a **Wildcard Subdomain Strategy**:

- **Predictable Subdomains**: Server-Side Client Extensions (SSCE) with a `Dockerfile` are automatically assigned a unique subdomain based on their ID. For example, if your project host is `my-project.local`, an extension with ID `custom-logic` will be accessible at `https://custom-logic.my-project.local`.
- **Zero-Config HTTPS**: LDM generates a single SSL certificate that covers both the main host and its wildcard (e.g., `my-project.local` and `*.my-project.local`). This secures all extensions automatically.
- **Automated Routing**: Traffic on port 443 is intercepted by the global Traefik proxy and routed to the correct container using SNI (Server Name Indication) and Docker labels.
- **Liferay Integration**: LDM automatically injects `LIFERAY_WEB_SERVER_HOST` and other necessary properties into Liferay to ensure it can communicate seamlessly with its client extension subdomains.

> [!TIP]
> **DNS Resolution**: Standard `/etc/hosts` files do not support wildcards. However, LDM's **`ldm fix-hosts [project]`** and **`ldm doctor --fix-hosts`** commands are intelligent—they scan your project for active client extensions and automatically append entries for both the main hostname and all required subdomains (e.g., `custom-logic.my-project.local`) to your hosts file.

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

### `reset`, `re-seed`

Surgically reset or completely restore a project to its original vanilla state.

```bash
ldm reset [project] [target]      # Clear specific data (state|db|search|all)
ldm re-seed [project]             # Wipe ALL data and re-apply vanilla seed
```

**Examples:**

```bash
ldm reset demo state              # Clear only the OSGi bundle state
ldm reset demo db                 # Clear only the database data
ldm re-seed demo                  # Total project reset to Day Zero (Seeded)
```

---

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

### `snapshot` & `restore`

Backup and recover project states, including files, DB, and search indices.

**Examples:**

```bash
# Create a named snapshot
ldm snapshot demo --name "post-setup-gold-standard"

# List snapshots for a project
ldm restore demo --list    # Non-interactive list of all snapshots
ldm restore demo --index 1 # Restore to index 1
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

### `hydrate` (Local Cloud Backup Hydration)

Creates or restores a project from a local Liferay Cloud backup layout (`database.gz` and `volume.tgz`). This command reuses the high-performance seeding and restoration capabilities of `cloud-fetch` but targets local backup archives instead of downloading them from LCP.

```bash
ldm hydrate /path/to/backup/folder [project-name]
ldm hydrate /path/to/backup/folder my-project --tag 2026.q1.7-lts --db postgresql
```

### `cloud-fetch` (Fetch Cloud State)

Synchronize an **existing local project** with data, logs, and configuration from Liferay Cloud (LCP). This is used for local debugging and state hydration, not for importing source code.

> [!NOTE]
> **Prerequisite:** You must have the [LCP CLI](https://customer.liferay.com/documentation/cloud/latest/en/reference/command-line-tool.html) installed and authenticated (`lcp login`).

```bash
# 1. Discover available cloud environments
ldm cloud-fetch --list-envs

# 2. Stream remote logs from UAT to your local terminal
ldm cloud-fetch [project] uat liferay --logs

# 3. Pull the latest Cloud backups (DB/Data) into your local project snapshots
ldm cloud-fetch [project] uat --download

# 4. Sync Cloud environment variables to your local project metadata
ldm cloud-fetch [project] uat --sync-env
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

### `reset`

Surgically clear project data folders. This command requires the project to be stopped.

```bash
ldm reset [project] [target]
```

**Available Targets:**

- **`state`** (Default): Clears the `osgi/state` folder.
- **`search`**: Clears internal Sidecar indices.
- **`db`**: Clears the database (e.g. PostgreSQL or Hypersonic).
- **`global-search`**: Deletes the project's indices from the shared Global Search container.
- **`all`**: Performs all of the above.

**Examples:**

```bash
ldm reset demo state          # Clear OSGi state for 'demo'
ldm reset demo search,db      # Clear local search and DB
ldm reset demo all            # Total project data wipe
```

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

View or set global LDM configuration settings (stored in `~/.ldmrc`).

```bash
ldm config                  # View all global settings
ldm config features "dev,beta,LPS-122920" # Set global default feature flags
ldm config key value        # Set a global preference
ldm config key --remove     # Remove a preference
```

---

## Configuration Files

- **`logging.json`**: Managed via `log-level` command.
- **`common/`**: Files here (configs, XML licenses, LPKG files) are synced to all project stacks.
- **`services/`**: Place standalone `Dockerfile` directories here for orchestration.

### Shared Configuration (`LDM_COMMON_DIR`)

By default, LDM uses a `common/` directory located in the project's parent, the current working directory, or `~/.ldm/common/` to store shared configurations and licenses.

If you need to use a specific, shared configuration across multiple independent project directories or CI pipelines, you can override this by setting the `LDM_COMMON_DIR` environment variable:

```bash
# Example: Point LDM to a shared organization config folder
export LDM_COMMON_DIR="/path/to/shared/organization/common"
ldm run my-project
```

### Environment Variable Forwarding

LDM automatically forwards specific host environment variables into your project containers using a prefix-based logic.

#### 1. Global Prefix Stripping (`LDM_`)

Any host variable starting with `LDM_` is forwarded to **all** containers in the stack with the prefix removed. This is the recommended way to inject global configurations.

- **Host**: `export LDM_COMPANY_ID=123`
- **Container**: `COMPANY_ID=123`

#### 2. Automatic Passthrough (AI & Liferay Cloud)

To ease CI integration, LDM automatically forwards variables from known providers as-is (preserving the prefix) to all containers:

- **Liferay Cloud**: `LXC_`, `COM_LIFERAY_LXC_`
- **AI Providers**: `OPENAI_`, `GEMINI_`, `ANTHROPIC_`, `MISTRAL_`

#### 3. Custom Passthrough Prefixes

You can extend the automatic passthrough list by setting `LDM_FORWARD_PREFIXES` on your host:

- **Host**: `export LDM_FORWARD_PREFIXES="AWS_,STRIPE_"`
- **Result**: Any variable starting with `AWS_` or `STRIPE_` will be forwarded to all containers.

#### 4. Service-Specific Targeting

You can target a specific service (including Client Extensions) by prefixing the variable with the **Service ID** (uppercased, with dashes replaced by underscores):

- **Service ID**: `my-custom-extension`
- **Host Variable**: `export MY_CUSTOM_EXTENSION_DEBUG=true`
- **Container** (`my-custom-extension` only): `DEBUG=true`

---

## Prerequisites

- **Docker Engine**: Docker Desktop, Colima, or native WSL2.
- **Docker Compose**: **v2 (Plugin)** is mandatory. Legacy v1 standalone is not supported.
- **Resources**: Recommended **4 CPUs and 8GB RAM** allocated to Docker.
  - *Note*: `ldm doctor` expects these minimums. If you allocate exactly 8GB, Docker may report ~7.7GB due to system overhead; the tool accounts for this by allowing a 7.5GB threshold.
- **Python**: 3.10+ (if not using binary)
- **mkcert**: (Optional) For automated local SSL.

### Increasing Resources in Colima

If `ldm doctor` reports insufficient resources in Colima, you can increase them with these commands:

```bash
colima stop
colima start --cpu 4 --memory 8
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
- **Tag Prefix Search**: When running `ldm run` without a tag, you can enter a prefix (e.g., `2025.q4`) to filter the available Liferay versions from Docker Hub. Alternatively, use the `--tag-prefix` switch to bypass the prompt entirely.
- **Tag Discovery**: If no prefix or release type is provided, the tool fetches the latest available tags from Docker Hub.
- **Automated Latest Tags**: In automated environments, use `--tag-latest` (with `ldm init` or `ldm run`) to automatically discover and use the most recent stable tag, bypassing all interactive prompts.
- **Omni-Admin Captcha**: During testing or CI workflows, you can use the `--no-captcha` flag during initialization or run to automatically disable Liferay's mandatory Omni-Admin CAPTCHA checks. This is strictly opt-in and reversible; running without the flag will automatically re-enable CAPTCHA enforcement.
- **Fast Login**: Use the `--fast-login` flag to automatically bypass typical post-startup prompts, such as the Terms of Use acceptance and the initial password reset screen. *Note: The password policy bypass component does not fully function if you explicitly use the embedded Hypersonic database (`--db hypersonic`). It works perfectly with the default PostgreSQL database.*
- **Filesystem Resilience**: If your project is stored on an external SSD (common on macOS `/Volumes/` paths), Liferay's OSGi container can fail due to bind-mount locking limitations. LDM **automatically detects** these paths and uses a high-performance internal volume for the OSGi state to prevent these errors. You can also force this behavior using the `--internal-state` flag.

---

## 🛠️ Development & Building

If you want to contribute to LDM or test your changes locally, follow these steps.

### 1. Run from Source (Live Development)

The easiest way to develop is to install LDM in "editable" mode. This allows your changes to the `ldm_core` package to take effect immediately.

```bash
# Clone the repo
git clone https://github.com/peterrichards-lr/liferay-docker-manager.git
cd liferay-docker-manager

# Install in editable mode
pip install -e .

# Run the entry point
python3 liferay_docker.py --help
```

### 2. Building Standalone Binaries

You can build a single-file executable to test how the tool behaves as a binary.

#### **Option A: Shiv (Official CI Method)**

Used for macOS and Linux. Fast and lightweight, but requires `python3` to be present on the host.

```bash
# Build only
./scripts/package-shiv.sh

# Build and install to /usr/local/bin/ldm (requires sudo)
./scripts/package-shiv.sh --install
```

#### **Option B: PyInstaller (True Standalone)**

Bundles the Python interpreter inside the file. Works even on machines without Python installed.

```bash
# Build only
./scripts/package-pyinstaller.sh

# Build and install to /usr/local/bin/ldm (requires sudo)
./scripts/package-pyinstaller.sh --install
```

The resulting binary will be found in the `dist/` folder (for PyInstaller) or the root (for Shiv).

## License

MIT © Peter Richards
