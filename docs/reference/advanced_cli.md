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
- **`--vanilla`** ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue): Bypasses all seeding mechanisms to start a completely fresh, vanilla Liferay instance.
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
- **`--target-env`**: (Used with `link`, `clone`, and `import`). Overrides the environment name metadata.
- **`--build`**: (Used with `link` and `clone`). Forces a full rebuild of any Server-Side Client Extensions found in the source workspace during initialization.
- **`--on-validation-failure`**: Behaviour when a config file (e.g. `fragment-overrides.json`) fails schema validation in non-interactive mode. Choices: `die` (default) or `ignore`.

## JVM & Tomcat Tuning

Advanced options for memory constraints and Java-level debugging.

- **`--lean`**: Enables a resource-optimized JVM profile. It caps memory and limits background threading. Highly recommended for laptops with less than 16GB of RAM or CI runners.
- **`--no-jvm-verify`**: Disables the JVM bytecode verification skip (`-Xverify:none`). This skip is enabled by default to shave seconds off startup time. Disable it if you are encountering weird classloader errors or testing core JVM security features.
- **`--no-tld-skip`**: Re-enables Tomcat's aggressive TLD (Tag Library Descriptor) scanning. LDM skips scanning non-Liferay jars by default to dramatically improve Tomcat initialization speed.
- **`--jvm-args="<args>"`**: Pass raw JVM arguments directly to Liferay, completely overriding LDM's defaults. Example: `--jvm-args="-Xmx8g -Xms8g"`

## Debugging & Diagnostics

- **`--gogo-port <port>`**: Exposes the OSGi Gogo shell on a specific host port. Required if you plan to use `ldm gogo [project]`.
- **`--mount-logs`**: By default, logs remain inside the container. This flag bind-mounts the `tomcat/logs` directory directly to the host for external log aggregator testing.
- **`--delay <seconds>`**: (Used with `monitor` and `link`). Alters the debounce delay for the background file watcher. Useful on slow filesystems.

## Search & Legacy Infrastructure

- **`--sidecar`**: Forces the project to use Liferay's internal Sidecar search process rather than the shared Global Search container. LDM does this automatically if the global container is offline. *(Note: Sidecar uses Elasticsearch 7 and is deprecated in Liferay 2025.Q2+. LDM will automatically ignore this flag and force Shared Search for newer releases).*
- **`--es7`**: Forces the Global Search infrastructure to use Elasticsearch 7 (legacy) instead of the default Elasticsearch 8. Use with `ldm infra-setup --es7`. *(Note: Elasticsearch 7 is deprecated in Liferay 2025.Q2+; future releases require Elasticsearch 8).*

### `--search-mode`

Controls whether LDM provisions a dedicated Elasticsearch container or connects to the Global Shared Elasticsearch cluster. Available modes: `sidecar` or `shared`.

### `--database-mode`

Controls whether LDM provisions an isolated PostgreSQL database or connects to the Global Shared Database cluster. Available modes: `isolated` or `shared`.

## Database Commands

- **`ldm db query [project]`**: Safe, SELECT-only SQL execution against project databases. By default, this resolves credentials automatically and prompts for query confirmation.
  - **`-s`, `--sql "<query>"`**: Inline SQL statement to execute. If not provided, LDM will read from stdin.
  - **`-f`, `--format {table,csv,json}`**: Output format (default: `table`).
  - **`--allow-db-query`**: Explicitly bypasses the interactive confirmation prompt.

- **`ldm db start [project]`** ![Added in v2.15.16](https://img.shields.io/badge/Added%20in-v2.15.16-blue): Start only the database service for a project without booting the full Liferay stack. Useful for performing database maintenance, running migrations, or connecting external tools without a full environment start.

  ```bash
  ldm db start my-project

  # Alias — the bare 'start' keyword is also routed to db start:
  ldm start my-project
  ```

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-16* | *Last Reviewed: 2026-07-09*
