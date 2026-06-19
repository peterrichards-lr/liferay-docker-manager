# LDM Project Mandates

> [!NOTE]
> **Purpose of this file**: This file establishes the core **Project & Developer Mandates** for Liferay Docker Manager (LDM) development (including volume strategies, JVM tuning, exit codes, and release guidelines). All human and AI contributors MUST adhere to these architectural mandates.

This file establishes the foundational mandates for Liferay Docker Manager development.

## 1. Hybrid Volume Strategy (macOS / ExFAT)

To resolve critical filesystem locking deadlocks (e.g., `Unable to create lock manager` or `access_denied_exception`), LDM MUST use a split-volume approach:

- **Named Docker Volumes**: MUST be used for directories requiring POSIX file locking.
  - `/opt/liferay/data`
  - `/opt/liferay/osgi/state`
- **Host Bind-Mounts**: SHOULD be used for directories facilitating developer hot-reloads.
  - `/mnt/liferay/deploy`
  - `/mnt/liferay/files`
  - `/mnt/liferay/scripts`
  - `/opt/liferay/osgi/modules`
  - `/opt/liferay/osgi/client-extensions`
  - `/opt/liferay/osgi/log4j`
- **macOS Hypervisor Sync**: LDM MUST implement a minimum 2-second "Sync Wait" after extracting backups to the host and before hydrating Docker volumes. This compensates for VirtioFS/gRPC-FUSE sync lag, ensuring files created on the Mac are physically visible to the Linux VM.
- **Volume Naming Consistency**: LDM MUST explicitly set the `name:` property for all Named Volumes in the generated `docker-compose.yml`. This prevents Docker Compose from automatically prefixing volumes with the project name, which causes hydration mismatches when LDM attempts to push data directly to the volume by its base name.

## 2. Infrastructure Enforcement

- **Database**: Standardize on PostgreSQL with mandatory healthchecks.
- **Search**: Use shared Global Search (ES8) by default; support Sidecar fallback isolation.
- **Self-Tuning JVM**: LDM MUST proactively scale JVM resources (e.g. `ReservedCodeCacheSize=512m`) and disable restrictive optimizations (e.g. `TieredStopAtLevel=1`) during "Production-grade" workloads like full search reindexing to prevent `NoSuchMethodException` and `CodeCache` exhaustion.
- **Logging**: Force `LIFERAY_LOG4J2_CONFIGURATION_FILE` injection to guarantee hot-reload capability.

## 3. Automation Standards

To support CI/CD pipelines and headless automation, all LDM commands MUST adhere to a standardized exit code contract:

- `0`: **Success**.
- `1`: **Generic/Validation Error**.
- `2`: **Authentication/Permission Error** (e.g. LCP login required).
- `3`: **Infrastructure/Data Error** (e.g. Backup download failure).
- `4`: **Orchestration/Deployment Error**.
- `126`: **Command Invocation Error**.

## 4. Pre-Release Strategy

To prevent "version fatigue" and ensure the stability of the main release channel:

- **Experimental Features**: All brand new or complex functionality (specifically **Liferay Cloud Golden Path** integrations) MUST be released as **Pre-Releases** (e.g. `v2.10.x-pre.y`) first.
- **Verification Gate**: A pre-release feature is only eligible for a stable release after the user has performed a full E2E verification and confirmed its success.
- **Stable Promotion**: Stable releases (`[release]`) MUST be reserved for hardened features and verified bugfixes.

## 5. Liferay Cloud Golden Path

LDM serves as a bridge for Liferay Cloud development. To maintain stability, it enforces a strict boundary:

- **Code (Git)**: Git remains the source of truth for the workspace structure, Client Extensions, and OSGi source. LDM must NEVER modify the user's Git history or structure.
- **Data (LCP)**: LDM automates the retrieval and restoration of Cloud backups (`database.gz` and `volume.tgz`).
- **Orchestration**: LDM must dynamically flatten LCP's nested backup structures into standard LDM snapshots during hydration.

## 6. Python Virtual Environment (venv)

- **Mandatory Alignment**: All development, testing, linting, and Git operations MUST be conducted within the project's Python virtual environment (`.venv`).
- **Hook Isolation**: Git hooks and pre-commit checks rely on packages installed in the virtual environment. Running operations outside the virtual environment (e.g. using global system Python) will trigger hook failures.

## 7. Reference Documentation

- [Architecture Guide](./docs/LDM_ARCHITECTURE.md)
- [Troubleshooting](./docs/TROUBLESHOOTING.md)
- [PaaS "Golden Path" Guide](./docs/guides/PAAS_LOCAL_DEV.md)
- [Agent Rules of Engagement](./.gemini/gemini.md)

## 8. Active Work State & Plan (June 19, 2026)

### Status

- Implemented Remote Import & Packaging features in `ldm_core/handlers/workspace.py` and successfully tested. All unit tests and linter checks pass clean on master.
- Implemented `lfr-tunnel-docker` integration directly into the project's generated `docker-compose.yml` to resolve SentinelOne/EDR friction and hostname routing. Merged to master as v2.11.14.
- Documenting `LFT_CLIENT_TOKEN` authentication token priorities in `docs/guides/SHARING_AND_TUNNELS.md`.
- Correcting `lfr-tunnel` Docker image namespace from `peterrichards` to `peterjrichards` in `composer.py` and `test_composer.py`.
- Implemented `--share-image` and `--image` CLI flags to allow specifying custom tunnel Docker image sources, and verified all unit tests and lint checks.
- Merged the patch release `v2.11.15` changes (`2275a518`) to `master` and successfully pushed the tag `v2.11.15` to trigger the build.
- Merged PR #73 with branch/tag alignment check and validation.

### Plan: Expose Public Tunnel URLs & Support .env Overrides

1. **Update `ldm_core/handlers/runtime.py`**:
   - In `_wait_for_ready()`, if sharing is enabled (`share` or `expose` in meta, or `--share` on CLI), resolve the provider and subdomain.
   - If the provider is `lfr-tunnel` or `lfr-tunnel-docker`, construct the public URL from the subdomain and `LFT_SERVER_URL` (defaulting to `lfr-demo.online`) and print it as the main access URL.
2. **Update `ldm_core/handlers/share.py`**:
   - Add a helper method `resolve_public_tunnel_url(subdomain)` to resolve the URL.
   - In `cmd_start()`, when `lfr-tunnel` or `lfr-tunnel-docker` starts successfully, print the public URL using `UI.success("🌍 Public Tunnel Active: ...")` to align with ngrok.
3. **Update `ldm_core/handlers/composer.py`**:
   - Automatically write/update `LFT_SUBDOMAIN`, `LFT_CLIENT_TOKEN`, and `LFT_SERVER_URL` in the local `.env` file of the project when generating stack configuration for `lfr-tunnel-docker`.
   - Update `lfr-tunnel` service environment definitions in `docker-compose.yml` to read from `.env` using `${VAR:-default}` syntax.
4. **Update `ldm_core/tests/test_composer.py`**:
   - Update the expected environment assertions to match the new dynamic fallback syntax.
5. **Verify**:
   - Run tests and linting to ensure compatibility and correctness.
