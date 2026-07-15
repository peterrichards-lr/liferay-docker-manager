# Data Management

## 🌱 Seeding (Instant Boot)

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

## 🔗 External Database Connection

If you want to use an external database (such as a shared development DB, an RDS instance, or a standalone local MySQL/PostgreSQL server) instead of generating an isolated database container inside your project's stack, you can use the `--db external` flag during initialization.

```bash
ldm init my-project --db external
```

### Interactive DB Wizard

When this flag is detected, LDM runs an interactive wizard that prompts you for:

1. **Database Type** (PostgreSQL, MySQL, Oracle, SQL Server, etc.)
2. **JDBC Host** (e.g. `192.168.1.50` or `db.internal.network`)
3. **JDBC Port** (Defaults based on type: 5432, 3306, 1521, 1433)
4. **Database Name** (e.g., `lportal` or your custom schema)
5. **Database Username & Password**

### What Happens Under the Hood?

- LDM formats your answers into standard Liferay JDBC properties (e.g. `jdbc.default.url`, `jdbc.default.driverClassName`) and securely appends them directly into your project's `portal-ext.properties`.
- It completely excludes the `db` service block from the generated `docker-compose.yml`.
- Liferay boots up normally, but opens a connection out to your specified external database instead of looking for a local container on the Docker bridge network.

> [!WARNING]
> Since the database lives outside of LDM's control, features like automatic **Seeding**, `reset db`, and complete `snapshot` backups will not capture the state of your external database.

---

## `snapshot` & `restore`

Backup and recover project states, including files, DB, and search indices.

**Examples:**

```bash
# Create a named snapshot
ldm snapshot demo --name "post-setup-gold-standard"

# Delete a specific snapshot by index or name
ldm snapshot demo --delete 1
ldm snapshot demo --delete "post-setup-gold-standard"

# Bulk management/pruning
ldm snapshot demo --keep-last 5   # Delete all but the 5 most recent snapshots
ldm snapshot demo --older-than 30 # Delete all snapshots older than 30 days

# List snapshots for a project
ldm restore demo --list    # Non-interactive list of all snapshots
ldm restore demo --index 1 # Restore to index 1
ldm restore demo --name "post-setup-gold-standard" # Restore by name
```

## `package` (Portable Package Export)

Bundles a project snapshot (code elements, database backup, document library, and Elasticsearch indices) into a single portable `.ldmp` package (tarball) alongside a `.ldmp.sha256` checksum file. This package is ideal for sharing local environments with other developers or releasing template stacks via GitHub Releases.

```bash
# Create a fresh snapshot and package the environment
ldm package

# Package using the latest existing snapshot (skips snapshot generation step)
ldm package --use-latest

# Specify a custom directory output path and bind a GitHub repository identifier
ldm package my-project --output /tmp/packages --repo my-owner/my-repo
```

### ⚠️ CI/CD Release Pipelines vs. Local DB Packaging

When packaging an `.ldmp` release using automated CI/CD pipelines (e.g. GitHub Actions), it is important to note that the database containers are typically **offline/not running** in the headless CI environment.

If your repository contains custom build/packaging hooks that query active Docker containers (for example, checking for running databases to export schemas), the resulting `.ldmp` package will be generated with a vanilla or blank database, resulting in a default welcome site when other developers import it.

#### Case Study: Liferay AI Commerce Accelerator (AICA)

- **The Issue**: AICA's packaging script (`scripts/package-ldmp.sh`) checks for the running database container using `docker ps | grep -q "aica-db"`.
- **Headless CI Failure**: In GitHub Actions (`release.yml`), the database container `aica-db` is not running. The packaging hook falls back to generating a blank `database.sql` and an empty `files.tar.gz`. The resulting `.ldmp` package uploaded to the GitHub Release is empty.
- **The Solution**:

  1. Build the package **locally on your host machine** where your active database container is running:

     ```bash
     ldm package
     ```

  2. Manually upload the populated `.ldmp` package and its `.ldmp.sha256` checksum directly to your GitHub Release assets, replacing the empty files created by the headless CI builder.

## `hydrate` (Local Cloud Backup Hydration)

Creates or restores a project from a local Liferay Cloud backup layout (`database.gz` and `volume.tgz`).

### Database Handling

LDM automatically attempts to detect the database type (MySQL or PostgreSQL) by analyzing the `database.gz` dump header.

- **Auto-Detection**: If the type is successfully detected, LDM will use it automatically.
- **Validation**: If you specify `--db`, LDM verifies it matches the backup. A mismatch will cause the command to exit.
- **Ambiguity**:
  - In **interactive mode**, if detection fails, LDM prompts you to select a type (defaulting to `postgresql`).
  - In **non-interactive mode**, if detection fails and no `--db` is provided, LDM exits with an error.

```bash
# LDM will auto-detect the DB type from the backup
ldm hydrate /path/to/backup/folder [project-name]

# Manually specify or override (validated against the backup)
ldm hydrate /path/to/backup/folder my-project --tag 2024.q1.3 --db postgresql
```

## `cloud-fetch` (Fetch Cloud State)

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

## `reset` and `re-seed`

Surgically clear project data folders or completely restore a project to its original vanilla state. These commands require the project to be stopped.

```bash
ldm reset [project] [target]      # Clear specific data (state|db|search|all)
ldm re-seed [project]             # Wipe ALL data and re-apply vanilla seed
```

**Available Targets (for `reset`):**

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
ldm re-seed demo              # Total project reset to Day Zero (Seeded)
```

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-15* | *Last Reviewed: 2026-07-02*
