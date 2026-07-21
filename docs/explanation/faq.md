# LDM Frequently Asked Questions & Walkthroughs

This document collects common technical questions, best practices, and walkthroughs for Liferay Docker Manager (LDM) development and demonstration.

---

## 1. CLI Commands & General Diagnostics

### Q: I hit an attribute error when running `ldm fix-hosts` (`'LiferayManager' object has no attribute 'manager'`). How do I resolve it?

This was a bug in version `2.11.68` (and earlier) where the `BaseHandler.cmd_fix_hosts` mixin method tried to call `self.manager.cmd_doctor(...)` instead of `self.cmd_doctor(...)`. Since `BaseHandler` is inherited directly by `LiferayManager`, the `self` context is already the manager instance, and `self.manager` is `None` (reserved for type stub analysis).

**Status**: Fixed in master (PR #236). Calling `ldm fix-hosts` delegates directly and safely to `self.cmd_doctor(fix_hosts=True)`.

---

## 2. Quick Environment Swapping & Resource Optimization

### Q: What is the recommended workflow for switching between multiple demo environments quickly — should each demo be a separate `ldm init` project, or is there a faster way to swap DXP versions on the same project?

**Recommendation**: Initialize **separate LDM projects** for each demo environment.

#### Why?

Swapping DXP versions (e.g., DXP 7.4 vs. DXP 2026) on the same database will trigger schema drift or database locking errors on startup because Liferay applies irreversible database schema changes dynamically during boot.

#### Walkthrough: Rapid Swapping Workflow

1. Initialize separate environments once:

   ```bash
   ldm init demo-2026 --tag 2026.q1.10-lts --db postgresql
   ```

2. Switch between them cleanly:

   ```bash
   # Stop whatever is currently running
   ldm stop
   
   # Spin up the target demo environment
   ldm run demo-74
   ```

3. **Shared Database Mode**: If you run many demos concurrently, configure LDM to run in **Shared Database mode** to place projects on a single global database container (`liferay-db-global`), resolving host memory issues.

### Q: How can I run multiple LDM demo environments side-by-side without exhausting my local machine's CPU, RAM, and Disk space?

Running multiple Liferay DXP instances locally can quickly overwhelm standard laptop resources. LDM includes several built-in features specifically designed to minimize resource consumption during parallel multi-demo execution:

#### 1. Shared Database Mode (`use_shared_db`)

By default, each LDM project spins up its own isolated database container (e.g., PostgreSQL or MySQL). In Shared Database Mode, LDM skips launching project-specific databases and routes all projects to a single global database container (`liferay-db-global`), namespaced as `lportal_<project_id>`:

* **Resource Saved**: Saves **~500MB to 1GB of RAM** per running project by eliminating redundant database container overhead.
* **Usage**: Configure it globally or toggle it for specific projects:

  ```bash
  ldm config database-mode shared --global
  ```

#### 2. Local Database Connection Pool Throttling

LDM automatically configures Liferay's database pool settings to be lean by default:

* **Limits**: Constrains pools to `jdbc.default.maxActive=15`, `jdbc.default.minIdle=2`, and `jdbc.default.maxIdle=5` (compared to Liferay's default of 100+ connections).
* **Resource Saved**: Prevents the database container from allocating hundreds of active connections/threads, drastically reducing CPU context-switching and RAM overhead.

#### 3. Elasticsearch Scheduling and Memory Limits

The global search container (`liferay-search-global`) is configured with strict footprint gates:

* **Heap limits**: Restricts the Elasticsearch Java heap space to **512MB** by default (`-Xms512m -Xmx512m`).
* **CPU scheduling**: Forces `-e "processors=1"` configuration.
* **Resource Saved**: Prevents Elasticsearch from aggressively consuming multi-gigabyte RAM allocations and pinning multiple CPU cores during reindexing.

#### 4. Smart Cache Hydration (Fast volume restore)

When hydrating or resetting a project, LDM uses hash-based change detection (saved in `.ldm_volume.sha256`) to check if the `volume.tgz` archive has changed:

* **Resource Saved**: Bypasses redundant write extractions of large documents and media assets. This avoids high Disk I/O bottlenecks and high CPU compression loads, spinning up stacks in seconds.

#### 5. Docker Log Driver Rotations

Every scaffolded container is configured with size and rotation limits:

* **Limits**: Restricts logs to `max-size: 10m` and `max-file: 3` rotations.
* **Resource Saved**: Prevents Docker logging directories from silently expanding and consuming hundreds of gigabytes of disk space over time.

---

## 3. Pre-Baking Custom Data & Workspace Seeding

### Q: Is there a way to pre-bake custom data (like specific Fragments, Objects, or Client Extensions) into a seed pack, so demos start with my customizations instead of generic samples?

Yes. Depending on your needs, you can distribute this state using two workflows:

#### Option A: Portable Workspace Packages (`.ldmp`)

You can export any customized environment (database dump + document library assets + configurations) into a single portable package.

1. Spin up a clean sandbox project:

   ```bash
   ldm run custom-demo-template
   ```

2. Log in and configure your fragments, objects, pages, and client extensions.

3. Capture a snapshot:

   ```bash
   ldm snapshot create custom-demo-template --name "Baked Custom Demo Data"
   ```

4. Package the workspace:

   ```bash
   ldm package custom-demo-template
   ```

This produces `custom-demo-template.ldmp`. Your colleagues can import and spin up this exact custom environment instantly by running:

```bash
ldm import custom-demo-template.ldmp
```

#### Option B: Mounting Custom Assets (Auto-Deploy)

To seed static code/modules without capturing database dumps, use the LDM staging folder:

* Place backend OSGi bundles (`.jar` files) in `[project]/files/osgi/modules/`
* Place configurations (`.config` files) in `[project]/files/osgi/configs/`
* Place Client Extension ZIP files in `[project]/client-extensions/`

LDM automatically mounts and hot-deploys these assets during stack start (`ldm run`).

---

## 4. Pre-Warmed Seed Packs Lifecycle

### Q: How often are the pre-warmed seed packs updated after a new LTS release drops?

* **Weekly Automation**: The GitHub Actions workflow `generate-seeded-states.yml` automatically rebuilds and uploads seed archives **every Sunday** to the `seeded-states` release tag.
* **Target Support**: The workflow queries upstream releases to cover the **latest 4 Quarter releases** and **2 active LTS lines**.
* **Manual Rebuilds**: Maintainers can trigger the workflow manually (`workflow_dispatch` on GitHub Actions) if a new LTS patch is released mid-week to make it immediately available for offline seeding.

---

## 5. Client Extension and Theme Packaging

### Q: Is there native support planned for client extensions or custom themes in the snapshot/package workflow (`ldm package`)?

**It is fully supported natively.**

* **Metadata manifests** automatically record the resources included inside the snapshot (e.g. `includes_client_extensions`, `includes_osgi_modules`).
* **Source & Build Packaging**: When running `ldm package`, LDM packs both the database/DL snapshot and the source workspace (including custom theme folders and client extensions).
* **Hydration auto-builds**: Upon `ldm import`, LDM restores the database, moves client extension zip files to `client-extensions/`, and builds/syncs code components so the theme and extensions load seamlessly on startup.

---

## 6. Remote Collaboration & Sharing (Tunnels)

### Q: How can I share my local running DXP demo instance with a remote client or colleague? What is the difference between `lfr-tunnel`, `ngrok`, and other tunnel options?

To facilitate collaboration, LDM includes a dedicated `ldm share` command which generates public-facing secure HTTPS URLs pointing to your local running instance.

#### Exposing your project

Run the sharing command:

```bash
ldm share [project_id] --provider lfr-tunnel --subdomain my-awesome-demo
```

This will output a public HTTPS URL (e.g., `https://my-awesome-demo.lfr.direct`) that anyone on the internet can use to view your demo.

#### Supported Providers in LDM

1. **`lfr-tunnel` (Native Liferay Tunnel - Recommended)**:

   * **How it works**: Runs the custom Liferay `lfr-tunnel` CLI binary directly on your host machine.
   * **Security**: Resolves the tunnel token securely using the local token config (`LFT_CLIENT_TOKEN` or system keystore).
   * **Custom Path**: LDM resolves custom paths via `LDM_LFR_TUNNEL_BIN` or automatically installs the tunnel binary if run with `--auto-install-lfr-tunnel`.

2. **`lfr-tunnel-docker` (Docker Sidecar)**:

   * **How it works**: Launches the tunnel client inside an isolated Docker sidecar container bundled in your project network stack.
   * **Benefit**: No binary installation is required on your host system, making it completely portable.

3. **`ngrok`**:

   * **How it works**: Launches an `ngrok` tunnel mapping your local port.
   * **Benefit**: Highly standard and stable tunnel provider; useful if you already have a paid ngrok subscription/token configured on your machine.

#### What about Cloudflare?

While Cloudflare (`cloudflared`) is not currently a built-in choice in the LDM CLI `--provider` argument, you can easily use it alongside LDM. Simply launch the cloudflared daemon manually on your terminal:

```bash
cloudflared tunnel --url http://localhost:8080
```

This maps your local Liferay HTTP port (`8080`) to a free, secure Cloudflare tunnel URL.

---

## 7. Version Upgrades, Rollbacks, and Database Lifecycle

### Q: What is the difference between an LDM snapshot restore after a failed upgrade attempt versus a standard database/tag downgrade?

**They are completely different operations with different safety guardrails:**

* **Standard Downgrade**: Attempting to boot an existing database containing schema updates from a newer Liferay version (e.g. `2026.q1.9`) on an older Liferay version (e.g. `2024.q4.1`). Since Liferay databases do not support backward schema migrations, this is blocked by LDM (`Downgrade detected...`) to prevent database corruption.
* **Restore after Failed Upgrade**: Returning to a working state by replacing the corrupted/upgraded database entirely with a clean snapshot taken prior to the upgrade. During `ldm restore`, LDM wipes the database container/volume, restores the Version A SQL dump, updates the Liferay version metadata back to Version A, and regenerates `docker-compose.yml` to boot with the previous Liferay version tag. This is fully supported and safe.

### Q: How can we tell if a Liferay version database upgrade has completed successfully? When is the "point of no return" for rollbacks?

* **Determining Success**: Database upgrades run asynchronously inside the Liferay container after boot. LDM is an orchestrator and cannot instantly verify success when starting the stack. You must monitor the logs (`ldm logs -f`) to verify that the upgrade tool completed without critical SQL schema errors, and check that the portal container state transitions to **Healthy** (`Liferay is ready`).
* **Point of No Return**: A rollback using the pre-upgrade snapshot is safe **only immediately after boot** during developer validation. Once the environment is released to general users and new production data (content, users, documents) is written to the upgraded database, rolling back to the snapshot will **wipe all business data created since the upgrade started**. At this stage, rollbacks are no longer recommended without manual database delta migrations.

### Q: Why are upgrade prompts (such as "Upgrade detected...") sometimes skipped when upgrading a Liferay version?

* **`Created` Project Status (First Run)**: If a project was newly initialized or imported but has never run successfully on the machine, its status is `Created` and its `last_run_liferay_version` property is empty. LDM assumes this is a fresh start, not an upgrade, and skips the database backup/auto-upgrade prompts because there is no existing database to back up or upgrade.
* **Direct Database Imports**: If you import a database dump from an older Liferay version directly into a newly created project, LDM cannot inspect the SQL tables to know what schema version is inside. To run Liferay's auto-upgrade tool on boot for such imports, you must pass the `--upgrade-db` flag explicitly:

  ```bash
  ldm run [project_id] --upgrade-db
  ```

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-21* | *Last Reviewed: 2026-07-02*
