# Configuration Guide

## Cascading Defaults

LDM uses a robust cascading configuration system to ensure consistent, reproducible environments while offering flexibility across system, user, and project levels. This architecture prevents a project's foundational settings from mysteriously changing if global configurations are updated later.

### The Resolution Hierarchy

When LDM needs a default value (e.g., for the Liferay tag, database type, or port), it resolves it in the following order (highest to lowest priority):

1. **CLI Flags**: A flag passed to the command wins over everything stored. `ldm run` then persists what it resolved into the project's `meta`, so the value survives into later `ldm up` calls that omit the flag.
2. **Project Metadata (`meta`)**: Once a project is created (via `init`, `run`, or `hydrate`), the resolved settings are "frozen" into the project's metadata. This ensures stability. The older `.liferay-docker.meta` and `.ldm.meta` filenames are still read.
3. **User Defaults (`~/.ldmrc`)**: The `defaults` block. Custom defaults specific to the current developer's machine.
4. **Global Defaults (`/etc/ldmrc`)**: The `defaults` block. System-wide defaults, typically managed by system administrators or CI/CD provisioning scripts.
5. **Convention Defaults**: Hardcoded fallback values within the LDM source code (e.g., Port `8080`, DB `postgresql`, Search `shared`).

For why the cascade is shaped this way, and the difference between `ldm defaults` and `ldm config set`, see [Conventions & Configuration](../explanation/conventions_and_config.md).

### Managing Defaults

You can view and manage these settings interactively or programmatically using the `ldm defaults` command.

```bash
# View the current resolution tree for all configuration keys
ldm defaults

# Set a custom user-level default (e.g., always prefer MySQL)
ldm defaults db_type mysql

# Set a system-wide default (requires appropriate permissions)
sudo ldm defaults port 9090 --global

# Remove a custom default to fall back to the convention
ldm defaults --remove tag
```

## Custom Containers (`custom_containers`) ![Added in v2.15.22](https://img.shields.io/badge/Added%20in-v2.15.22-blue)

You can inject external services or orchestrate multi-compose architectures (such as WordPress sidecars, specialized caching layers, or monitoring agents) into the LDM runtime by defining `custom_containers` in your `.ldmrc` file.

These custom containers are seamlessly integrated into the generated `docker-compose.yml`, automatically attached to the `liferay-net` network, and properly labeled so that LDM's garbage collection manages their lifecycle alongside the core Liferay stack.

### Example `.ldmrc`

```json
{
  "custom_containers": [
    {
      "name": "wordpress",
      "image": "wordpress:latest",
      "subdomain": "blog",
      "ports": ["8000:80"],
      "environment": {
        "WORDPRESS_DB_HOST": "db",
        "WORDPRESS_DB_USER": "lportal",
        "WORDPRESS_DB_PASSWORD": "test",  # pragma: allowlist secret
        "WORDPRESS_DB_NAME": "lportal"
      }
    }
  ]
}
```

- **`name`**: The service name in `docker-compose.yml`.
- **`subdomain`**: Automatically injects Traefik routing labels to route `[subdomain].lfr.local` securely via HTTPS to this container.
- **`ports`**: Host port bindings.
- **`environment`**: Key-value pairs for environment variables.

## Configuration Files

- **`logging.json`**: Managed via `log-level` command.
- **`common/`**: Files here (configs, XML licenses, LPKG files) are synced to all project stacks.
- **`services/`**: Place standalone `Dockerfile` directories here for orchestration.

## Sharing Configuration Across Projects (`common/`)

Anything you put in a `common/` folder reaches every project LDM runs. This is the supported way to share configuration, such as an API key in an OSGi configuration, across several projects; [Sharing Configuration Across Projects](../how-to/share_configuration.md) walks through it. On every `ldm run` (which also creates a project), and on `ldm config rebuild-properties`, `ldm config reset-properties` and `ldm infra migrate-search`, LDM syncs:

