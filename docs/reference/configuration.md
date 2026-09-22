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

## Shared Configuration (`LDM_COMMON_DIR`)

By default, LDM uses a `common/` directory located in the project's parent, the current working directory, or `~/.ldm/common/` to store shared configurations and licenses.

If you need to use a specific, shared configuration across multiple independent project directories or CI pipelines, you can override this by setting the `LDM_COMMON_DIR` environment variable:

```bash
# Example: Point LDM to a shared organization config folder
export LDM_COMMON_DIR="/path/to/shared/organization/common"
ldm run my-project
```

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

---

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-09-22* | *Last Reviewed: 2026-09-22*
