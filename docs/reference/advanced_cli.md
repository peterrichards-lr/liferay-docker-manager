# Advanced CLI Usage & Flags

This document covers advanced flags and commands intended for CI/CD automation, complex edge-cases, debugging, and extreme performance tuning. For standard workflow commands, see the [CLI Reference](cli/core.md).

## Filesystem & Volumes (macOS / Windows)

These flags control how LDM mounts volumes to bypass filesystem locking limitations on non-Linux hosts.

- **`--internal-state`**: Forces the use of an internal Docker volume for the OSGi state folder (`osgi/state`). LDM enables this automatically if it detects the project is on an external drive (`/Volumes/`). Use this to manually bypass `access_denied_exception` lock errors on slow or external filesystems.
- **`--no-vol-cache`**: Disables the `:cached` mount delegation flag on macOS and Windows. Use this if you are experiencing severe file synchronization delays between the host and the container.

## Initialization & Seeding State

These flags modify how LDM handles the initial startup of a Liferay environment.

- **`--persist-osgi`**: Maps the container's `osgi/state` directory to the host instead of an anonymous Docker volume, allowing bundle state to persist across container restarts. This dramatically reduces subsequent Liferay startup times by bypassing the OSGi bundle resolution phase. *Note: LDM will automatically invalidate and wipe this state if it detects the underlying Liferay image tag has changed to prevent bundle conflicts.*
- **`--no-persist-osgi`**: Explicitly disables OSGi state persistence, forcing a clean OSGi resolution on every start.
- **`--no-seed`**: Completely bypasses the pre-warmed database and OSGi cache. The project will start totally fresh, forcing Liferay to build its schema and resolve all OSGi bundles from scratch.
- **`--vanilla`** ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue): Starts a Liferay with **nothing pre-populated**. It implies `--no-seed`, and additionally refuses to run alongside `--samples` or `--snapshot` (which would restore content), and wipes any host-persisted OSGi state from `--persist-osgi` so no bundles arrive pre-resolved. Use `--no-seed` instead if you only want to skip the pre-warmed seed while still restoring a snapshot — that combination stays legal.
- **`-n`, `--nightly`** ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue): Targets the latest Liferay DXP nightly build (`7.4.13.nightly`) from Docker Hub.
- **`--master`** ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue): Alias for `--nightly`. Targets the latest Liferay DXP master/nightly build.
- **`--pull`** ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue): Forces Docker to pull the latest image layers before running or starting containers.
- **`--no-osgi-seed`**: Bypasses only the OSGi cache seed. Useful if you are testing custom OSGi resolution logic or diagnosing a corrupted `.osgi_state_archive`.
- **`--verify` / `--no-verify`**: Controls whether LDM generates or checks the integrity checksum of snapshots and imports. Defaults to true. Disabling can speed up local imports slightly.
- **`--snapshot`**: Initialize a project directly from an external snapshot folder.
- **`--portal`**: Forces the use of the Liferay Portal (CE) image instead of the default DXP image.
- **`--refresh`**: Forces Docker to pull the latest image layers and refresh cached assets before startup.

## Execution Flow & Networking

These flags alter the standard startup behavior and networking defaults.

- **`--expose`**: Injects an `ngrok` sidecar container into your stack to expose your local Liferay instance to the public internet securely over HTTPS. It requires a free ngrok Auth Token, which LDM will prompt for once and save globally. Perfect for testing webhooks, SaaS integrations, or sharing your local dev environment.
- **`--no-up`**: Scaffolds the project folder and generates configurations, but skips starting the Docker containers. (Similar to `ldm init`).
- **`--no-wait`**: Skips the readiness gating (health checks) after container startup, returning control to the terminal immediately.
- **`--timeout <seconds>`**: Overrides the maximum wait time for health checks (default is 900 seconds).
- **`-f`, `--follow`**: Automatically follows the container logs immediately after a successful startup.
- **`--force-ssl`**: Forces SSL termination via Traefik even if the host is `localhost`.

## CI/CD & Pipeline Automation

