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

## 8. Active Work State & Plan (June 20, 2026)

### Status

- Released `v2.11.17` successfully to master.
- Received feedback to make the tunnel inspector dashboard opt-in, clean up the local host-side `.env` files of `LFT_INSPECTOR_BIND`, and bind the dashboard to `127.0.0.1` inside the container by default (unless opted in).

### Plan: Opt-in Tunnel Inspector & Clean .env (v2.11.18)

1. **Update `ldm_core/cli.py`**:
   - Add `--share-inspector` to the `ldm run` command parser.
   - Add `--inspector` to the `ldm share start` subcommand parser.
2. **Update `ldm_core/handlers/share.py`**:
   - Update `cmd_start()` signature to accept `inspector=False` parameter.
   - If `provider == "lfr-tunnel-docker"`, save `share_inspector` as `"true"` or `"false"` in `project_meta`.
3. **Update `ldm_core/handlers/runtime.py`**:
   - In `cmd_run` / `cmd_import`, parse `share_inspector` from `args` and `project_meta`, and save it to metadata.
   - Pass `inspector=share_inspector` to `self.manager.share.cmd_start(...)` when sharing starts automatically.
4. **Update `ldm_core/handlers/composer.py`**:
   - Remove the block that writes `LFT_INSPECTOR_BIND` into `.env` file.
   - If `share_inspector` is enabled: add `LFT_INSPECTOR_BIND=${LFT_INSPECTOR_BIND:-0.0.0.0}` to the container's env list, and map port `"4040:4040"`.
   - If `share_inspector` is disabled (default): add `LFT_INSPECTOR_BIND=${LFT_INSPECTOR_BIND:-127.0.0.1}` to the container's env list, and do NOT map port `4040`.
5. **Update `ldm_core/tests/test_composer.py`**:
   - Update tests to reflect that `ports` and `LFT_INSPECTOR_BIND=0.0.0.0` are only mapped when `share_inspector` is True in metadata/args.
   - Add test case verifying the default safe behavior (`ports` is absent, and `LFT_INSPECTOR_BIND=127.0.0.1`).
6. **Verify**:
   - Run tests and lint checks locally inside `.venv`.
