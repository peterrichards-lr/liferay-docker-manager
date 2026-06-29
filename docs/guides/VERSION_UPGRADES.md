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
