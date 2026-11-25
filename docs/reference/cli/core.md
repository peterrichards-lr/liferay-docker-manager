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

# Change an existing project's host port: stop it, then run with --port.
# The new port is re-mapped and saved to the project's meta, so later runs
# keep it without the flag. No hand edit of docker-compose.yml or meta.
ldm stop my-project && ldm run my-project --port 9090

# Give the Liferay container a fixed hostname (persisted to meta)
ldm run my-project --container-hostname liferay

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

### Reserved project name

You cannot name a project `ldm-infra` or `resources`. LDM's shared
infrastructure (the Traefik proxy) runs as the Docker Compose project
`ldm-infra`, and a project of the same name would share it: starting the
project would remove the proxy that routes every project. Older LDM versions
ran it as `resources`, so that name stays reserved too, while a proxy started
by an older version may still carry it. LDM moves such a proxy to `ldm-infra`
the next time it sets up or tears down the infrastructure, by recreating the
proxy container. The proxy holds no data, so nothing is lost. The check uses
the project name as Docker sees it, so names that become reserved once LDM
makes them Docker-safe are refused too, such as `Resources` or `résources`. A
project whose metadata sets `container_name` to a reserved name is refused as
well.

`run`, `init`, `import`, `link`, `clone`, `quickstart` and `fork` refuse such
a name with exit code `1` before any Docker command runs. Choose another name,
e.g. `my-resources`.

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

### Container Hostname

Docker names a container's host after its container ID, so the hostname changes every time the container is recreated. Some Marketplace apps (Digital Sales Room among them) issue **host-locked activation keys**, checked against the container hostname, and fail their licence check after a recreate.