These flags are ideal for automated testing pipelines where interactivity is impossible.

- **`--no-captcha`**: Disables Liferay's mandatory Omni-Admin CAPTCHA requirement. Strictly opt-in and easily reversible; running without this flag on a subsequent start will re-enable CAPTCHA.
- **`--fast-login`**: Automatically bypasses typical post-startup prompts (Terms of Use acceptance, initial password reset). Best used with an external database (`--db mysql` or `postgresql`), as password policy bypass has known limitations with the embedded Hypersonic database.
- **`--target <node>` / `--node <node>`**: Target a specific registered compute node (e.g. `--target aws-1`). Overrides the default compute target for the execution context.
- **`--target-env`**: (Used with `link`, `clone`, and `import`). Overrides the environment name metadata.
- **`--build`**: (Used with `link` and `clone`). Forces a full rebuild of any Server-Side Client Extensions found in the source workspace during initialization.
- **`--on-validation-failure`**: Behaviour when a config file (e.g. `fragment-overrides.json`) fails schema validation in non-interactive mode. Choices: `die` (default) or `ignore`.

## JVM & Tomcat Tuning

Advanced options for memory constraints and Java-level debugging.

> How LDM sizes the JVM by default, and how that compares with Liferay's
> published guidance, is recorded in the [JVM & Database Tuning
> reference](tuning.md).

