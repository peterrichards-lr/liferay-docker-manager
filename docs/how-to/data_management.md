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

If you want to use an external database (such as a shared development DB, an RDS instance, or a standalone local MySQL/PostgreSQL server) instead of generating an isolated database container inside your project's stack, use `--database-mode external` during initialization.

```bash
ldm init my-project --db postgresql --database-mode external
```

`external` is a **mode**, not an engine: it answers *who runs the database*, and the engine is still whatever it is. Naming it with `--db` is what lets LDM resolve the JDBC driver and the per-tag Hibernate dialect for your server.

> [!NOTE]
> **`--db external` remains valid** -- it is the older spelling, kept working deliberately (LDM-#1511). LDM infers the engine from the JDBC URL scheme instead: `jdbc:postgresql://` is PostgreSQL, `jdbc:mysql://` and `jdbc:mariadb://` are MySQL. A URL naming an engine LDM does not support falls back to writing the URL, username and password alone, exactly as before. Projects already carrying `db_type: "external"` in their `meta` are migrated on their next run and keep booting unchanged.

### Interactive DB Wizard

When the mode resolves to `external` and no JDBC URL is recorded yet, LDM runs an interactive wizard that prompts you for:

1. **JDBC URL** (e.g. `jdbc:postgresql://db.internal.network:5432/lportal`)
2. **Database Username**
3. **Database Password**

### What Happens Under the Hood?

- LDM formats your answers into standard Liferay JDBC properties (`jdbc.default.url`, `jdbc.default.username`, `jdbc.default.password`) and securely appends them directly into your project's `portal-ext.properties`. Where the engine is known, `jdbc.default.driverClassName` and `hibernate.dialect` are written too.
- It completely excludes the `db` service block from the generated `docker-compose.yml`, and no `depends_on` refers to it.
- Liferay boots up normally, but opens a connection out to your specified external database instead of looking for a local container on the Docker bridge network.

> [!WARNING]
> Since the database lives outside of LDM's control, features like automatic **Seeding**, `reset db`, `ldm db query` and complete `snapshot` backups do not operate on your external database. LDM refuses those rather than reaching for a container it does not own.

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
*Last Updated: 2026-09-03* | *Last Reviewed: 2026-09-03*
