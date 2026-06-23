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

## 8. Active Work State & Plan (June 23, 2026)

- Released `v2.11.34` successfully (integrated automatic stop in non-interactive/yes mode and `--leave-running` option for workspace imports).
- Released `v2.11.33` successfully (integrated CWD home directory warning, `--stop-running` flag for import, and portable packaging documentation updates).
- Released `v2.11.31` successfully (integrated quickstart templates overrides, automatically start sharing under `ldm import`, standalone `ldmp` package exports, and test suite/pre-commit fixes).
- Released `v2.11.30` successfully (integrated Visual Diagnostics Web Dashboard (P3), Dynamic JVM Self-Tuning (P2), and solved GITHUB_ACTIONS env var mock test coverage mismatch).

### Plan

1. **Fix `--tag-latest` / `--tag-prefix` Overrides**:
   - [x] Modify tag resolution in `cmd_run` in `ldm_core/handlers/runtime.py` to bypass project metadata tag if `tag_latest` or `prefix` is specified.
   - [x] Add unit test in `ldm_core/tests/test_runtime.py` verifying that `--tag-latest` properly overrides metadata-stored tags.
   - [x] Run all unit tests to ensure they are green.
2. **Safe lfr-tunnel Path Check & User-Controlled Installation**:
   - [x] Support custom binary path resolution via `LDM_LFR_TUNNEL_BIN`/`LFR_TUNNEL_BIN` env vars, `lfr_tunnel_bin` config, system `PATH` check (`shutil.which`), and fallback to `~/.ldm/bin/lfr-tunnel`.
   - [x] Avoid automatic downloads. Require user instruction via CLI flag `--auto-install-lfr-tunnel` or interactive prompt.
   - [x] Support custom installation command via `LDM_LFR_TUNNEL_INSTALL_CMD`/`LFR_TUNNEL_INSTALL_CMD` env vars or `lfr_tunnel_install_cmd` config.
   - [x] Add CLI arguments in `ldm_core/cli.py` for `--auto-install-lfr-tunnel`.
   - [x] Add unit tests verifying path resolution priority and interactive/non-interactive prompt behaviors.
3. **Preferred Admin Details Global Configuration**:
   - [x] Retrieve preferred admin settings (`admin_password`, `admin_screen_name`, etc.) from global config in `sync_common_assets` in `ldm_core/handlers/config.py`.
   - [x] Map and inject these settings into `host_updates` written to `portal-ext.properties`.
   - [x] Add support in `cmd_config` for setting these config keys.
   - [x] Add unit tests verifying global config admin properties are correctly mapped and written.
   - [x] Fix merging collision: ensure custom values in global `common/portal-ext.properties` override vanilla defaults in project `portal-ext.properties` while preserving project-specific custom overrides.
4. **Fix Windows Subprocess `UnicodeDecodeError`**:
   - [x] Update `run_command` in `ldm_core/utils.py` to explicitly specify `encoding="utf-8"`. This prevents crashes on Windows when subprocesses (like `mkcert` or Docker) output UTF-8 symbols (like `✓` checkmarks) in `cp1252` locale environments.
   - [x] Run pre-commit format and full test suite to verify stability.
5. **Release Stable v2.11.35**:
   - [x] Bump version to v2.11.35, push release branch, and open PR.
   - [x] Fix mypy method-assign type check errors in unit tests.
   - [x] Wait for PR checks to pass and auto-merge to complete.
   - [x] Checkout master, pull, tag `v2.11.34` -> `v2.11.35` locally, push tag, and verify release.
6. **Release Stable v2.11.36 (Spaces in Named Volumes & Windows Path Parsing)**:
   - [x] Fix volume path parsing in `ldm_core/handlers/composer.py` to support drive letters and sanitize named volumes with spaces.
   - [x] Add unit tests verifying parsing logic and volume name sanitization.
   - [/] Bump version to v2.11.36, run tests, open PR, and tag release.
7. **Handle Project Collisions During Import / Run**:
   - [x] Auto-unregister project from registry if its registered path does not exist on disk.
   - [x] Add CLI flag `--overwrite-registry` to automatically resolve registry collisions when the path exists.
   - [x] Prompt the user interactively to overwrite the registry collision if they are not in non-interactive mode.
   - [x] Add unit tests verifying auto-cleanup of non-existent paths, overwrite-registry flag, and interactive prompts.
8. **Implement Automated Release Script**:
   - [x] Create `scripts/release.py` to automate getting latest master, checking uncommitted files, bumping version, committing, pushing, and auto-merging the PR.
   - [x] Ensure only documentation changes (`.md` files) and version changes (`ldm_core/constants.py`, `pyproject.toml`) are allowed when running the script.
   - [x] Verify script works correctly and handles clean/unclean workspace states.
   - [x] Fix LDM CHANGELOG version bump extra blank line bug in `ldm_core/handlers/dev.py`.