`--container-hostname NAME` sets the Liferay container's `hostname:` in the generated `docker-compose.yml` and saves it to the project's meta as `container_hostname`, so it survives stop/start, `--force-recreate` and every regeneration of the compose file. A later `ldm run` without the flag keeps it. Change or remove it later with [`config container-hostname`](#config-container-hostname).

- **Allowed characters.** One RFC 1123 label: lowercase letters, digits and hyphens, 1-63 characters, not starting or ending with a hyphen. Anything else is refused before anything is written.
- **No dots, for now.** It has not been verified whether host-locked keys are checked against the short hostname or a fully qualified one, so `liferay.example.com` is refused until that is known.
- **Scaled stacks.** With `--scale liferay=N` (N > 1) the hostname is not applied, with a warning: every replica would share it.
- **Shared hostnames.** Two projects may use the same hostname; LDM warns and names the other project(s).
- **Confirmed after boot.** Once the container is up, `ldm run` reads its hostname with `docker inspect` and stops with exit code 3 if it does not match.

**Packages carry it.** `ldm package` records the hostname in the package manifest as `container_hostname`, and `ldm import` and `ldm quickstart` apply it to the imported project, replacing any hostname that project already had. A value that is not a valid hostname is reported and not applied. To use a different hostname after importing, set it with [`config container-hostname`](#config-container-hostname) or `ldm run --container-hostname`.

### `--samples` Pack

`--samples` copies the sample assets into the project. They come from the
latest release of
[ldm-cx-samples](https://github.com/peterrichards-lr/ldm-cx-samples), which
LDM downloads to `~/.ldm/references/samples/` the first time.

- **The cache follows the latest release.** Each `--samples` command checks,
  once, which release is the latest. When it has moved since the cache was
  made, LDM downloads it again and replaces the extracted samples. With no
  network, LDM uses the cached pack as it is.
- **Downloads are verified.** A downloaded pack is checked against the
  release's `ldm-cx-samples.ldmp.sha256`. A pack that does not match is
  refused, and LDM keeps the previous cache if there is one. A release without
  a checksum file is used with a warning that it was not verified.
- **The pack's manifest is honoured.** When you do not give a tag, the pack's
  declared Liferay `tag` is used. When neither the project nor `--db` sets one,
  its `db_type` is used. A pack that declares no `db_type` leaves your
  `~/.ldmrc` default in place.
- **A stateless pack is not restored.** A pack that declares
  `package_kind: stateless` (see
  [Stateful and stateless packages](#stateful-and-stateless-packages)) is
  deployed onto a fresh instance. A pack that does not declare it restores
  as it always has.

`ldm prune --samples` deletes the cache.

### `--vanilla` Switch ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue)

Bypasses downloading the pre-warmed database seed from GitHub releases. Spawns the Liferay project stack with a pristine, empty database.

`--vanilla` is an *intent* flag: nothing pre-populated. It implies `--no-seed`, refuses to combine with `--samples` or `--snapshot`, and wipes persisted OSGi state. `--no-seed` remains the narrower *mechanism* flag — skip the pre-warmed seed only — and still combines freely with `--samples`/`--snapshot`.

## `init`

Initialize project scaffolding (creating `.liferay-docker.meta`, `portal-ext.properties`, etc.) without actually starting the Docker containers. Accepts many of the same configuration flags as `run`.

```bash
ldm init my-project --tag 2024.q4.0 --db mysql
ldm init my-project --tag-prefix 2026.q3   # newest 2026.q3 tag (LDM-#2168)
```

`--container-hostname` is accepted too, and is saved to meta exactly as it is by [`run`](#container-hostname).

`--tag`, `--tag-latest` and `--tag-prefix` resolve exactly as they do for `run`. `--tag-prefix` was missing from `init` until LDM-#2168.

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

LDM uses smarter defaults for SSL based on your hostname. When a custom domain is used with `--host-name`, SSL is enabled by default to support modern Liferay features like Client Extensions. `localhost` and `*.localhost` names stay on HTTP unless you pass `--ssl` (LDM-#2514), and need no hosts-file entry or `sudo` (LDM-#2513). A bare name with no dot, such as `--host-name myproj`, becomes `myproj.localhost`. See [Networking & DNS](../networking.md) for the full rules.

> [!TIP]
> **SSL Hostname Prompt**: If you explicitly pass the `--ssl` flag without providing a `--host-name`, LDM will interactively prompt you for a virtual hostname, suggesting `<project>.localhost` (no hosts entry needed). A custom domain you enter there goes through the hosts-file prompt as usual. (In non-interactive mode `-y`, it safely defaults to `localhost`).

| Command | Host Name | SSL Default | Access URL |
| :--- | :--- | :--- | :--- |
| `ldm run` | `localhost` | `False` | `http://localhost:8080` |
| `ldm run --ssl` | *Prompts User* | `True` | `https://<prompted-host>` |
| `ldm run --host-name my.localhost` | `my.localhost` | `False` | `http://my.localhost` |
| `ldm run --host-name my` | `my.localhost` | `False` | `http://my.localhost` |
| `ldm run --host-name my.localhost --ssl` | `my.localhost` | `True` | `https://my.localhost` |
| `ldm run --host-name my.demo` | `my.demo` | `True` | `https://my.demo` |
| `ldm run --no-ssl` | `localhost` | `False` | `http://localhost:8080` |
| `ldm run --host-name my.demo --no-ssl` | `my.demo` | `False` | `http://my.demo:8080` |

A `*.localhost` URL carries no port when the global proxy holds port 80; if it could not bind port 80, LDM reports `http://my.localhost:<port>` instead.

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

# Manually bind a Liferay Cloud project ID
ldm import ~/Downloads/my-project.ldmp --cloud-project lctintranet
```

A stateful package (the default) restores its database, `data/`, OSGi state
and search snapshot. A stateless package restores nothing: the project starts
as a vanilla instance with the package's assets deployed. This applies to a
local file, a URL and a GitHub release alike, and `ldm quickstart` follows the
same rule. A stateless package still applies its manifest fields, such as
`tag`, `db_type`, `host_name` and `container_hostname`. Importing a stateless
package over an existing project resets that project's database and volumes,
as a stateful import does. See
[Stateful and stateless packages](#stateful-and-stateless-packages).

## `quickstart`

Bootstrap and start a predefined accelerator demo stack in one command. Downloads target repositories, configures metadata, and automatically starts the environment.

```bash
# Bootstrap the AICA (AI Commerce Accelerator) template
ldm quickstart aica

# Bootstrap and expose the stack dynamically using lfr-tunnel
ldm quickstart aica --share --share-subdomain my-custom-demo

# Bootstrap with a custom target directory and project name
ldm quickstart aica --name custom-aica-demo

# Re-run over an existing project. Without --replace quickstart refuses (and
# asks, interactively), because replacing deletes the project's database
# and volumes. A project that records no LDM version (any quickstart project
# made before 2.26.7) gets its database and search modes from your defaults;
# one that records a version keeps the modes it had.
ldm quickstart aica --replace

# Bootstrap with a custom domain and SSL enabled
ldm quickstart aica --host-name aica.demo --ssl

# Bootstrap with a custom name and SSL explicitly disabled
ldm quickstart aica --name aica-http --host-name aica.demo --no-ssl
```

With `--share`, the tunnel is started by the run itself once the portal is ready, so its URL arrives at the end of the boot rather than before it.

Custom templates and repository mappings can be configured by defining overrides in `~/.ldm_templates.json`. Each key is a template name (matched case-insensitively) and needs both `repo` and `default_name`; an entry missing either is ignored. Set `"private": true` for a repository that needs your GitHub token. A key named `aica` replaces the built-in.

```json
{
  "my_demo": {
    "repo": "https://github.com/example/my-demo.git",
    "default_name": "my-demo"
  }
}
```

```bash
ldm quickstart my_demo
```

An unrecognized name is refused with the list of templates available, built-in and custom.

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

# Package only the project's assets, for deployment onto a vanilla instance
ldm package my-project --stateless
```

### Stateful and stateless packages

By default `ldm package` takes a fresh snapshot and packages it: a **stateful**
package. With `--stateless` it takes no snapshot, and nothing needs to be
running. It packages the project's assets and declares
`"package_kind": "stateless"` in the manifest. The assets are
`osgi/client-extensions/`, `client-extensions/`, `deploy/`, `osgi/modules/`,
`osgi/configs/`, `configs/`, `files/portal-ext.properties`, `scripts/`,
`portal-patches/` and `.ldm/fragment-overrides.json`. The package has no
database dump, `data/`, `osgi/state`, `snapshots/` or search snapshot.
Importing it gives a vanilla instance with those assets deployed.

Client extensions are packaged so that each one ships once and none deploys
too early:

- **Each extension's zip ships once.** A container extension, one with a
  `Dockerfile` that is not an LCP `kind: Job`, also ships its whole expanded
  `client-extensions/<name>/` folder, because its microservice is built from
  it. Any other extension whose zip is in the package ships only the
  descriptors from that folder: `client-extension.yaml`, `LCP.json`,
  `*.client-extension-config.json` and `Dockerfile`. Those are the only files
  LDM reads there, so a site initializer's images are no longer packaged
  twice.
- **Site initializers are held back.** A site initializer is packaged in
  `.ldm/deferred-client-extensions/`, not `osgi/client-extensions/`. That
  includes one `ldm link` has parked there because the portal has not been
  healthy yet. On the consumer's machine, Liferay starts without it, and
  `ldm run` or `ldm wait` deploys it once the portal is healthy. Present at
  first boot against an empty database, it fails silently and creates no
  site.

| | Stateful (default) | Stateless (`--stateless`) |
| --- | --- | --- |
| Snapshot taken | Yes (or `--use-latest` / `--snapshot`) | No; refused with either flag |
| Database, `data/`, OSGi state, search | Included and restored | Not included; Liferay starts fresh |
| Publisher material left out | Yes | Yes |
| Manifest fields (`tag`, `db_type`, ...) | Written | Written |
| `ldm quickstart` seed | Skipped when the package has a database | Skipped |

**A stateless package is built before the portal boots.** Liferay's
auto-deploy consumes `deploy/` once the portal starts, and a stateless package
has no database to carry what it imported. For a linked project,
`ldm package --stateless` compares `deploy/` with what the workspace put there
(its fragment collections and its own `deploy/` files, licences excepted) and
refuses, with exit code `1`, when any is missing, naming each one. Link with
`--no-run` and package before running the project.

**`scripts/` ships and is executed.** In both kinds, `scripts/` is mounted at
`/mnt/liferay/scripts` in the importer's container, and Liferay's entrypoint
runs every script there before the portal starts. `ldm package` names each
shipped script in a warning, and flags any that mention a licence or routes.

A manifest without `package_kind` is stateful, so every existing package
imports exactly as before. Both kinds keep the payload in a top-level
`files.tar.gz`, laid out relative to the project root. That is the member
LDM v2.26.x `--samples` reads, so a samples pack built with `--stateless`
works there too. Its site initializers are held back there as well, because
every LDM since v2.23.0 deploys what it finds in
`.ldm/deferred-client-extensions/` once the portal is ready.

### What a package leaves out

Some of what a snapshot holds belongs to the machine that made it, not to
whoever imports the package. `ldm package` leaves it out and names each file:

- **Licence keys**: licence XML files in `deploy/` and `osgi/modules/`, and
  `data/license/` (LDM-#2443). Whoever imports the package uses their own key;
  `ldm doctor` and `ldm run` tell them where to put it.
- **Marketplace products**: `.lpkg` files in `deploy/` and `osgi/marketplace/`.
  Add `--include-marketplace` only if their licence allows you to
  redistribute them.
- **`routes/`**: the OAuth client credentials and DXP host your Liferay wrote
  for its client extensions (LDM-#2446). The importer's Liferay writes its own
  when it starts.

After an imported package has started, `ldm import` and `ldm quickstart` look
up each OAuth client ID under `routes/default/` in the restored database. For
each one that matches no OAuth application, they warn, naming the client
extension and the fix: remove its `routes/default/<name>` tree and run
`ldm restart`, so Liferay writes the tree again from this database. Without
that fix, the extension's calls to Liferay are refused with 401. This check is
a warning, not a failure. It is skipped when the import does not start the
project (for example `ldm import --no-run`), when no client IDs are on disk,
and when LDM does not run the database or cannot query it.

Each rule applies under `files/` as well. `files/` is copied over
`/opt/liferay` when the container starts, so `files/deploy/`,
`files/osgi/modules/`, `files/osgi/marketplace/` and `files/data/license/`
deliver the same material and are filtered the same way, by content for licence
XML and with the same `--include-marketplace` opt-in.

`scripts/` does ship: it is mounted at `/mnt/liferay/scripts` in the importer's
container. `ldm package` lists every script it ships, and warns about any whose
content mentions a licence or routes, so you can check it does not write your
key or client-extension credentials. Nothing is left out on that account.

Only the package is filtered. A local snapshot keeps everything, so
`ldm restore` on the same machine brings a licensed project back licensed.
`ldm import` warns when a package made before v2.26.13 still contains any of
this.

```bash
# Ship Marketplace products whose licence allows redistribution
ldm package my-project --include-marketplace
```

### Portal patches travel with the package

A package is not filtered for `portal-patches/`: the patched core JARs and
their `.jar.json` sidecars are part of what the project needs to behave as it
did (LDM-#2444). `ldm snapshot` archives them, `ldm restore` and `ldm import`
put them back, and the importer's `ldm run` copies them into the container on
every boot, as it does for yours. See
[Patching Core Portal JARs](../../how-to/portal_patches.md).

`ldm package` lists each patch it ships, with the release recorded in its
sidecar:

```text
Portal patches shipped in the package (1), applied over the stock JARs on every boot:
  - com.liferay.site.initializer.impl.jar: introduced in 2026.q1.12-lts
```

The manifest records the same set (`portal_patches`: each JAR's name,
`introduced_in` and `max_version`). `ldm import` checks it against the tag the
project will run, with the rule `ldm run` uses, before it creates the project.
A different quarter, or a tag beyond a patch's `max_version`, is refused with
the message `ldm run` gives and exit code `1`. `--force-portal-patches` imports
and boots it anyway. When `--tag-latest`, `--tag-prefix` or `--nightly` leave
the tag to discovery, the check waits for `ldm run`, which makes it before the
Liferay container is created. A package made before LDM-#2444 records no patch
set and carries no patches.

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

Blocks the terminal until the Liferay project has fully started, initialized, and is ready to accept HTTP traffic. A project whose container is not running fails at once with exit code `1`; start it first with `ldm start` or `ldm run`.

```bash
ldm wait [project]

# Example:
ldm wait demo

# Probe a URL of your choosing instead of the derived one
ldm wait demo --node node-b --probe-url https://my-project.demo
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

A host sleep or a suspended Docker VM does not use up the timeout: the time it
was suspended is detected between polls, reported once, and left out of the
budget. This applies to `ldm run`'s built-in wait too.

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

While following, `ldm logs -f` recognises a few known failure lines and prints
`LDM TROUBLESHOOTING ADVICE` above them. The JVM ones are specific: heap,
metaspace and a full code cache each name their own `ldm run` flag (after
`ldm stop`), since those flags do not exist on `ldm restart` (LDM-#2307).
Tomcat's echo of LDM's own JVM options (`Command line argument: ...`) is never
treated as an error. PostgreSQL's `database "lportal" does not exist` is
reported as expected on a project's first boot, while the image is still
creating the database.

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
>
> For a project that uses **shared search**, `ldm start` also starts the `liferay-search-global` container when it is stopped, and waits for its cluster to be at least yellow before starting Liferay (LDM-#2319). Compose cannot do this itself: the project's compose file refers to shared search only in Liferay's settings, not as a dependency. This matters after a Docker VM restart, which stops every container. If the shared search container does not exist at all, `ldm start` refuses and names `ldm run`, which provisions it. It never recreates shared search itself, because that path can delete every project's search data.

```bash
ldm start [project] [service]     # Start existing containers
ldm stop [project] [service]      # Stop containers gracefully
ldm restart [project] [service]   # Stop and then start
ldm down [project] [service]      # Remove containers (and optionally -v volumes)
ldm rm [project]                  # Alias for 'down'
```

`start` confirms what it did and closes with a next-step hint, rather than succeeding silently (LDM-#1937):

```text
✅  Project 'my-project' started.
💡  Next step: Run 'ldm status' to view environment status, or 'ldm logs -f' to tail the logs.
```

`stop` points back at `start`, because `stop` stops containers without removing them -- they still exist, so `ldm start` is a plain `docker compose start` rather than a full pipeline run:

```text
💡  Next step: Run 'ldm start' to start the container again, or 'ldm status' to view environment status.
```

That hint previously named `ldm run`. `ldm run` remains the correct command when containers were *removed* rather than stopped (the `Not Created` state described above), but having just stopped them, `ldm stop` knows that is not the state the project is in.

`restart` restarts the containers from the project's **existing** `docker-compose.yml`, and so does `restart --force-recreate`. Neither re-renders it from the project's `meta`, so a change to `meta` -- a `custom_env` key, a JVM flag -- is not applied by `restart`. Use `ldm stop` and then `ldm run`. `restart` now says so, along with what it did (LDM-#2317):

```text
✅  Project 'my-project' restarted. Liferay is still starting -- run 'ldm wait my-project' before relying on it.
💡  Next step: 'ldm restart' reuses the existing docker-compose.yml. To apply changes made to the project's meta, run 'ldm stop' and then 'ldm run'.
```

`restart` returns once the containers are back, not once Liferay serves, so a
request sent straight afterwards can get `404`. Scripts should follow it with
`ldm wait <project>`, which the success line names (LDM-#2391). Restarting a
single non-Liferay service prints only `restarted.`

`ldm run` on a project that is already running refuses in the same terms, with exit code `5` when run non-interactively. It used to recommend `ldm restart` "to apply updates".

### Options

- **`--share`** (Only for `start` and `run`): Starts a secure tunnel once the containers are up. On `start` this is the point of the flag: `run` reconfigures the project before booting it, which is exactly what someone sharing an already-configured project does not want. Cannot be combined with `--all` -- a tunnel leases one subdomain and forwards to one target -- and the refusal arrives before any project is started. ![Added in v2.26.0](https://img.shields.io/badge/Added%20in-v2.26.0-blue)
- **`--share-subdomain`**, **`--share-domain`**, **`--share-url`**, **`--share-provider`**, **`--share-image`**, **`--share-inspector`** (Only for `start` and `run`): As for `ldm share start`'s `--subdomain`, `--domain`, `--url`, `--provider`, `--image` and `--inspector`; see [Sharing & Tunnels](../../how-to/sharing_tunnels.md). Sharing is auxiliary to `start`: the project starts, and then it is shared -- `ldm start --share` saves you running `ldm start` and `ldm share start` in sequence. ![Added in v2.26.0](https://img.shields.io/badge/Added%20in-v2.26.0-blue)
- **`--clean-state`** (Only for `start` and `run`): Explicitly wipes the contents of the OSGi state volume before starting the container to remove any stale bundle locks. ![Added in v2.15.22](https://img.shields.io/badge/Added%20in-v2.15.22-blue)
- **`--fix-permissions`** (Only for `start` and `run`): Forces root permission reclamation on bind-mounted host directories. Useful for resolving Liferay lock crashes when running macOS/Windows Docker Desktop against external drives or network shares. ![Added in v2.15.22](https://img.shields.io/badge/Added%20in-v2.15.22-blue)
- **`--force-recreate`** (For `run`, `start`, and `restart`): Recreates containers even if their configuration and image haven't changed. **Note:** Since native Docker `start` doesn't support recreation, passing this to `ldm start` or `ldm restart` will smoothly intercept and route to `up -d --force-recreate` under the hood. ![Added in v2.15.23](https://img.shields.io/badge/Added%20in-v2.15.23-blue)
- **`--force-portal-patches`** (For `run`, `start`, `restart` and `import`): Applies JARs from the project's `portal-patches/` directory even when their recorded `introduced_in` release does not match the release being booted, or when the target JAR is absent from the image. Without it, a release-line mismatch aborts the boot. See [Patching Core Portal JARs](../../how-to/portal_patches.md). ![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue)
- **`--verify-mac`** (Only for `start` and `restart`): Also checks the pinned MAC against the node's own interfaces, over SSH. The MAC *comparison* -- container against configured value -- always runs on these commands and is free, being a local `docker inspect`; this adds the extra round trip that answers a different question: "is the configured address one this node actually has?" It is off by default because that round trip was measured turning a 2.9s restart into 4.3s, and because a wrong pin is already caught once at `ldm target add`, where a typo is cheapest to fix. Use it when a node's interfaces may have changed since it was registered. ![Added in v2.23.0](https://img.shields.io/badge/Added%20in-v2.23.0-blue)
- **`-V`, `--volumes`** (Only for `down`/`rm`): Also removes the project's Docker Compose volumes. Implied automatically by `--delete`.
- **`--infra`** (Only for `down`/`rm`): Also tears down the shared global infrastructure (Proxy, Search, etc.), independently of whatever project is targeted -- can be combined with a project target or passed alone. With `-y`, it refuses (exit 3) while any **other** LDM project is running, naming each one with an `ldm stop` command, and also when it cannot tell what is running; pass `--force` to tear down anyway. The project named in the same `ldm rm <project> --infra` call does not count as another project. Interactively, the existing confirmation still applies (LDM-#2371).
- **`--clean-hosts`** (Only for `down`/`rm`): Removes the project's entries from your `/etc/hosts` file.
- **`-d`, `--delete`** (Only for `down`/`rm`): Escalates teardown beyond just stopping/removing containers -- drops the project's schema from the shared database (if it uses shared-mode DB), deletes the project's indices from the shared search node (LDM-#2337) and the `action.auto_create_index` entry Liferay left for its index prefix there (LDM-#2318), unregisters the project, and **permanently deletes its directory from disk**. This cannot be undone. `ldm rm`/`ldm down` *without* `--delete` only tears down containers and keeps the project registered, so it can be `ldm run` again later; `--delete` is the one-way, destructive option.

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
ldm rm --delete           # Permanently delete the project's directory, DB schema, search indices, and registry entry
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
ldm config env [project] KEY --remove
ldm config env [project] --import              # Import variables from a local .env file
ldm config env                                 # Interactive manager (view and edit all)

# Legacy flat form (still works):
ldm env [project] KEY=VALUE
```

These variables are applied to the **Liferay container only**. `-s/--service`
accepts just `liferay`, which changes nothing. Any other service is refused
with exit `1` and nothing is written (LDM-#2384) -- before that, the flag was
silently ignored and the variable landed on Liferay instead.

To set a variable on another service, such as a client extension, prefix it
with the service id and export it in the environment of `ldm run`. See
[Service-Specific Targeting](../configuration.md#4-service-specific-targeting):

```bash
MY_CUSTOM_EXTENSION_DEBUG=true ldm run [project]   # DEBUG=true in my-custom-extension
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

## `config common-config-policy`

View or set what the `common/` sync does to **one project's** edited copy of a
shared `.config` or `.cfg`: `protect` (keep it), `warn` (replace it and say so)
or `overwrite` (replace it silently). The value is written to the project's
`meta` and overrides `ldm defaults common_config_policy` for that project only.

```bash
# Show the effective value for the current project, and where it comes from
ldm config common-config-policy
ldm config common-config-policy -p my-project

# Set it for one project
ldm config common-config-policy overwrite my-project

# Remove the project's value, so it inherits `ldm defaults` again
ldm config common-config-policy --unset my-project
```

An invalid value is refused (exit `1`). There is no flat legacy alias. The
rules are in
[Overriding a shared file in one project](../configuration.md#overriding-a-shared-file-in-one-project).

## `config container-hostname`

Show, set or remove the Liferay container's fixed hostname (see [`--container-hostname`](#container-hostname)). The value is stored in the project's meta, never in `~/.ldmrc`.

```bash
# Print the current value for the project in this directory
ldm config container-hostname

# Print it for a named project
ldm config container-hostname -p my-project

# Set it (same rules as --container-hostname; no dots)
ldm config container-hostname liferay my-project

# Remove it -- Docker goes back to using the container ID
ldm config container-hostname --unset my-project
```

With a single positional, the positional is the hostname to set on the project in the current directory; use `-p` to name a project when printing. With `--unset`, a single positional is the project.

The hostname is fixed when the container is created, so a change takes effect only when the container is recreated: run `ldm stop <project>` and then `ldm run <project>`.

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
*Last Updated: 2026-11-25* | *Last Reviewed: 2026-11-25*