| in `common/` | copied to the project's |
|---|---|
| `*.config`, `*.cfg` | `osgi/configs` |
| `*.xml` (licences and other XML, except `portal-log4j-ext.xml`) | `deploy` |
| `*.lpkg` | `deploy` |
| `portal-ext.properties` | merged into the project's `portal-ext.properties` |

### Which `common/` folders are used

LDM reads up to two, in this order:

1. `~/.ldm/common/`, always, when it exists.
2. **Then one more:** `LDM_COMMON_DIR` if it is set; otherwise the first `common/` found in the current directory, the project's parent, or its grandparent.

The second folder is **added after** `~/.ldm/common/`, not used instead of it. For a `.config` or `.cfg` file present in both, the second folder's copy is the one that ends up in the project.

Each folder's `portal-ext.properties` is a separate layer of the [properties cascade](../explanation/properties.md): `~/.ldm/common/` is layer 3 (global), and the second folder -- whether it is `LDM_COMMON_DIR` or a workspace `common/` -- is layer 4 (local), so its values win over the global ones unless a global key is marked `# !important`. The layer follows from which folder it is, not from its path: a workspace `common/` under `/home/` is still layer 4.

```bash
# Example: add a shared organisation config folder for this shell
export LDM_COMMON_DIR="/path/to/shared/organization/common"
ldm run my-project
```

### What can and cannot be shared

| Liferay setting lives in | shareable via `common/` |
|---|---|
| System Settings (OSGi Configuration Admin) | yes, as a `.config` file |
| Portal properties | yes, via `portal-ext.properties` |
| Instance Settings (per instance, stored in the database) | **no** |
| Control Panel screens with no OSGi configuration behind them | **no** |

Settings in the last two rows have no file to place, so they must be entered in each project by hand.

### Credentials are stored in plaintext

A credential in a `common/` `.config` file sits in plaintext in that folder and is copied, in plaintext, into every project's `osgi/configs`. Treat both locations as you would any file holding a secret. This is the same exposure that makes `--keep-credentials` opt-in for snapshots (LDM-#1703).

### Overriding a shared file in one project

To give one project its own version of a shared `.config` or `.cfg`, **edit the project's copy in `osgi/configs`.** What the next sync does with your edit is set by `common_config_policy` (LDM-#2424):

| value | when the project's copy has been edited since LDM last wrote it |
|---|---|
| `protect` (**default**) | your copy is kept. If the `common/` version has changed since, LDM warns that it was **not applied**, naming the file |
| `warn` | the `common/` version replaces your copy, and LDM warns, naming the file |
| `overwrite` | the `common/` version replaces your copy, silently. The behaviour before LDM-#2424 |

A copy you have **not** edited is always updated when the `common/` version changes, whatever the policy. A file that was in the project before LDM first copied the shared one is never overwritten.

#### Setting the policy

