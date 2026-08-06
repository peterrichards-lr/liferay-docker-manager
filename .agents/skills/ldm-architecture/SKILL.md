---
name: ldm-architecture
description: Activate this skill whenever designing new features, modifying Docker compose logic, or interacting with Liferay environments.
---

# LDM Architecture Mandates

## Hybrid Volume Strategy (macOS / ExFAT)

To resolve critical filesystem locking deadlocks (e.g., `Unable to create lock manager` or `access_denied_exception`), LDM MUST use a split-volume approach:

- **Named Docker Volumes**: MUST be used for directories requiring POSIX file locking.
  - `/opt/liferay/data`
  - `/opt/liferay/osgi/state`
  - **Documented exception**: when the user opts into `--persist-osgi`, LDM
    deliberately maps `/opt/liferay/osgi/state` to a host bind-mount instead of
    a Named Volume, trading the POSIX-locking guarantee for dramatically faster
    subsequent startups (bypassing OSGi bundle resolution). LDM automatically
    invalidates and wipes this bind-mounted state if the underlying Liferay
    image tag changes, to prevent stale-bundle conflicts. See
    `docs/reference/advanced_cli.md` and `ldm_core/handlers/composer.py`
    (the `persist_osgi` branch of the compose volume builder). This is the
    only sanctioned exception to the Named Volume mandate above.
- **Host Bind-Mounts**: SHOULD be used for directories facilitating developer hot-reloads.
  - `/mnt/liferay/deploy`
  - `/mnt/liferay/files`
  - `/mnt/liferay/scripts`
  - `/opt/liferay/osgi/modules`
  - `/opt/liferay/osgi/client-extensions`
  - `/opt/liferay/osgi/log4j`
- **macOS Hypervisor Sync**: LDM MUST implement a minimum 2-second "Sync Wait" after extracting backups to the host and before hydrating Docker volumes. This compensates for VirtioFS/gRPC-FUSE sync lag.
- **Volume Naming Consistency**: LDM MUST explicitly set the `name:` property for all Named Volumes in the generated `docker-compose.yml`.

## Infrastructure Enforcement

- **Database**: Standardize on PostgreSQL with mandatory healthchecks.
- **Search**: Use shared Global Search (ES8) by default; support Sidecar fallback isolation.
- **Self-Tuning JVM**: LDM MUST proactively scale JVM resources (e.g. `ReservedCodeCacheSize=512m`) and disable restrictive optimizations (e.g. `TieredStopAtLevel=1`) during "Production-grade" workloads like full search reindexing to prevent `NoSuchMethodException` and `CodeCache` exhaustion.
- **Logging**: Force `LIFERAY_LOG4J2_CONFIGURATION_FILE` injection to guarantee hot-reload capability.

## Automation Standards

To support CI/CD pipelines and headless automation, all LDM commands MUST adhere to a standardized exit code contract:

- `0`: Success.
- `1`: Generic/Validation Error.
- `2`: Authentication/Permission Error (e.g. LCP login required).
- `3`: Infrastructure/Data Error (e.g. Backup download failure).
- `4`: Orchestration/Deployment Error. Used for LDM-internal failures at the
  orchestration layer -- e.g. `ldm_core/pipelines/run.py`'s port-conflict-detected
  and project-path-resolution-failed cases -- as opposed to a user-input
  validation problem (which stays under `1`) or an external data/API failure
  (which uses `3`, e.g. the same file's Docker Hub tag-discovery failures).
  This triage was done deliberately, one call site at a time, per
  [#996](https://github.com/peterrichards-lr/liferay-docker-manager/issues/996)
  -- not every failure in `ldm_core/runtime/orchestration.py`/`pipelines/run.py`
  belongs under `4`; most are genuinely `1` (bad project id, missing flag,
  precondition not met) and were deliberately left alone.
- `126`: Command Invocation Error. No genuine candidate for this exists in the
  orchestration/pipeline layer as of the #996 triage -- every "not found"-shaped
  message there (project not found, archetype not found) is a validation error
  (`1`), not a failure to invoke a command. This code is reserved for a true
  invocation failure at that layer if one is ever added; see the `run_command()`
  exception below for where invocation-shaped failures currently do occur.
- **Low-level subprocess wrapper exception**: `ldm_core/utils.py`'s
  `run_command()` helper -- called from a very large number of sites across
  the codebase -- intentionally uses POSIX-standard shell conventions instead
  of the contract above for its own direct failure exits: `124` for a timed-out
  subprocess (matching GNU `timeout`'s convention) and `127` for
  "command not found" (matching the shell's own convention), and otherwise
  passes through the wrapped subprocess's own `returncode` unchanged on a
  generic failure, since discarding that information would make wrapped-tool
  failures harder to diagnose. This is a deliberate, standard choice for a
  subprocess-wrapping utility, not a violation of the contract above -- the
  0-4/126 contract governs LDM's own top-level command outcomes, not every
  exit path of every subprocess it shells out to.

## Liferay Cloud Golden Path

LDM serves as a bridge for Liferay Cloud development. To maintain stability, it enforces a strict boundary:

- **Code (Git)**: Git remains the source of truth for the workspace structure, Client Extensions, and OSGi source. LDM must NEVER modify the user's Git history or structure.
- **Data (LCP)**: LDM automates the retrieval and restoration of Cloud backups (`database.gz` and `volume.tgz`).
- **Orchestration**: LDM must dynamically flatten LCP's nested backup structures into standard LDM snapshots during hydration.

## Custom Containers & Multi-Compose Architecture

- **Custom Containers Integration**: When a user requests to run external services (e.g., WordPress, Node.js, Web Crawler) alongside Liferay, use the LDM `custom_containers` feature rather than altering the native LDM Python orchestration.
- **Multi-Compose Decoupled Networks**: For enterprise multi-compose decoupled architecture setups, always refer to the reference templates in `docker-compose-templates/` to understand the standard `shared-search-net` and `shared-crawl-net` external networking boundaries. Do not invent new bridging architectures if these templates suffice.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-06* | *Last Reviewed: 2026-08-06*
