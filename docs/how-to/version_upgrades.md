# Liferay Version Upgrades Guide

This guide describes how Liferay Docker Manager (LDM) orchestrates Liferay version upgrades inside your local sandboxes. It outlines the conceptual flow, the automation steps that occur under the hood, and how to verify that your local data remains safe.

---

## Conceptual Overview

When running a local sandbox, developers often need to change the Liferay version (e.g. from `2026.q2.4-lts` to `2026.q2.5-lts`) using the `--tag` parameter. LDM is designed to handle this seamlessly, ensuring that your local workspace files, database schemas, and document library contents persist through the upgrade process.

Unlike manual upgrades, LDM encapsulates data protection and schema upgrade instructions directly into the sandbox startup sequence (`ldm run`).

---

## Under-the-Hood Workflow

Here is the exact lifecycle mapping of what LDM executes during a version upgrade:

| Step | Phase | Action | Purpose / Detail |
| :--- | :--- | :--- | :--- |
| **1** | **Safety Check** | Compare new tag with `last_run_liferay_version` in `.liferay-docker.meta`. | Identifies if this is a version upgrade, downgrade, or no change. |
| **2** | **Pre-Upgrade Backup** | Temporarily starts the DB container (if stopped) and runs `ldm snapshot`. | Takes an automated SQL dump of the database (`Pre-upgrade snapshot to {tag}`) before any upgrade script runs. |
| **3** | **Compose Rebuild** | Regenerates `docker-compose.yml` with the new Liferay image tag. | Embeds the new image version and injects the `LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true` environment variable. |
| **4** | **Container Recreation** | Executes `docker compose up -d`. | Docker Compose automatically destroys the old container, pulls the new image, and boots the new Liferay container. |
| **5** | **Volume Remapping** | Natively mounts persistent named volumes and bind-mount directories. | Reattaches existing document library folders and database volumes to the new Liferay container without data loss. |
| **6** | **Auto-Upgrade Execution** | Liferay boots and runs database schema updates. | Liferay reads the upgrade environment variable, runs schema migrations, and starts the portal. |
| **7** | **Post-Upgrade Reset** | Cleans up temporary environment flags for subsequent runs. | Updates `.liferay-docker.meta` so subsequent starts bypass both the backup prompt and the schema migration, booting normally. |

---

## Data Safety & Persistent Volumes

LDM separates **stateless runtime containers** from **persistent stateful volumes**:

- **Named Docker Volumes**: Docker named volumes (like `liferay-data` and `liferay-state`) exist independently of any individual container. Re-creating a container with a new tag does not destroy these volumes.
- **Host Bind-Mounts**: Directories containing your OSGi modules, deployed client extensions, and properties reside on your host machine and are simply mapped into the new container.

This hybrid volume strategy guarantees that your data is safe and remains remapped to the new container.

---

## Performing an Upgrade

### 1. Interactive Upgrade

To initiate an upgrade, run `ldm run` with the target tag version:

```bash
ldm run --tag 2026.q2.5-lts
```

If LDM detects a version change:

1. **Database Backup Prompt**: It will ask:

   ```text
   Upgrade detected: Liferay version is changing from 2026.q2.4-lts to 2026.q2.5-lts.
   Would you like to take a database backup snapshot before proceeding? (y/n)
   ```

2. **Schema Auto-Upgrade Prompt**: It will warn you and prompt:

   ```text
   New Liferay versions often require a database schema upgrade.
   Do you want to run Liferay's database auto-upgrade tool on startup? (y/n)
   ```

Select `y` (Yes) for both to ensure a clean, backed-up upgrade path.

### 2. Automated Pipeline / Non-Interactive Upgrade

In CI/CD environments or headless scripts where prompts are skipped (using `-y` / `--non-interactive`), you can configure the behavior using CLI flags:

- **Force Backup & Auto-Upgrade**:

  ```bash
  ldm run --tag 2026.q2.5-lts --backup-on-upgrade --upgrade-db --non-interactive
  ```

- **Disable Backup & Auto-Upgrade**:

  ```bash
  ldm run --tag 2026.q2.5-lts --no-backup-on-upgrade --no-upgrade-db --non-interactive
  ```

---

## Downgrade Protection

Downgrading Liferay versions on an existing database will corrupt schemas. By default, LDM blocks downgrades entirely. If you need to force a downgrade (for example, to revert to a backup tag), you must pass the override flag:

```bash
ldm run --tag 2026.q2.4-lts --force-downgrade
```

---

## Post-Upgrade Verification & Validation

Since database schema upgrades run asynchronously inside the Liferay JVM container after startup, LDM cannot instantly verify if the upgrade was successful upon running the `ldm run` command. You must validate the upgrade using the following steps:

1. **Log Inspection**: Stream and monitor the container logs:

   ```bash
   ldm logs -f [project_id]
   ```

   Inspect the log stream to verify that Liferay's upgrade framework finishes without throwing critical SQL schema or data integrity exceptions.
2. **Container Health status**: Run `ldm status` or check the project dashboard to ensure the container status transitions to **Healthy** (e.g., printing `Liferay is ready` in logs).
3. **Sanity Testing**: Access the portal URL, log in as administrator, and perform basic sanity checks (verify that custom client extensions, OSGi modules, pages, and portlets load correctly).

---

## Executing a Rollback / Reversion

If the upgrade fails, or if sanity testing reveals critical runtime incompatibilities, you can roll back the environment to its exact pre-upgrade state.

1. **Restore the Pre-Upgrade Snapshot**:
   Run the restore command:

   ```bash
   ldm restore [project_id] --latest
   ```

   *(Or specify the snapshot name: `ldm restore [project_id] --name "Pre-upgrade snapshot to {tag}"`)*
2. **Automated Version Reversion**:
   During the restore process, LDM will automatically:
   - Wipe the database container and volume.
   - Restore the SQL database dump from the pre-upgrade snapshot.
   - Revert the project metadata Liferay version tag (`tag` and `last_run_liferay_version`) back to the pre-upgrade version.
   - Regenerate `docker-compose.yml` to boot using the previous Liferay image tag.

---

## Business Boundaries & The "Point of No Return"

While LDM allows you to restore the pre-upgrade snapshot at any time, doing so carries business and data loss implications:

- **Safe Rollback Window**: Reverting is safe **only during the immediate verification phase** after boot, before any new business or developer data is created.
- **Point of No Return**: The boundary is reached as soon as the upgraded sandbox is put into active use. Once users begin publishing web content, uploading documents, or configuring permissions on the new version, restoring the snapshot will **permanently delete all business data created since the upgrade started**.
- **Recommendation**: Always perform validation immediately on startup and do not release the sandbox environment to general users until you have confirmed a successful upgrade.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-07* | *Last Reviewed: 2026-07-02*
