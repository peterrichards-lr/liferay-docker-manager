# Starting a Fresh Vanilla Liferay

![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue)

This guide explains how to start a completely fresh, vanilla Liferay instance using Liferay Docker Manager (LDM), detailing the configurations, database profiles, volume strategies, and CLI options.

---

## 1. What is Vanilla Start?

By default, when you start a new project with LDM, it attempts to download and extract a **pre-warmed database seed** from GitHub Releases. Pre-warmed seeds contain pre-initialized database schemas and cached OSGi configurations, reducing the first boot time of Liferay to under a minute.

A **Vanilla Start** bypasses the pre-warmed seeds entirely:

- It starts with a completely blank database (PostgreSQL, MySQL, or Hypersonic).
- Liferay will perform its own full schema creation and startup initialization script executions on the very first boot.
- This is useful when you want to start a truly pristine instance, verify startup scripts, or build a clean baseline without LDM's pre-packaged configurations.

> [!WARNING]
> Because vanilla starts require Liferay to initialize its database from scratch, the first boot will take significantly longer (typically 2 to 5 minutes depending on your hardware and database choice). Subsequent boots will be fast since the database schema will already be initialized.

---

## 2. Starting a Vanilla Instance

To start a vanilla instance, use the `--vanilla` flag ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue) (which acts as a developer alias to `--no-seed`) during project initialization or run:

```bash
# Start a fresh vanilla Liferay using the default PostgreSQL database
ldm run my-vanilla-project --vanilla

# Start a vanilla instance of a specific Liferay version
ldm run my-vanilla-project --vanilla -t 2025.q1.0
```

You can also scaffold the project structure without immediately booting the containers:

```bash
ldm init my-vanilla-project --vanilla -t 2025.q1.0
```

---

## 3. Configuration Options

### Liferay Version

Specify the exact Liferay version you want to target using the `-t` or `--tag` flag. If omitted, LDM will prompt you to choose or automatically select the latest LTS release.

```bash
ldm run my-vanilla-project --vanilla -t 2025.q1.0
```

### Database Options

LDM supports multiple database backends via the `--db` argument:

- `postgresql` (Default): Boots a dedicated PostgreSQL container in your project stack.
- `mysql`: Boots a dedicated MySQL container in your project stack.
- `hypersonic`: Uses Liferay's built-in HSQL database. Rapid startup, but not suitable for production or complex setups.
- `external`: Configures Liferay to connect to an external database (prompts you for JDBC URL and credentials).

```bash
# Start a vanilla Liferay with a fresh MySQL database
ldm run my-vanilla-project --vanilla --db mysql
```

### Database Modes

You can configure database resource pooling configurations globally or per project:

- **Isolated** (Default): A dedicated database container is spawned for your project.
- **Shared**: Shares a global database container (`liferay-db-global`), creating a namespaced schema for the project to save system memory.

```bash
ldm run my-vanilla-project --vanilla --database-mode shared
```

### Search Modes

Specify how Elasticsearch is deployed for search indexing:

- **Shared** (Default): Uses the global search container (`liferay-search-global`) to reduce resource overhead.
- **Sidecar**: Runs the embedded Elasticsearch search server directly inside the Liferay container.

```bash
ldm run my-vanilla-project --vanilla --search-mode sidecar
```

---

## 4. Volume Strategy & File Mounts

To comply with the LDM Hybrid Volume Strategy, vanilla projects separate storage types:

- **Named Docker Volumes**: Used for directories requiring POSIX filesystem locks (e.g. `/opt/liferay/data`, `/opt/liferay/osgi/state`) to prevent locks or latency issues on macOS and Windows hosts.
- **Host Bind-Mounts**: Folders facilitating developer interaction and hot-reloads (like `deploy/`, `files/`, and `modules/`) are mapped directly to your local workspace directory.

When starting a project, LDM creates a project workspace directory under `~/.ldm/projects/my-vanilla-project/` containing:

- `files/`: Place customizations or hot-reload configurations here.
- `files/portal-ext.properties`: Pre-configured portal overrides cascade.
- `deploy/`: Drop client extensions or OSGi bundles to deploy them to Liferay.

---

## 5. Seeding Prompts, Aliases, and Headless Execution

### Interactive Seeding & Cache Misses

By default, if you run or initialize a project without specifying a seeding flag, LDM checks if a pre-warmed database seed for the target version exists in the local cache (`~/.ldm/seeds/`).
If the seed is not cached, LDM will prompt you interactively:

```text
Project seed not found in cache. Download pre-warmed {tag} seed? [Y/n]
```

If you decline (`n`), or if you explicitly bypass seeding, LDM starts a fresh vanilla instance.

### Semantic Aliases: `--vanilla` vs. `--no-seed`

LDM provides two flags to skip pre-warmed database seeding:

1. **`--no-seed`** (Technical Mechanism): Bypasses downloading or restoring any pre-warmed database schema.
2. **`--vanilla`** (Expected Outcome): A user-friendly alias for `--no-seed` that explicitly signifies you want to boot a completely pristine, empty Liferay instance.

Both flags have identical behavior and can be used interchangeably.

### Headless and CI/CD Guidelines

In automated, headless, or non-interactive environments (such as GitHub Actions, GitLab CI, or cron-triggered orchestration scripts), any interactive prompt will block execution indefinitely or cause build timeouts.

To guarantee successful headless execution:

- **To use pre-warmed database seeds**: Pass the `-y`, `--yes`, or `--non-interactive` flag to automatically approve seed downloads on a cache miss.

  ```bash
  ldm run my-project -y
  ```

- **To use vanilla setups**: Pass `--vanilla` or `--no-seed` to skip seeding prompts entirely.

  ```bash
  ldm run my-project --vanilla
  ```

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-16* | *Last Reviewed: 2026-07-10*
