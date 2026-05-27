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
