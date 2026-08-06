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
- `4`: Orchestration/Deployment Error.
- `126`: Command Invocation Error.

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