- **`--lean`**: Enables a resource-optimized JVM profile. It caps memory and limits background threading. Highly recommended for laptops with less than 16GB of RAM or CI runners.
- **`--jvm-args="<args>"`**: Pass raw JVM arguments directly to Liferay. Example: `--jvm-args="-Xmx8g -Xms8g"`

  > **This replaces LDM's defaults entirely.** It is not additive: the adaptive
  > heap and metaspace sizing, the platform-specific compiler settings and the
  > reindex scale-up are all discarded, and only what you pass is used. To change
  > one value while keeping the rest, see [LDM-#1446][1446].

### Accepted but inert

These two flags are accepted for compatibility and **have no effect**. Passing
either prints a warning.

- **`--no-jvm-verify`**
- **`--no-tld-skip`**

Earlier versions of this page described them as disabling defaults that LDM
applied. Those defaults never existed: there is no `-Xverify:none` anywhere in
the codebase, and no TLD configuration at all. Both values were read from the
command line, written to project metadata, and never used ([LDM-#1447][1447]).

`-Xverify:none` is also not something to reinstate — it has been deprecated
since JDK 13, and these images run Java 21, where it emits a warning and does
nothing. A genuine TLD scanning skip is still worth having and is tracked in
[LDM-#1446][1446].

[1446]: https://github.com/peterrichards-lr/liferay-docker-manager/issues/1446
[1447]: https://github.com/peterrichards-lr/liferay-docker-manager/issues/1447

## System Tray GUI

The `ldm tray` command launches a native system tray application (menu bar icon) to quickly manage projects, view status, and open the dashboard. The tray is **syntactic sugar** and is entirely optional. If the UI dependencies fail to load, LDM will gracefully fallback to opening the Web Dashboard.

**Dependencies:**

- macOS / Windows: Dependencies (`pystray`, `Pillow`) are bundled in the standalone binaries.
- Linux: Native tray is not officially supported due to Wayland/AppIndicator fragmentation. If you wish to use it, you must manually install AppIndicator libraries (e.g., `sudo dnf install libappindicator-gtk3` on Fedora/RHEL, or `sudo apt install libappindicator3-1` on Ubuntu/Debian).

### Autostart / Launch on Login Configuration

LDM includes built-in commands to automatically configure native system autostart on boot or user login across macOS, Windows, and Linux:

```bash
# Enable launch-on-login for System Tray
ldm tray --autostart

# Disable launch-on-login for System Tray
ldm tray --uninstall-autostart
```

- **macOS**: Provisions a macOS App Bundle (`~/Applications/Liferay Docker Manager.app`) and a user LaunchAgent (`~/Library/LaunchAgents/com.liferay.ldm.plist`) configured to run automatically in the background at login.
- **Windows**: Creates a startup script in `AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Liferay Docker Manager.bat`.
- **Linux**: Creates a FreeDesktop Autostart entry at `~/.config/autostart/ldm.desktop`.

### Running in Background (Detached Terminal)

To launch the System Tray manually in the background without keeping a terminal window open:

```bash
# macOS / Linux (nohup)
nohup ldm tray >/dev/null 2>&1 &

# zsh / bash
ldm tray & disown
```

## Debugging & Diagnostics

- **`--gogo-port <port>`**: Exposes the OSGi Gogo shell on a specific host port. Required if you plan to use `ldm gogo [project]`.
- **`--mount-logs`**: By default, logs remain inside the container. This flag bind-mounts the `tomcat/logs` directory directly to the host for external log aggregator testing.
- **`--delay <seconds>`**: (Used with `monitor` and `link`). Alters the debounce delay for the background file watcher. Useful on slow filesystems.

## Search & Legacy Infrastructure

- **`--sidecar`** ![Deprecated](https://img.shields.io/badge/Deprecated-2025.Q2+-orange): Forces the project to use Liferay's internal Sidecar search process rather than the shared Global Search container. LDM does this automatically if the global container is offline. *(Note: Sidecar uses Elasticsearch 7 and is deprecated in Liferay 2025.Q2+. LDM will automatically ignore this flag and force Shared Search for newer releases).*
- **`--es7`** ![Deprecated](https://img.shields.io/badge/Deprecated-2025.Q2+-orange): Forces the Global Search infrastructure to use Elasticsearch 7 (legacy) instead of the default Elasticsearch 8. Use with `ldm infra-setup --es7`. *(Note: Elasticsearch 7 is deprecated in Liferay 2025.Q2+; future releases require Elasticsearch 8).*

### `--search-mode`

Selects where Liferay's search index lives. Available modes: `sidecar`, `shared` or `remote`.

| Mode | Where Elasticsearch runs | How LDM configures it |
| :--- | :--- | :--- |
| `sidecar` | **Inside the Liferay container** as a child process -- there is no separate container | Liferay's own default; LDM does not need to configure anything |
| `shared` | The global `liferay-search-global` container, shared across projects | LDM writes `com.liferay.portal.search.elasticsearch<N>.configuration.ElasticsearchConfiguration.config` into the project's `osgi/configs/` |
| `remote` | Your own external cluster | **You** provide the `.config` in `osgi/configs/`; LDM leaves it alone |

In `shared` mode each project gets its own index namespace via `indexNamePrefix`:

```text
ldm-<project name, sanitized, lowercased>-
```

Liferay appends the company ID, so the actual indices look like
`ldm-myproject-20101-workflow-metrics-tasks`. The prefix is written lowercase because Liferay lowercases it regardless (`StringUtil.toLowerCase`), and a mixed-case value would not match the indices it creates.

> [!NOTE]
> Search settings are OSGi configuration, **not** portal properties, so they cannot be set with `LIFERAY_*` environment variables. This is why the `.config` file is the mechanism.

### `--database-mode`

Controls whether LDM provisions a dedicated database container for the project or connects it to the Global Shared Database cluster. Available modes: `isolated` or `shared`.

`isolated` supports every engine `--db` accepts -- `postgresql`, `mysql`, `hypersonic` and `external`.

**`shared` supports PostgreSQL and MySQL/MariaDB.** There is one global container per engine, and each is provisioned lazily -- on the first project that needs it:

| `--db` | Global container | Host port | Image |
| :--- | :--- | :--- | :--- |
| `postgresql` (default) | `liferay-db-global` | `5433` | `postgres:<resolved>` |
| `mysql`, `mariadb` | `liferay-db-mysql-global` | `3307` | `mysql:<resolved>` |

`hypersonic` cannot be shared -- it runs in-process, so LDM downgrades it to `isolated` with a warning. `external` ignores the mode entirely: LDM uses the JDBC URL you supplied and never consults a global container.

> [!NOTE]
> `--db mysql` and `--db mariadb` share **one** container. This is not a shortcut: LDM emits an identical `jdbc:mariadb://` URL and `MariaDB103Dialect` for both engines, so Liferay cannot distinguish them from the connection down. A single container also avoids a third idle global on a mixed fleet.

**A mixed fleet runs both globals.** Shared mode exists to save roughly 500MB-1GB per project, and an all-PostgreSQL or all-MySQL fleet gets that saving in full. Mixing engines means both globals run and the saving erodes -- though two shared containers still beat one container per project, which is the comparison that matters.

Before v2.19.0, `shared` was PostgreSQL only and `--database-mode shared --db mysql` exited `1`. That refusal was deliberate: the only global container was PostgreSQL while the MariaDB URL aimed at port 3306 of it, so the combination could never connect.

In `shared` mode every project gets its own database on the one cluster for its engine, named from the project:

```text
lportal_<project name, sanitized, lowercased, hyphens as underscores>
```

**The derived name is always lowercase.** `MyProject` becomes `lportal_myproject`; `Saarbrücken` becomes `lportal_saarbruecken`.

This matters when the shared cluster is **external** and you provision the database yourself: create it with the lowercase name above, because that is the only name LDM will look for. Lowercasing also matches what PostgreSQL itself does with an unquoted `CREATE DATABASE`, so a hand-provisioned database and an LDM-provisioned one end up with the same name.

The same contract carries the name on MySQL, where database names are case-sensitive on Linux (`lower_case_table_names=0`). LDM's own global MySQL container runs with `--lower_case_table_names=1`, which folds names as PostgreSQL does; because LDM always derives lowercase, the name is predictable either way and on an external server too.

In `isolated` mode the database is always called `lportal` and the project name is not used.

## Database Commands

- **`ldm db query [project]`**: Safe, SELECT-only SQL execution against project databases. By default, this resolves credentials automatically and prompts for query confirmation.
  - **`-s`, `--sql "<query>"`**: Inline SQL statement to execute. If not provided, LDM will read from stdin.
  - **`-f`, `--format {table,csv,json}`**: Output format (default: `table`).
  - **`--allow-db-query`**: Explicitly bypasses the interactive confirmation prompt.

- **`ldm db start`** ![Added in v2.15.16](https://img.shields.io/badge/Added%20in-v2.15.16-blue): Starts the shared global databases (e.g. Postgres, MySQL) defined in the infrastructure mode.

- **`ldm db stop`** ![Added in v2.15.16](https://img.shields.io/badge/Added%20in-v2.15.16-blue): Stops the shared global databases defined in the infrastructure mode.
- **`ldm db reset-admin [project]`**: Forcefully resets the password for the default admin account (`test@liferay.com`) to `test` by injecting the known PBKDF2 hash directly into the database. Also activates the account and resets failed login locks.

## Custom Containers ![Added in v2.15.22](https://img.shields.io/badge/Added%20in-v2.15.22-blue)

- **`ldm config add-container [project]`**: Interactively inspects and provisions an arbitrary Docker image to run alongside the Liferay stack.
  - **`--image <image_name>`**: Required. The fully qualified Docker image name to add (e.g., `wordpress:latest`).
  - **`--service-name <name>`**: Optional. Overrides the extracted service name (e.g., `my-wordpress`).

## Compute & Target Node Power Management ![Added in v2.15.30](https://img.shields.io/badge/Added%20in-v2.15.30-blue)

- **`ldm node power wake <node>`**: Powers on or wakes a target compute node.
  - **`--ttl <duration>`**: Specifies the wake duration before scheduled off-hours power enforcement (e.g., `--ttl 2h` or `--ttl 4h`). Default: `2h`.
- **`ldm node power sleep <node>`**: Immediately shuts down a target compute node.
- **`ldm node power status`**: Displays active compute node power states and wake TTLs.
- **`ldm node power enforce`**: Enforces scheduled off-hours shutdowns.
- **`ldm node power sync-dns`**: Queries AWS EC2 for live public IP/DNS names and updates local node configurations.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-28* | *Last Reviewed: 2026-08-28*