It is a [cascading default](#cascading-defaults): set it for every project, and override it in any one project.

```bash
# Every project that has no value of its own
ldm defaults common_config_policy overwrite

# One project: written to that project's meta, overriding the default
ldm config common-config-policy overwrite my-project

# Show a project's effective value, and whether it is its own or inherited
ldm config common-config-policy -p my-project

# Remove the project's value, so it inherits the default again
ldm config common-config-policy --unset my-project
```

A project without its own value follows `ldm defaults`; `ldm defaults --remove common_config_policy` returns that default to `protect`. `ldm config common-config-policy` refuses an invalid value. A value that reaches a project's `meta` or `~/.ldmrc` some other way and is not recognised is ignored with a warning, and `protect` applies.

#### How LDM tells an edit from its own copy

LDM records, per project:

- the **names** of the files it copied, in `.liferay-docker.deployed`, as before
- a **checksum** of what it last wrote to each, in `.liferay-docker/common_assets.json`

A copy whose checksum differs from the recorded one has been edited. A file on the list with no checksum yet -- every file copied by an LDM older than this setting -- cannot be told apart from an older `common/` copy, so it is **not** treated as an edit: the first sync after upgrading replaces it, and **warns** if its content differed. From then on it follows the policy.

When the same name is in both `common/` folders, the [second one's copy](#which-common-folders-are-used) is the only one applied.

#### Taking the `common/` version back

When `protect` withholds an update, either:

- delete the project's copy: the next sync copies the `common/` version afresh; or
- set the policy to `overwrite`, globally or for the project, as above.

#### Settings LDM needs

Some `.config` content is not a preference: LDM relies on it for the project to work. When `protect` keeps your copy of one of these files, LDM checks it for the keys it needs. If they are all present with the expected values, your copy is kept quietly, whatever else you changed. If one is missing or different, your copy is still kept, but LDM warns that the project may behave unexpectedly, naming the file, the key and the value it expected.

| file (in `osgi/configs`) | key | expected value |
|---|---|---|
| `com.liferay.portal.search.elasticsearch7.configuration.ElasticsearchConnectionConfiguration.config` and its `elasticsearch8` twin | `active` | `B"true"` |
| | `connectionId` | `"REMOTE"` |
| | `httpSSLEnabled` | `B"false"` |
| | `networkHostAddresses` | `["http://liferay-search-global:9200"]` |

The table is `REQUIRED_CONFIG_KEYS` in `ldm_core/common_assets.py`, and its values are what the shipped baseline writes for shared search. Values are compared as text, ignoring whitespace outside quotes.

#### Exceptions

- **Elasticsearch files** follow the project's search mode first. In a project using its own search sidecar, or with no shared search container, LDM removes them regardless of the policy. `ElasticsearchConfiguration.config` is rewritten on every `ldm run` for shared search, so no policy applies to it; the connection config above does follow the policy.
- **Licences** follow their own rule: a `common/` licence replaces the project's only when it is better (for example, it expires later).

## State Directory (`LDM_HOME`)

LDM keeps its global state -- the project registry, the pre-warmed seed cache, sample packs, plugins and shared `common/` files -- under `.ldm` in the current user's home directory.

Set `LDM_HOME` to put that state somewhere else:

```bash
export LDM_HOME="/Volumes/External/ldm-state"
ldm list          # reads and writes /Volumes/External/ldm-state/.ldm/registry.json
```

`LDM_HOME` takes precedence over everything else, and it is the **only** way to redirect the state directory from outside the process. Setting `HOME` does not work: on macOS LDM reconstructs `/Users/<username>` from `SUDO_USER`/`USER` so that it still finds the real user's home when invoked under `sudo`, which means `HOME` is ignored entirely.

Notes:

- A leading `~` is expanded.
- The directory does not need to exist; LDM creates it on first use.
- An unset or whitespace-only value falls back to the normal home-directory resolution.

> [!TIP]
> This is also the supported way to isolate automated tests and CI jobs from a developer's real state. Any test that runs LDM as a subprocess **must** set it -- see `.agents/skills/testing-and-ci/SKILL.md`.

## Remote Node Readiness (`LDM_SSH_READY_TIMEOUT`)

A command against a remote node that has just been powered on used to fail on
the first attempt. `sshd` accepts a TCP connection on port 22 seconds before it
will authenticate, so a node ~15 seconds into a cold boot is reachable and not
yet usable -- and LDM reported that healthy node as stopped or re-addressed
(LDM-#1863).

LDM now waits. A **transport** failure -- connection refused, credentials
refused, or a connect timeout -- is retried for up to 30 seconds by default:

```bash
export LDM_SSH_READY_TIMEOUT=60   # allow a slower boot
export LDM_SSH_READY_TIMEOUT=0    # disable; fail on the first attempt as before
```

It says so once, up front, rather than waiting silently.

Three properties are deliberate:

- **Only transport failures are retried.** This path is reached only for
  `error during connect`, which means SSH never carried the command and the
  remote side provably did not run it. Retrying a command that had begun
  executing could perform it twice; retrying one that never started cannot.
- **Only causes that waiting can fix.** A wrong hostname, a changed host key
  and an absent network route do not resolve themselves, so they still fail
  immediately -- waiting on them would only delay a correct error.
- **The original failure survives.** When the budget is spent, the exit code,
  the message and the tip are exactly what they were before.

> [!NOTE]
> Like every `LDM_`-prefixed variable, this one is also forwarded into project
> containers as `SSH_READY_TIMEOUT` by the rule below. It has no meaning there
> and is harmless, but it will appear in the container environment.

## Environment Variables LDM Reads

Every `LDM_`-prefixed variable LDM itself reads. The three sections above
(`LDM_COMMON_DIR`, `LDM_HOME`, `LDM_SSH_READY_TIMEOUT`) explain theirs in
full; this table is the index.

This list is **derived from the source**, not maintained by hand —
`ldm_core/tests/test_env_var_reference.py` fails when a variable the code
reads is missing from it. Three variables shipped in v2.26.0 documented
elsewhere and not here, and a hand-kept list would drift the same way again
(LDM-#2025).

Do not confuse these with the variables LDM *forwards into containers*,
described under [Environment Variable Forwarding](#environment-variable-forwarding).
Those are values you supply for your own services; these change how LDM
itself behaves.

| Variable | Default | Effect |
|---|---|---|
| `LDM_ADMIN_PORT` | `18080` | Host port for the global proxy's admin endpoint. |
| `LDM_ALLOW_ROOT` | `false` | Set `true` to permit running as root, overriding the sudo guard. |
| `LDM_BRINGUP_TIMEOUT` | `1800` | Seconds allowed for `docker compose up`/`create` when starting a project's stack. A liveness bound, not a performance limit: it stops a dead Docker transport hanging a run for ever (exit `124`). `0` removes the bound and restores the unbounded wait. See [Stack bring-up timed out](../TROUBLESHOOTING.md#stack-bring-up-timed-out-exit-code-124). |
| `LDM_COMMON_DIR` | — | An extra `common/` folder, read after `~/.ldm/common/`. See [above](#which-common-folders-are-used). |
| `LDM_CPU_IDLE_CHECKS` | `3` | Consecutive idle CPU samples before the readiness wait treats the portal as settled. |
| `LDM_CPU_IDLE_THRESHOLD` | `15.0` | CPU percentage below which a sample counts as idle. |
| `LDM_DEV_MODE` | unset | Must be `true` for developer-only commands; they refuse otherwise. |
| `LDM_DOCKER_TUNNEL` | unset | One SSH connection per run instead of one per Docker command, for projects on a remote node. See [Remote Node Architecture](../explanation/remote-node-architecture.md#7a-one-ssh-connection-per-run-ldm_docker_tunnel). |
| `LDM_DRY_RUN` | unset | `true` plans without executing. Also set by `--dry-run`. |
| `LDM_FAIL_ON_STALE_PATCHES` | unset | `1` fails the run on stale portal patches rather than warning. |
| `LDM_FORWARD_PREFIXES` | — | Extra prefixes to forward into containers. See [below](#3-custom-passthrough-prefixes). |
| `LDM_FRAGMENT_PATCH_TIMEOUT` | `0` (off) | Seconds to wait for OSGi/Site-Initializer readiness. `--fragment-patch-timeout` wins over it. |
| `LDM_HOME` | — | State directory. See [above](#state-directory-ldm_home). |
| `LDM_HTTP_PORT` | `80` | Host HTTP port for the global proxy. |
| `LDM_IGNORE_DOCKER` | `false` | `true` skips Docker availability checks. |
| `LDM_LFR_TUNNEL_BIN` | unset | Path to the `lfr-tunnel` binary, instead of searching `PATH`. |
| `LDM_LFR_TUNNEL_INSTALL_CMD` | unset | Command LDM names when `lfr-tunnel` is missing. |
| `LDM_MCP_CIRCUIT_BREAKER_MAX_ACTIONS` | `5` | Actions allowed inside the MCP circuit-breaker window. |
| `LDM_MCP_CIRCUIT_BREAKER_WINDOW` | `300` | That window, in seconds. |
| `LDM_NO_KEYRING` | unset | `1` bypasses the OS keyring. |
| `LDM_PROXY_LOG_FORMAT` | `common` | Traefik log format for the global proxy. `json` is greppable; `common` emits ANSI escapes. See [Troubleshooting](../TROUBLESHOOTING.md). |
| `LDM_PROXY_LOG_LEVEL` | `ERROR` | Traefik log level. `ERROR` is Traefik's own default, which is why the proxy log is empty on a healthy run. |
| `LDM_REPO_NAME` | `liferay-docker-manager` | Repository LDM checks for updates. |
| `LDM_REPO_OWNER` | `peterrichards-lr` | Owner of that repository. |
| `LDM_RUN_CONTEXT` | unset | `ci` marks the run as automated. |
| `LDM_SAMPLES_PATH` | — | Overrides the sample-extension cache location. |
| `LDM_SEARCH_READY_TIMEOUT` | `300` | Seconds `ldm infra setup` (and any run that creates the shared search node) waits for a newly created Global Search container to answer, probed every 5 seconds and rounded up to whole probes. When it does not answer in time, the automatic search volume repair runs, which refuses while other projects are running unless `--force` is given. `0` skips the wait and goes straight to that repair; it exists so the E2E suite can reach the guard deterministically, not as a tuning knob. A malformed or negative value warns and keeps `300`. |
| `LDM_SLUG_OS_SUFFIX` | unset | Suffix added to the OS component of a verification report slug. Unset on an ordinary run. |
| `LDM_SSH_READY_TIMEOUT` | — | Remote node readiness. See [above](#remote-node-readiness-ldm_ssh_ready_timeout). |
| `LDM_SSL_PORT` | `443` | Host HTTPS port for the global proxy. |
| `LDM_TEST_MODE` | unset | `true` marks a test run. |
| `LDM_WORKSPACE` | — | Overrides the workspace directory. |

> [!NOTE]
> `LDM_CERTS_DIR` appears in the shipped Compose template but is **set by LDM**
> before Compose runs, so setting it yourself achieves nothing.

## Environment Variable Forwarding

LDM forwards specific host environment variables into your project containers
using a prefix-based logic. Some variables are withheld regardless of prefix --
see *What is never forwarded* below.

### 1. Global Prefix Stripping (`LDM_`)

Any host variable starting with `LDM_` is forwarded to **all** containers in the stack with the prefix removed. This is the recommended way to inject global configurations.

- **Host**: `export LDM_COMPANY_ID=123`
- **Container**: `COMPANY_ID=123`

### 2. Automatic Passthrough (AI & Liferay Cloud)

To ease CI integration, LDM automatically forwards variables from known providers as-is (preserving the prefix) to all containers:

- **Liferay Cloud**: `LXC_`, `COM_LIFERAY_LXC_`
- **AI Providers**: `OPENAI_`, `GEMINI_`, `ANTHROPIC_`, `MISTRAL_`

### 3. Custom Passthrough Prefixes

You can extend the automatic passthrough list by setting `LDM_FORWARD_PREFIXES` on your host:

- **Host**: `export LDM_FORWARD_PREFIXES="AWS_,STRIPE_"`
- **Result**: Any variable starting with `AWS_` or `STRIPE_` will be forwarded to all containers.

### 3a. What is never forwarded

Some variables are withheld regardless of prefix, from every route above. The
patterns live in `common/env-blacklist.txt`.

| withheld | why |
|---|---|
| `COM_LIFERAY_LXC_DXP_*`, `LIFERAY_ROUTES_*` | LDM sets these itself for local development |
| `*_OAUTH2_HEADLESS_SERVER_CLIENT_ID` / `_SECRET` | belong to the project's routes, not the environment |
| `LIFERAY_JVM_OPTS` | a hazardous whole-JVM override |
| `*_SECRET`, `*_SECRET_KEY`, `*_PASSWORD`, `*_PASSWD`, `*_TOKEN`, `*_PAT`, `*_PRIVATE_KEY`, `*_CREDENTIALS`, `*_API_KEY`, `*_ACCESS_KEY` | credential-shaped (LDM-#1910) |

**The credential patterns apply even to a prefix you named yourself** in
`LDM_FORWARD_PREFIXES`. Naming a prefix says which *family* of variables you
want forwarded; it is not evidence you meant to ship a credential into a
container image you may not have built. The AI-provider keys are the deliberate
exception -- `OPENAI_*`, `ANTHROPIC_*`, `GEMINI_*` and `MISTRAL_*` are negated
in the shipped list, because the passthrough prefixes above exist to carry
exactly those.

Note the interaction with section 2: `COM_LIFERAY_LXC_` is listed there as
automatic passthrough, but the `COM_LIFERAY_LXC_DXP_*` subset is withheld. So
`COM_LIFERAY_LXC_FOO` forwards and `COM_LIFERAY_LXC_DXP_FOO` does not. That
asymmetry was undocumented and cost an external team days of debugging
(LDM-#1903).

**LDM says what it withheld.** Names only, never values:

```text
Withheld from container environment (blacklisted): LDM_BOT_PAT
  To forward one deliberately, add a negation to the project's env-blacklist.txt, e.g. '!MY_VAR'.
```

### 3b. Forwarding something withheld anyway

A `!` prefix in a project's own `env-blacklist.txt` un-blacklists a name, and
negations win:

```text
!LIFERAY_OAUTH_CLIENT_SECRET
!ACME_*
```

This exists because the list is inherited -- a project's file is concatenated
with the shipped one, so without negation a project could only ever *add*
patterns and had no way to opt back in. Putting the exception in the project,
in a tracked file, makes it deliberate and reviewable.

### 4. Service-Specific Targeting

You can target a specific service (including Client Extensions) by prefixing the variable with the **Service ID** (uppercased, with dashes replaced by underscores):

- **Service ID**: `my-custom-extension`
- **Host Variable**: `export MY_CUSTOM_EXTENSION_DEBUG=true`
- **Container** (`my-custom-extension` only): `DEBUG=true`

`ldm config env -s <service>` is **not** a way to do this. `config env`
writes to the Liferay container only, so for any service other than `liferay`
it refuses, writes nothing, and points here (LDM-#2384). Until then the flag
was silently ignored and the variable landed on Liferay.

> [!IMPORTANT]
> **For a client-extension container this is the only route in.** Sections 1-3
> reach the Liferay container; a client extension receives what its own
> `LCP.json` declares plus variables targeted at it by name.
>
> That is deliberate. A targeted variable names one service explicitly, so
> delivering it surprises nobody. The global pool is implicit and carries
> whatever `LDM_`-prefixed values happen to be exported on the host, which is
> not something to inject into a container image you may not have built.
>
> Until LDM-#1903 this section was documented but unreachable: nothing ever
> called the resolver with a service id, so the branch could not execute.

Credential-shaped names are withheld from targeted variables too -- see
*What is never forwarded* above. `MY_EXTENSION_API_SECRET` is blocked exactly
as `API_SECRET` would be.

So are the LDM-managed names. `MY_EXTENSION_LIFERAY_ROUTES_DXP` is blocked
exactly as `LIFERAY_ROUTES_DXP` would be, and cannot be used to repoint a tree
LDM mounts.

> [!NOTE]
> Until LDM-#1954 only the first of those two held. The blacklist was tested
> against the name as written on the host, while the container received the
> name with the service prefix stripped -- so a pattern anchored at the
> **start** of the name never matched, and `MY_EXTENSION_LIFERAY_ROUTES_DXP`
> arrived as `LIFERAY_ROUTES_DXP`. Suffix patterns like `*_API_SECRET` were
> never affected, because a prefix does not disturb a suffix match, which is
> why the credential half of this section was always true.

---

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-11-23* | *Last Reviewed: 2026-11-23*
