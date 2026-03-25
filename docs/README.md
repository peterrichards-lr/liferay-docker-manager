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

## Why use ldm?

- **Multi-Instance Session Isolation**: Run multiple demos side-by-side on the same machine without session cross-talk. `ldm` automatically manages unique session cookie names and virtual hostnames.
- **Orchestrated Search Snapshots**: Save and restore the exact state of a demonstration, including the database, files, and **Elasticsearch 8.x index state**.
- **Service-Specific Lifecycle**: Manage individual components within a project surgically. Restart or view logs for a single extension without affecting the rest of the stack.
- **Client Extension Lifecycle**: Automatically detects and builds Server-Side Client Extensions (SSCE). Subdomains are automatically generated, and traffic is routed based on `LCP.json`.
- **Zero-Config SSL**: Automated HTTPS using `mkcert` and a global Traefik proxy. Works on Docker Desktop, **Colima**, and **WSL2**.
- **Smart Discovery**: Automatically detects projects from your current directory or provides an interactive selector if no project is specified.

---

## Documentation

- [Architecture Overview](LDM_ARCHITECTURE.md)
- [Future Roadmap](roadmap.md)

---

## Installation

### 1. Standalone Binary (Recommended)

The standalone binary is a single-file executable that includes all dependencies.

**macOS / Linux / WSL2:**

Download the latest `ldm` directly using your terminal:

```bash
# Using curl
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-standalone -o /usr/local/bin/ldm

# OR using wget
sudo wget https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-standalone -O /usr/local/bin/ldm

# Make it executable
sudo chmod +x /usr/local/bin/ldm

# Verify
ldm --version
```

> [!TIP]
> **WSL2 Users:** Use the Linux binary within your WSL terminal. Ensure your Docker Desktop is configured to "Use the WSL 2 based engine" and that integration is enabled for your specific distribution. LDM will automatically detect the Windows-side browser when launching URLs.

**Windows:**

Open PowerShell as an Administrator and run:

```powershell
# Create a bin folder if it doesn't exist
New-Item -ItemType Directory -Force -Path "$HOME\bin"

# Download the executable
Invoke-WebRequest -Uri "https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-standalone.exe" -OutFile "$HOME\bin\ldm.exe"

# Add to your User PATH (one-time setup)
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$HOME\bin", "User")

# Verify (in a new terminal window)
ldm --version
```

### 2. Manual Installation (Development)

Clone this repository and use the provided wrapper script for your platform. The wrapper will automatically set up a local Python virtual environment and install the required dependencies on its first run.

**macOS / Linux / WSL2:**

```bash
git clone https://github.com/peterrichards-lr/liferay-docker-manager.git
cd liferay-docker-manager
./ldm --help
```

**Windows:**

```cmd
git clone https://github.com/peterrichards-lr/liferay-docker-manager.git
cd liferay-docker-manager
ldm.bat --help
```

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

# Monitor an existing project (manually)
ldm monitor /path/to/workspace
```

---

## Command Reference

### `list`

Display a tabulated overview of all initialized LDM sandbox environments.

```bash
ldm list
```

### `run`

Initialize and start a project stack.

```bash
# Run with a specific tag and virtual hostname
ldm run --tag 2024.q4.0 --host-name demo.local

# Initialize with "Confidence Booster" samples
ldm run demo --samples

# Interactive run (will prompt for version and project name)
ldm run
```

### `init-from` (Live Link)

Initialize a project from a source workspace and establish a **persistent link**. This command automatically starts the `monitor` process to sync your code changes in real-time.

```bash
ldm init-from ~/repos/my-workspace my-project
```

### `import` (Static Snapshot)

Scaffold a new project by taking a **one-time snapshot** of an existing workspace. This project is detached from the source; changes to the source workspace will not be synced.

```bash
ldm import ~/repos/my-workspace my-static-project
```

### `monitor`

Continuously monitor a Liferay workspace and automatically sync built artifacts (`.jar`, `.war`, `.zip`) to your running project. (Automatically invoked by `init-from`).

```bash
ldm monitor [path_to_workspace] --delay 2.0
```

### `logs`

View real-time logs. Supports filtering by project and specific service.

```bash
ldm logs [project] [service]

# Examples:
ldm logs                  # All logs for current project
ldm logs demo             # All logs for 'demo' project
ldm logs demo liferay     # Only Liferay logs for 'demo'
ldm logs demo my-extension # Only logs for a specific client extension
```

### `stop`, `restart`, `down`

Manage the lifecycle of a project or a specific service.

```bash
ldm stop [project] [service]      # Stop containers gracefully
ldm restart [project] [service]   # Stop and then start
ldm down [project] [service]      # Remove containers (and optionally -v volumes)

# Examples:
ldm restart               # Full stack restart (graceful stop + run)
ldm restart demo liferay  # Surgical restart of just the Liferay container
ldm down --volumes        # Tear down stack and clear all database/data state
```

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
ldm restore demo          # No index provided = list all
ldm restore demo --index 1 # Restore to index 1
```

### `shell` & `gogo`

Jump into a container shell or connect to the OSGi Gogo console.

```bash
# Enter bash in the Liferay container
ldm shell demo

# Enter bash in an extension container
ldm shell demo my-node-service

# Connect to the Gogo shell (if port was exposed during run)
ldm gogo demo
```

### `env`

Manage persistent environment variables in project metadata.

```bash
ldm env [project] KEY=VALUE
ldm env [project] --remove KEY
ldm env                   # Interactive manager (view and edit all)
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

Verify host environment health, Docker resources (CPUs/Memory), and project dependencies.

```bash
ldm doctor
```

### `prune`, `infra-down`, `clear-cache`

Identify and remove orphaned resources and maintenance.

```bash
ldm prune                 # Remove orphaned containers and temp files
ldm infra-down            # Tear down global proxy and search services
ldm clear-cache           # Clear the Docker tag cache (~/.liferay_docker_cache.json)
```

---

## Configuration Files

- **`logging.json`**: Managed via `log-level` command.
- **`common/`**: Files here (configs, XML licenses, LPKG files) are synced to all project stacks.
- **`services/`**: Place standalone `Dockerfile` directories here for orchestration.

---

## Prerequisites

- **Docker Engine**: Docker Desktop, Colima, or native WSL2.
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

---

## License

MIT © Peter Richards
