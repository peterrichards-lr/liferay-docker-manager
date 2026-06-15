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

## 8. Active Work State & Plan (June 15, 2026)

### Status

- Merged documentation PR 34 and PR 31 into master.
- Resolved CI smoke-test failures, added unit tests for OSGi state persistence, and created `scripts/verify_osgi_persistence.sh` E2E verification script on `feature/osgi-performance`.
- Resolved an issue where standalone macOS/Linux binaries (`ldm-macos-x86_64`, `ldm-linux`) would crash with a `ModuleNotFoundError` (`pydantic_core._pydantic_core`) when run with Python versions other than 3.13.
- **Current Issue**: Investigating `ldm upgrade` / `ldm system upgrade` check failures. The check fails when unauthenticated GitHub API rate limits (60 req/hour per IP) are reached, which commonly affects corporate offices or colleagues sharing public IP addresses.

### Plan to resolve LDM Upgrade Check Failures

1. **Implement Fallback Upgrade Checking via HTML Redirect** [Completed]:
   - If the unauthenticated GitHub API rate limit is exceeded (HTTP 403) or any other API request exception occurs during stable checks (`pre_release=False`), fallback to a HEAD request on `https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest`.
   - Inspect the returned `Location` header to parse the tag name of the latest stable version (e.g. `v2.11.8`).
   - Dynamically build the asset download URL based on the user's OS and architecture (e.g., `ldm-macos-arm64` on Apple Silicon, `ldm-macos-x86_64` on Intel Mac, etc.), matching the format `https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{version}/{asset_name}`.
2. **Add Unit Tests for Fallback** [Completed]:
   - Add unit tests verifying both successful fallback to HTML redirect and graceful failure when both mechanisms fail.
3. **Fix Existing Broken Unit Test** [Completed]:
   - Fix the failing test `test_check_tooling_and_integrity_venv_inactive` by correctly mocking `verify_executable_checksum` to return `"Source"`.
4. **Verify stability & check-in** [Completed]:
   - Run the full test suite with `pytest` to confirm all unit tests pass.
5. **Update Troubleshooting Documentation**:
   - Update `docs/INSTALLATION.md` and `docs/TROUBLESHOOTING.md` to document the GitHub API rate limit behavior and explain the automatic fallback mechanism.
