# CLI Core Commands

Display a tabulated overview of all initialized LDM sandbox environments.

```bash
ldm list
ldm ls

# Machine-readable output for scripts/automation, instead of the color-coded
# table -- a stable JSON array with one object per project (project, version,
# target, status, running_containers, total_containers, db_unhealthy, url,
# seeded, path, last_seen).
ldm list --json
```

The `Status` column reflects more than just whether the container process is alive: a project whose container is running but has failed its own Docker `HEALTHCHECK` (e.g. Postgres crash-looping after an out-of-disk `PANIC`) is reported as `Unhealthy` rather than `Running`, and a project whose app container is healthy but whose DB container has failed its healthcheck is reported as `Running (DB unhealthy)`. This does not perform a live HTTP probe -- for that, use `ldm wait` after starting a project.

`Not Created` (LDM-#1870) is distinct from `Stopped`: it means the project's `meta` and volumes exist, but no container matching it exists at all -- typically because the containers were removed directly (e.g. `docker rm`) rather than through LDM. `Stopped` still has a container to start; `Not Created` does not, so `ldm start` refuses it (see below) and `ldm run` must be used to recreate the stack. Both `--json` and the formatted table use the same `Not Created` string in the `status` field/column; `running_containers`/`total_containers` are `0`/`0` in this state, same as for a project with zero matched containers under the older binary behavior -- a consumer that already branches on those counts rather than the status string is unaffected by this new value.

## `run` (alias: `up`)

Initialize and start a project stack. If the project is already initialized, it will print an informational message and provide a 5-second window to safely abort (CTRL+C) before reconfiguring.

```bash
# Run with a specific tag and virtual hostname
ldm run --tag 2024.q4.0 --host-name demo.local

# Automatically grab the latest Quarterly Release
ldm run demo --tag-latest --release-type qr

# Run with a resource-optimized JVM profile (great for laptops with less RAM)
ldm run my-project --lean

# Inject an environment variable directly
ldm run my-project --env LIFERAY_COMPANY_DEFAULT_WEB_ID=my-domain.com

# Repeat --env for more than one variable
ldm run my-project --env A=1 --env B=2

# Enable specific Liferay feature flags
ldm run demo --feature LPS-122920 dev beta

# Run on a custom port
ldm run my-project --port 8081

# Using the alias
ldm up demo

# Initialize with "Confidence Booster" samples
ldm run demo --samples

# Auto-open the browser after startup
ldm run demo --open

# Start an ngrok tunnel to expose Liferay to the public internet
ldm run demo --expose

# Boot a 2-node Liferay cluster in one command
ldm run demo --scale liferay=2

# Boot a scaled stack and open the browser
ldm run demo --scale liferay=2 --open

# Interactive run (will prompt for version and project name)
# Prompts are automatically pre-filled using the Cascading Defaults system
ldm run
```

### `--open` Switch

Use `--open` to automatically launch the Liferay URL in your system browser once the instance is ready. This is equivalent to running `ldm browser` immediately after startup, but in a single command.

### `--scale` Switch

Use `--scale SERVICE=N` to boot a scaled stack without having to run `ldm scale` as a separate step. Multiple services can be scaled at once:

```bash
ldm run demo --scale liferay=2 --scale my-ext=3
```

### Tag Discovery & Release Types

When you do not name a tag with `-t/--tag`, LDM discovers one from the container
registry. `--tag-latest`, `--release-type <channel>`, `--tag-prefix <prefix>` and the
interactive `Release type (lts|u|qr|nightly|master|latest), prefix, or specific tag`
prompt all feed the same resolver.

| Channel | Means | Resolves to (verified 2026-09-10) |
| :--- | :--- | :--- |
| `lts` | Long-Term Support quarterly (the default when nothing is specified) | `2026.q1.12-lts` |
| `qr` | Quarterly Release — the newest quarterly, LTS or not | `2026.q3.2` |
| `latest` / `any` | The newest release of any family, quarterly preferred | `2026.q3.2` |
| `u` | The legacy `7.4.13-uNNN` update line | `7.4.13-u152` |
| `nightly` / `master` | The floating nightly tag, not a timestamped build | `7.4.13.nightly` |
| a prefix (e.g. `2025.q1`) | The newest patch within that prefix | `2025.q1.27-lts` |

```bash
# The newest quarterly release, whatever quarter that currently is
ldm run demo --tag-latest --release-type qr

# The newest patch of a specific quarter
ldm run demo --tag-prefix 2025.q1
```

> [!IMPORTANT]
> **`--portal` has only one usable channel.** `liferay/portal` publishes no quarterly,
> LTS (`*-lts`), update (`*-u`), `*-qr` or `nightly` tags at all — its releases are named
> `7.4.3.132-ga132`. Only `latest`/`any` (or an explicit `-t`) can resolve for a Portal
> CE project; every other release type correctly reports that nothing was found.

Results are cached in `~/.liferay_docker_cache.json` for 24 hours per
`(repository, release type, prefix)` combination. Pass `--refresh` to force a live
lookup — worth knowing, because a cached answer for one channel can make an unrelated
broken channel look healthy (LDM-#1647).

A withdrawn tag is skipped rather than offered. `liferay/dxp` publishes a
`7.4.13-u999` placeholder whose only image the registry reports as `inactive`, and on a
version sort it beats the real newest update release — so the winner of a legacy
`*-uNNN` lookup is checked against the registry before being used (LDM-#1649). The check
fails open: an unreachable registry keeps the candidate rather than discarding it.

If the registry cannot be reached at all, discovery falls back to
[`releases.json`](https://releases.liferay.com/releases.json), which covers every
quarterly and GA release but not nightlies — an unpublished build is not a release
(LDM-#1648).

### Workspace Product Pin

If the project is linked to a workspace whose `gradle.properties` sets
`liferay.workspace.product`, LDM compares that pin against the tag it resolved and
warns when they disagree (LDM-#1658):

```text
⚠️  The linked workspace pins liferay.workspace.product=dxp-2026.q3.0
    (Docker tag 2026.q3.0), but this run resolved 2026.q3.2.
```

Interactively LDM then offers the pinned tag, defaulting to yes. With
`-y`/`--non-interactive` it warns and proceeds, so existing automation is
unaffected.

This matters more than a version mismatch usually does. A shared OSGi
fragment/override bundle carries `Import-Package` ranges bound to one product
line, and bnd copies a declared range into the manifest verbatim — so a bundle
built for `dxp-2026.q3.0` does not resolve on `2026.q3.2`. The failure shows up
as an unresolved bundle in the OSGi log at boot, a long way from the tag decision
that caused it.

LDM stays quiet when:

- `-t`/`--tag` was given — an explicit tag is a decision, not an accident;
- the workspace has no `liferay.workspace.product`;
- the pin and the resolved tag are the same release. `dxp-2026.q1.7` and
  `2026.q1.7-lts` count as agreement.

`ldm set-version <product-key>` writes the pin; both a plain workspace
(`gradle.properties` at the root) and a Liferay Cloud workspace
(`liferay/gradle.properties`) are recognised.

### Compatibility Claims ![Added in v2.22.0](https://img.shields.io/badge/Added%20in-v2.22.0-blue)

A pin states a version but makes no claim. `tag` in an `.ldmp` manifest — and
`liferay.workspace.product` in a workspace — is a fact about how the package was
**built**; every consumer reads it as a claim about what it **supports**. Those
are different, and a consumer deviating from the pin could not tell whether they
were doing something the publisher tested and rejected, something nobody has
tried, or something fine (LDM-#1791).

> [!IMPORTANT]
> **There is no such thing as a "stale" pin.** A package legitimately pins an
> older line because a later one was tried and failed, or because nobody has had
> time to qualify one. The pin is the statement of what is known to work.

A package may therefore carry a separate `compatibility` claim, recording what
it has actually been *tested* on. Every `.ldmp` published before v2.22.0 carries
none, and that absence is the default: **no claim**.

Whenever `ldm run` settles on a tag it says which of the three applies:

```text
ℹ  Liferay tag 2026.q1.7-lts is declared verified by the package
   (evidence: https://…/actions/runs/123, tested 2026-09-17, with LDM 2.22.0).

ℹ  The package declares 2026.q1.7-lts verified; this run resolved
   2026.q1.12-lts, unverified -- nothing has been tested for it.

ℹ  Liferay tag 2026.q1.12-lts carries no compatibility claim: the package
   records the tag it was built with, not one it has been tested against.
```

The third line is the default, and it is printed rather than left silent
deliberately. LDM-#1782 was silence being read as success.

#### The declared ceiling, and `--ignore-verified-ceiling`

Declaring a *refuted* tag — one that was tried and failed — sets a ceiling at
the highest verified tag. A refutation is a tested fact, so LDM refuses to boot
above it and exits `1`:

```text
❌  The package declares 2026.q1.7-lts as its verified ceiling, and
    2026.q1.12-lts is above it.
      2026.q1.12-lts was tried and it failed (evidence: …, tested 2026-09-17).
      A pin records the version that has been TESTED -- this one is a tested
      negative result, not a package lagging behind.
      Pass --ignore-verified-ceiling to proceed anyway. The override is
      recorded in the project metadata.
```

Packages get fixed, and a consumer may know more than the publisher did, so
`--ignore-verified-ceiling` proceeds anyway. It is accepted by every command
that boots a project — `run`/`up`, `init`, `import`, `link`, `clone`,
`init-from`, `restore` and `quickstart` — because the ceiling is enforced in the
run pipeline they all share. It
is never silent: LDM warns, and writes a `compatibility_override` record into the
project metadata naming the tag, the ceiling it crossed, the evidence and the
timestamp.

Nothing is refused that LDM cannot reason about. A tag with no version in it —
`nightly`, `master` — orders against nothing and is announced as unknown, never
refused. A `verified` claim with no refutation is a point claim and sets no
ceiling at all: promoting it to a refusal would invent a tested fact that nobody
tested.

See [`package`](#package) for how a publisher declares a claim.

### `--vanilla` Switch ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue)

Bypasses downloading the pre-warmed database seed from GitHub releases. Spawns the Liferay project stack with a pristine, empty database.

`--vanilla` is an *intent* flag: nothing pre-populated. It implies `--no-seed`, refuses to combine with `--samples` or `--snapshot`, and wipes persisted OSGi state. `--no-seed` remains the narrower *mechanism* flag — skip the pre-warmed seed only — and still combines freely with `--samples`/`--snapshot`.

## `init`

Initialize project scaffolding (creating `.liferay-docker.meta`, `portal-ext.properties`, etc.) without actually starting the Docker containers. Accepts many of the same configuration flags as `run`.

```bash
ldm init my-project --tag 2024.q4.0 --db mysql
```

### External Database Integration

LDM supports connecting your local Liferay instance to an external database (e.g., a shared development database or a standalone local database server) instead of running a database container within the project's Docker Compose stack.

To initialize a project with an external database:

```bash
ldm init my-project --db postgresql --database-mode external
```

LDM will launch an interactive wizard to gather your JDBC connection details (Host, Port, Database Name, Username, Password) and generate the necessary properties in your `portal-ext.properties`. The database service container is entirely omitted from the generated stack.

**Name the engine.** `external` is a *mode* -- it says who runs the database, not which one it is -- so pairing it with `--db postgresql` or `--db mysql` lets LDM resolve the JDBC driver and the per-tag Hibernate dialect for your server, exactly as it does for a database it runs itself.

> [!NOTE]
> **`--db external` still works** and is the older spelling of the same thing (LDM-#1511). LDM infers the engine from the JDBC URL scheme you supply -- `jdbc:postgresql://` means PostgreSQL, `jdbc:mysql://` and `jdbc:mariadb://` mean MySQL -- and writes the driver and dialect accordingly. Where the scheme names an engine LDM does not support (an Oracle URL, say), it falls back to writing only the URL, username and password, which is what it always did. Existing projects whose `meta` records `db_type: "external"` are read forward the same way and keep booting unchanged.

### SSL Defaults (New Projects)

LDM uses smarter defaults for SSL based on your hostname. When a custom `--host-name` is used, SSL is enabled by default to support modern Liferay features like Client Extensions.

> [!TIP]
> **SSL Hostname Prompt**: If you explicitly pass the `--ssl` flag without providing a `--host-name`, LDM will interactively prompt you for a custom virtual hostname. This allows LDM to attempt to inject the custom domain into your `/etc/hosts` file automatically. (In non-interactive mode `-y`, it safely defaults to `localhost`).

| Command | Host Name | SSL Default | Access URL |
| :--- | :--- | :--- | :--- |
| `ldm run` | `localhost` | `False` | `http://localhost:8080` |
| `ldm run --ssl` | *Prompts User* | `True` | `https://<prompted-host>` |
| `ldm run --host-name my.local` | `my.local` | `True` | `https://my.local` |
| `ldm run --no-ssl` | `localhost` | `False` | `http://localhost:8080` |
| `ldm run --host-name my.local --no-ssl` | `my.local` | `False` | `http://my.local:8080` |

### 🛡️ Modern Liferay & JDK 17+ Standards

LDM automatically hardens modern environments (DXP 2024+ and modern Quarterly Releases) to ensure stable startup:

- **JVM Module Exports**: Automatically injects mandatory `--add-opens` flags for JDK 17+ (covering `java.net`, `java.lang.reflect`, `security`, and more).
- **Hardened MySQL 8.4 (LTS)**:
  - Standardized on the **MariaDB JDBC Driver** and `MariaDB103Dialect` to mirror **Liferay Cloud (LXC)** environments.
  - Forces `mysql_native_password` authentication for CI compatibility.
  - Includes performance-optimized connection parameters (e.g., `rewriteBatchedStatements`, `prepStmtCacheSize`).
  - **Redline Configuration**: Explicitly sets `hibernate.dialect` and `jdbc.default.*` properties in `portal-ext.properties` to ensure reliable interpretation of mixed-case keys (like `driverClassName`).
  - Prioritizes `LIFERAY_JDBC_DEFAULT_*` environment variables ONLY for runtime user overrides; LDM baseline always uses `portal-ext.properties`.
- **Proactive Boot Sequencing**: Configures `depends_on` with healthchecks to ensure Liferay only starts once the database is fully ready to accept connections.

## `link` (alias: `init-from` ![Deprecated](https://img.shields.io/badge/Deprecated%20in-v2.15.16-red)) ![Added in v2.15.16](https://img.shields.io/badge/Added%20in-v2.15.16-blue)

Initialize a project from a source workspace and establish a **persistent link**. This command records the workspace path in the project metadata and automatically starts the `monitor` process to sync your code changes in real-time. If a Liferay Cloud Workspace is detected, it will also launch an interactive wizard to hydrate the data from the remote environment.

> [!NOTE]
> `ldm init-from` is a supported legacy alias for `ldm link` and will continue to work. New scripts and documentation should use `ldm link`.

```bash
# ldm link <source_path> [project_name] [--host-name custom.local]
ldm link ~/repos/my-workspace my-project --host-name forge.demo

# Link with the latest tag and disable CAPTCHAs for CI testing
ldm link ~/repos/my-workspace my-ci-project -y --tag-latest --no-captcha

# Manually bind a Liferay Cloud project ID to the local workspace
ldm link ~/repos/my-workspace my-project --cloud-project lctintranet

# Record the link without starting the file watcher, and without booting
ldm link ~/repos/my-workspace my-project --no-monitor --no-run

# Legacy alias (still works)
ldm init-from ~/repos/my-workspace my-project
```

> [!NOTE]
> `ldm link` normally ends by starting a file watcher, which runs until Ctrl-C.
> **`--no-monitor`** records the link and returns instead; the link is persisted
> either way, so `ldm monitor -p <project>` attaches later with no path argument.
> **`--no-run`** sets the project up without booting it. Together they make
> `ldm link` scriptable (LDM-#1689).

### Liferay Cloud workspaces

A Liferay Cloud (LCP) repository keeps its Liferay Workspace under `liferay/`
and its standalone services -- any sibling directory holding both an `LCP.json`
and a `Dockerfile` -- beside it. `ldm link` resolves that layout, imports the
workspace code from `liferay/`, and copies each standalone service into
`<project>/services/`. The infrastructure directories (`backup`, `ci`,
`database`, `search`, `webserver`) are skipped.

The Liferay Cloud project ID is resolved in this order:

1. `--cloud-project`, if given;
2. the `id` field in the repository-root `LCP.json` / `lcp.json`;
3. an interactive prompt, defaulting to the repository directory name.

With `-y`/`--non-interactive` and no ID available from the first two, LDM
**refuses with exit code `2`** rather than guessing -- a wrong ID would run
`lcp` commands against someone else's project.

## `clone` ![Added in v2.15.16](https://img.shields.io/badge/Added%20in-v2.15.16-blue)

Clone a remote Git repository and initialize an LDM project from it. Unlike `link`, which connects to an existing local workspace, `clone` handles the Git clone step automatically and sets up hot-reload mounts in one command.

```bash
# Clone a Git repository and initialize an LDM project
ldm clone https://github.com/my-org/my-repo.git

# Clone into a named project
ldm clone https://github.com/my-org/my-repo.git my-project

# Clone using SSH and specify a host name
ldm clone git@github.com:my-org/my-repo.git my-project --host-name demo.local

# Clone and force a full Gradle build on initialization
ldm clone https://github.com/my-org/my-repo.git --build
```

## `import` (Data Packages Only)

Import a pre-built **LDM Package (`.ldmp`)** or remote package URL. As of v2.15.16, `ldm import` is restricted to **data packages only**. To clone a Git repository use `ldm clone`; to link a local workspace use `ldm link`.

```bash
# Import a local .ldmp package
ldm import ~/Downloads/my-project.ldmp

# Import a remote .ldmp package by URL
ldm import https://example.com/assets/my-project.ldmp

# Import from a GitHub Release (auto-detects .ldmp asset)
ldm import https://github.com/my-org/my-repo

# Import using a specific release type filter
ldm import https://github.com/my-org/my-repo --tag-latest --release-type qr

# Manually bind a Liferay Cloud project ID
ldm import ~/Downloads/my-project.ldmp --cloud-project lctintranet
```

## `quickstart`

Bootstrap and start a predefined accelerator demo stack in one command. Downloads target repositories, configures metadata, and automatically starts the environment.

```bash
# Bootstrap the AICA (AI Commerce Accelerator) template
ldm quickstart aica

# Bootstrap and expose the stack dynamically using lfr-tunnel
ldm quickstart aica --share --share-subdomain my-custom-demo

# Bootstrap with a custom target directory and project name
ldm quickstart aica --name custom-aica-demo

# Bootstrap with a custom domain and SSL enabled
ldm quickstart aica --host-name aica.demo --ssl

# Bootstrap with a custom name and SSL explicitly disabled
ldm quickstart aica --name aica-http --host-name aica.demo --no-ssl
```

Custom templates and repository mappings can be configured by defining overrides in `~/.ldm_templates.json`.

## `package`

Package a project snapshot (code elements, database, and volumes) into a portable LDM package (`.ldmp` archive) alongside a SHA-256 checksum signature (`.ldmp.sha256`).

```bash
# Package the current project
ldm package

# Package a specific project, outputting to a custom directory
ldm package my-project -o /tmp/packages

# Package using a specific repository manifest identifier and the latest snapshot
ldm package my-project --repo my-owner/my-repo --use-latest

# Override target host-name and SSL defaults in the package manifest
ldm package my-project --host-name custom.demo --ssl
ldm package my-project --host-name custom.demo --no-ssl
```

### Declaring What Was Tested ![Added in v2.22.0](https://img.shields.io/badge/Added%20in-v2.22.0-blue)

The manifest's `tag` says how the package was **built**. These three flags say
what it has been **tested** on — see *Compatibility Claims* under
[`run`](#run-alias-up) for how a consumer then reads it.

| Flag | Meaning |
| :--- | :--- |
| `--verified <tag>` | This package has been tested against this Liferay tag and it worked. |
| `--refuted <tag>` | This tag was tried and it failed. Declares the verified ceiling. |
| `--evidence <ref>` | URL or reference to whatever produced that result. **Mandatory** for either. |

```bash
# A point claim: tested here, nothing said about anything else
ldm package my-project \
  --verified 2026.q1.7-lts \
  --evidence https://github.com/my-org/my-repo/actions/runs/123

# A ceiling: verified at q1.7, and q1.12 was tried and failed
ldm package my-project \
  --verified 2026.q1.7-lts \
  --refuted 2026.q1.12-lts \
  --evidence https://github.com/my-org/my-repo/issues/1782
```

`--evidence` is not optional politeness. A claim that LDM could mint without
anything having run becomes noise within a month, so a claim without it is
refused with exit code `1`, before any snapshot is taken. LDM never manufactures
a claim of its own — a package that declares nothing keeps the default of "no
claim", which is what every package published before v2.22.0 already carries.

The claim is written into the manifest under `compatibility`, with a `tested_on`
date and the LDM version that recorded it (a January claim is weaker in
December), and each entry has a `platform` slot for the day claims become
architecture-qualified — `2026.q1.7-lts` has booted green on `aarch64` while
failing on an `x86_64` runner, so that dimension is real even though LDM does
not yet read it.

## Data Management Commands

LDM includes powerful commands for managing your project's database, OSGi state, and Elasticsearch indices. For full details on the following commands, please see the [Data Management Guide](../../how-to/data_management.md).

- **`snapshot` / `restore`**: Backup and recover exact project states.
- **`package`**: Export a project snapshot into a portable `.ldmp` package.
- **`hydrate`**: Create or restore a project from a local Liferay Cloud backup.
- **`cloud-fetch`**: Sync an existing local project directly with a live Liferay Cloud (LCP) environment.
- **`reset` / `re-seed`**: Surgically clear data folders or completely wipe a project back to its original vanilla state.

## `monitor`

Restarts the background watch process for a project linked to a Liferay workspace. This command can **only be used for projects created with `ldm link`** (or the legacy `ldm init-from`). It automatically syncs built artifacts (`.jar`, `.war`, `.zip`) whenever they are updated in the workspace.

```bash
ldm monitor [project_name] --delay 2.0
```

## `wait`

Blocks the terminal until the Liferay project has fully started, initialized, and is ready to accept HTTP traffic.

```bash
ldm wait [project]

# Example:
ldm wait demo

# Probe a URL of your choosing instead of the derived one
ldm wait demo --node aws-2 --probe-url https://my-project.demo
```

### Choosing the URL to probe (`--probe-url`)

By default the probe follows the target: against a remote node it dials that
node's address, which is what stops it checking `127.0.0.1` on your own
machine while the containers run elsewhere.

That default is right and is unchanged. But LDM cannot infer the correct
target in every topology. With SSL on a remote node the certificate is issued
for the **project's host name** while the derived URL dials the node's
**address**, so the probe and the certificate disagree by construction — and
if you reach the stack through an SSH tunnel, the address LDM can see is not
the one your client uses at all.

`--probe-url` replaces the derived URL verbatim. Nothing is rewritten: not the
scheme, not the host, not the port. Omit it and resolution behaves exactly as
before, on local and remote targets alike.

> [!NOTE]
> This affects `ldm wait` only. `ldm run`'s built-in wait does not make an HTTP
> probe — it watches container health and Liferay's own logs — so there is
> nothing there for this switch to override.

The wait sequence uses a progressively aggressive validation strategy with visual milestone tracking:

1. **Container Health:** Polls the Docker daemon until the `liferay` container reaches a `healthy` state.
2. **OSGi Subsystem Readiness:** Connects to the internal Gogo shell (via telnet) to ensure all core OSGi bundles are completely `Active` without any unresolved dependencies.
3. **HTTP Server Validation:** Repeatedly sends lightweight HTTP probes to Liferay's primary dashboard endpoints (`/c/portal/layout` and `/o/api`) looking for a `200 OK`.

This guarantees that scripts or CI/CD pipelines leveraging `ldm wait` will not proceed until Liferay is strictly capable of serving web requests, mitigating race conditions during automated deployments or integration tests.

## `logs`

View real-time logs. Supports filtering by project, specific services, global infrastructure, or individual scaled replicas.

```bash
ldm logs [project] [service1] [service2] ...

# Examples:
ldm logs                  # All logs for current project
ldm logs demo             # All logs for 'demo' project
ldm logs -f               # Follow logs continuously
ldm logs -n 250           # Show last 250 lines (default: 100)
ldm logs -t               # Show timestamps
ldm logs --since 1h       # Show logs from the last hour
ldm logs --until 10m      # Show logs until 10 minutes ago
ldm logs --no-wait        # Tailing usually waits for containers to be ready; use this to tail immediately
ldm logs --export         # Export logs to a local file
ldm logs --include-infra  # Include global infrastructure logs when viewing/exporting project logs
ldm logs --infra          # Show logs for all global infrastructure (ES, Proxy, etc.)
ldm logs --infra es       # Show logs only for Global Elasticsearch
ldm logs --infra proxy    # Show logs only for Global SSL Proxy
ldm logs demo liferay     # Only Liferay logs for 'demo'
ldm logs demo liferay my-ext # Multi-service tailing (all replicas)
```

### Targeting a Specific Scaled Replica (`--instance N` / `-i N`)

When a service is scaled to multiple replicas (e.g. `ldm scale demo liferay=3`), `ldm logs` streams from **all instances simultaneously** by default. Use `--instance N` to isolate a single replica:

```bash
# Stream logs from replica 2 of the liferay service only
ldm logs demo liferay --instance 2

# Short form, following in real time
ldm logs demo liferay -i 2 -f

# Tail the last 50 lines of replica 3
ldm logs demo liferay -i 3 -n 50
```

> [!NOTE]
> `--instance` routes to `docker logs` directly (bypassing Compose) so it targets the exact container. The container name is resolved from project metadata using the standard naming convention `{project}-{service}-{index}` (e.g. `demo-liferay-2`). This pattern is stored automatically when you run `ldm scale`, making subsequent lookups instant.

<!-- -->

> [!TIP]
> If you request an out-of-range instance (e.g. `--instance 5` when only 3 replicas are running), LDM will report the valid range and exit cleanly.

## `start`, `stop`, `restart`, `down` (alias: `rm`)

Manage the lifecycle of a project or a specific service.

> [!NOTE]
> The `start` command simply boots existing containers without running configuration or network scaffolding -- it never creates a container. If a project does not exist, `start` will immediately fail and recommend `ldm run`. The same applies if the project exists (its `meta` and volumes are intact) but its containers were removed directly, e.g. via `docker rm` (LDM-#1870, reported as `Not Created` by `ldm list`): rather than letting the underlying `docker compose start` fail with its own internal wording about a service it never named, `ldm start` refuses up front and names `ldm run`, which recreates the containers from the existing volumes without losing data.

```bash
ldm start [project] [service]     # Start existing containers
ldm stop [project] [service]      # Stop containers gracefully
ldm restart [project] [service]   # Stop and then start
ldm down [project] [service]      # Remove containers (and optionally -v volumes)
ldm rm [project]                  # Alias for 'down'
```

### Options

- **`--clean-state`** (Only for `start` and `run`): Explicitly wipes the contents of the OSGi state volume before starting the container to remove any stale bundle locks. ![Added in v2.15.22](https://img.shields.io/badge/Added%20in-v2.15.22-blue)
- **`--fix-permissions`** (Only for `start` and `run`): Forces root permission reclamation on bind-mounted host directories. Useful for resolving Liferay lock crashes when running macOS/Windows Docker Desktop against external drives or network shares. ![Added in v2.15.22](https://img.shields.io/badge/Added%20in-v2.15.22-blue)
- **`--force-recreate`** (For `run`, `start`, and `restart`): Recreates containers even if their configuration and image haven't changed. **Note:** Since native Docker `start` doesn't support recreation, passing this to `ldm start` or `ldm restart` will smoothly intercept and route to `up -d --force-recreate` under the hood. ![Added in v2.15.23](https://img.shields.io/badge/Added%20in-v2.15.23-blue)
- **`--force-portal-patches`** (For `run`, `start`, and `restart`): Applies JARs from the project's `portal-patches/` directory even when their recorded `introduced_in` release does not match the release being booted, or when the target JAR is absent from the image. Without it, a release-line mismatch aborts the boot. See [Patching Core Portal JARs](../../how-to/portal_patches.md). ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue)
- **`--verify-mac`** (Only for `start` and `restart`): Also checks the pinned MAC against the node's own interfaces, over SSH. The MAC *comparison* -- container against configured value -- always runs on these commands and is free, being a local `docker inspect`; this adds the extra round trip that answers a different question: "is the configured address one this node actually has?" It is off by default because that round trip was measured turning a 2.9s restart into 4.3s, and because a wrong pin is already caught once at `ldm target add`, where a typo is cheapest to fix. Use it when a node's interfaces may have changed since it was registered. ![Added in v2.23.0](https://img.shields.io/badge/Added%20in-v2.23.0-blue)
- **`-V`, `--volumes`** (Only for `down`/`rm`): Also removes the project's Docker Compose volumes. Implied automatically by `--delete`.
- **`--infra`** (Only for `down`/`rm`): Also tears down the shared global infrastructure (Proxy, Search, etc.), independently of whatever project is targeted -- can be combined with a project target or passed alone.
- **`--clean-hosts`** (Only for `down`/`rm`): Removes the project's entries from your `/etc/hosts` file.
- **`-d`, `--delete`** (Only for `down`/`rm`): Escalates teardown beyond just stopping/removing containers -- drops the project's schema from the shared database (if it uses shared-mode DB), unregisters the project, and **permanently deletes its directory from disk**. This cannot be undone. `ldm rm`/`ldm down` *without* `--delete` only tears down containers and keeps the project registered, so it can be `ldm run` again later; `--delete` is the one-way, destructive option.

- **`--keep-credentials`** (Only for `down`/`rm`, with `--delete`): Keeps database and admin passwords in the `~/.ldm/removed` archive that `--delete` writes. They are **removed by default**, with a marker left in place of each value; opting in means the archive holds them in plaintext and securing it becomes yours to do. Set it permanently with `ldm config set tombstone_keep_credentials true`. See [Data Management](../../how-to/data_management.md) (LDM-#1703).

  Interactively, `--delete` now lists what is about to go and asks before touching anything (LDM-#1703):

  ```text
  ⚠️  The following will be permanently removed:
    my-project: 1.1 GB, no snapshot -- database state will be lost
  ❓  Permanently delete this project, including containers and volumes? [y/N]:
  ```

  The prompt defaults to **no**, and reports whether a snapshot exists -- `ldm snapshot` is the real undo for database state, and it is the one thing `--delete` cannot give back. With `-y`/`--non-interactive` there is no prompt and behaviour is unchanged, so existing automation is unaffected.

### Examples

```bash
ldm stop --all            # Stop all running projects in the workspace
ldm restart --all         # Restart all running projects
ldm restart               # Full stack restart (graceful stop + run)
ldm down --volumes        # Tear down stack and clear all database/data state
ldm down --infra          # Also tear down the global infrastructure (Proxy, Search)
ldm down --clean-hosts    # Remove project entries from your /etc/hosts file upon deletion
ldm rm --delete           # Permanently delete the project's directory, DB schema, and registry entry
```

## `status`

View the status of all projects in the current workspace.

```bash
ldm status

# Machine-readable output for scripts/automation -- a stable JSON object
# with "infrastructure" and "projects" arrays; each project entry gains a
# "status" field alongside the existing "running" boolean.
ldm status --json
```

> [!TIP]
> Projects marked with a 🌱 (seedling) emoji were initialized from a **Seeded State**, meaning they started with a pre-calculated database and OSGi cache for near-instant boot times.

Like `ldm list` (see above), `ldm status`/`ldm status --detailed` distinguishes `Not Created` (LDM-#1872) from `Stopped`: `Not Created` means no container matching the project exists at all (typically `docker rm`-ed directly, meta/volumes intact), while `Stopped` means a container was found but isn't running. Previously both cases fell through to the same bare `Stopped` label here, unlike `ldm list`, which already made this distinction (LDM-#1870, #1876) -- this closes that inconsistency. `ldm run` recreates a `Not Created` project's containers from the existing volumes; `ldm start` cannot, for the same reason described under `start` below.

---

## `deploy`

Hot-deploy built artifacts or rebuild extension images.

```bash
ldm deploy [project] [service] --rebuild

# Examples:
ldm deploy                # Sync all artifacts and refresh stack
ldm deploy demo my-ext --rebuild  # Rebuild and restart one extension
```

## `scale`

Scale services within a project for multi-node simulation and clustering tests.

```bash
ldm scale [project] service=count

# Examples:
ldm scale demo liferay=2  # Scale Liferay to 2 nodes (enables clustering)
ldm scale demo my-ext=3   # Scale a client extension to 3 nodes
```

## `shell` & `gogo`

Jump into a container shell for deep inspection or connect to the OSGi Gogo console for runtime management.

**Interactive Shell Examples:**

```bash
# Enter bash in the Liferay container
ldm shell demo

# Common Shell Tasks (inside container):
# 1. View live Tomcat logs
cd tomcat/logs && tail -f catalina.out

# 2. Check injected environment variables
env | grep LIFERAY

# 3. Verify mounted OSGi configurations
ls osgi/configs
```

**Gogo Shell Examples:**

```bash
# Connect to the Gogo shell (requires --gogo-port during run)
ldm gogo demo

# Common Gogo Commands:
# 1. List all active bundles
lb

# 2. Check for unresolved dependencies
diag

# 3. List declarative services (SCR)
scr:list
```

## `config env` (legacy: `env`)

Manage persistent environment variables in project metadata.

```bash
ldm config env [project] KEY=VALUE
ldm config env [project] --remove KEY
ldm config env [project] -s liferay KEY=VALUE  # Target a specific service instead of global
ldm config env [project] --import              # Import variables from a local .env file
ldm config env                                 # Interactive manager (view and edit all)

# Legacy flat form (still works):
ldm env [project] KEY=VALUE
```

## `config feature` (legacy: `feature`)

Quickly toggle Liferay feature flags without manually editing `portal-ext.properties`. Requires a project restart to take effect.

```bash
ldm config feature [project] --enable LPS-122920
ldm config feature [project] --disable LPS-111111 LPS-222222

# Legacy flat form (still works):
ldm feature [project] --enable LPS-122920
```

## `config edit` (legacy: `edit`)

Rapidly modify project configuration files in your system's `$EDITOR` (defaults to `vi` or `notepad`).

```bash
ldm config edit [project]                         # Edit .liferay-docker.meta
ldm config edit [project] --target properties     # Edit portal-ext.properties

# Legacy flat form (still works):
ldm edit [project]
```

## `config log-level` (legacy: `log-level`)

Manage Liferay internal logging levels (Log4j2) without restarts.

```bash
# List current custom levels
ldm config log-level --list

# Set a specific category to DEBUG
ldm config log-level [project] --bundle portal --category com.liferay.portal --level DEBUG

# Interactive configuration
ldm config log-level

# Legacy flat form (still works):
ldm log-level [project] --list
```

## Global Flags

The following flags can be passed to almost any command:

- **`-v`, `--verbose`**: Enable verbose debug logging to trace exact shell commands, API calls, and Docker interactions.
- **`--info`**: Show informational logging (a middle tier between standard output and debug).
- **`-y`, `--non-interactive`**: Accept all defaults and skip confirmation prompts.
- **`--upgrade-db`**: Force-enables Liferay's database auto-upgrade tool on startup (`LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true`).
- **`--no-upgrade-db`**: Force-disables Liferay's database auto-upgrade tool.
- **`--backup-on-upgrade`**: Force-enables automatic database backup snapshot creation before running version upgrades.
- **`--no-backup-on-upgrade`**: Force-disables automatic database backup snapshot creation before running version upgrades.
- **`--tag-prefix`**: Force specific tag discovery prefix when resolving latest tags.
- **`--skip-project`**: Skips project discovery. Useful for global diagnostics like `ldm doctor --skip-project`.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-09-21* | *Last Reviewed: 2026-09-21*
