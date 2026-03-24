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

## Quick Start

```bash
# Import assets from an existing Liferay Workspace
./ldm import /path/to/workspace --project my-project

# Run a project (automatically detects folder if you are inside it)
./ldm run

# View logs for a specific project
./ldm logs my-project

# View logs for just the 'liferay' service in a project
./ldm logs my-project liferay
```

---

## Command Reference

### `list`

Display a tabulated overview of all initialized LDM sandbox environments.

```bash
./ldm list
```

### `import`

Scaffold a new project from an existing workspace. Supports standard folders, Liferay Cloud, and source archives (`.zip`, `.tgz`).

**Examples:**

```bash
# Simple import from a local workspace folder
./ldm import ~/repos/my-workspace my-project

# Full Stack Restore: Build modules and restore a Cloud backup (DB + Data)
./ldm import ./workspace my-cloud-restore --build --backup-dir ./backups/uat-state

# Import directly from a source archive
./ldm import ./workspace-main.zip
```

### `run`

Initialize and start a project stack.

```bash
# Run with a specific tag and virtual hostname
./ldm run --tag 2025.q1.0 --host-name demo.local

# Interactive run (will prompt for version and project name)
./ldm run
```

### `logs`

View real-time logs. Supports filtering by project and specific service.

```bash
./ldm logs [project] [service]

# Examples:
./ldm logs                  # All logs for current project
./ldm logs demo             # All logs for 'demo' project
./ldm logs demo liferay     # Only Liferay logs for 'demo'
./ldm logs demo my-extension # Only logs for a specific client extension
```

### `restart`

Restart a project or a specific service.

```bash
./ldm restart [project] [service]

# Examples:
./ldm restart               # Full stack restart (graceful stop + run)
./ldm restart demo liferay  # Surgical restart of just the Liferay container
```

### `deploy`

Hot-deploy built artifacts or rebuild extension images.

```bash
./ldm deploy [project] [service] --rebuild

# Examples:
./ldm deploy                # Sync all artifacts and refresh stack
./ldm deploy demo my-ext --rebuild  # Rebuild and restart one extension
```

### `snapshot` & `restore`

Backup and recover project states, including files, DB, and search indices.

**Examples:**

```bash
# Create a named snapshot
./ldm snapshot demo --name "post-setup-gold-standard"

# List snapshots for a project
./ldm snapshots demo

# Restore to a specific snapshot index
./ldm restore demo --index 1
```

### `shell` & `gogo`

Jump into a container shell or connect to the OSGi Gogo console.

```bash
# Enter bash in the Liferay container
./ldm shell demo

# Enter bash in an extension container
./ldm shell demo my-node-service

# Connect to the Gogo shell (if port was exposed during run)
./ldm gogo demo
```

### `env`

Manage persistent environment variables in project metadata.

```bash
./ldm env [project] KEY=VALUE
./ldm env [project] --remove KEY
./ldm env                   # Interactive manager (view and edit all)
```

### `log-level`

Manage Liferay internal logging levels (Log4j2) without restarts.

```bash
./ldm log-level [project] portal com.liferay.portal DEBUG
./ldm log-level --list
```

### `doctor`

Verify host environment health, Docker resources (CPUs/Memory), and project dependencies.

```bash
./ldm doctor
```

### `prune`

Identify and remove orphaned Docker containers and temporary files from projects that have been deleted.

```bash
./ldm prune
```

---

## Configuration Files

- **`logging.json`**: Managed via `log-level` command.
- **`common/`**: Files here (configs, XML licenses, LPKG files) are synced to all project stacks.
- **`services/`**: Place standalone `Dockerfile` directories here for orchestration.

---

## Interactive Mode Tips

- **Project Detection**: `ldm` prioritizes positional arguments, then CLI flags, then your current directory. If no project is found, it will show you a list to choose from.
- **Quitting**: Type `q` at any prompt to abort.

---

## Prerequisites

- **Docker Engine**: Docker Desktop, Colima, or native WSL2.
- **Resources**: Recommended 4 CPUs and 8GB RAM allocated to Docker.
- **Python**: 3.10+
- **mkcert**: (Optional) For automated local SSL.

---

## License

MIT © Peter Richards
