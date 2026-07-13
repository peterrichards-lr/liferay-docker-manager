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

- **Release Orchestration**: All version updates, pre-releases, and stable promotions MUST be performed using the automated orchestrator script: `python3 scripts/release.py --bump [beta|patch|minor|major]` or `python3 scripts/release.py --promote`. Manual git tagging or direct version modifications are strictly prohibited.
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

### 7. Reference Documentation

- [Architecture Guide](./docs/explanation/architecture.md)
- [Troubleshooting](./docs/TROUBLESHOOTING.md)
- [PaaS "Golden Path" Guide](./docs/tutorials/paas_local_dev.md)
- [Workspace Import & Packaging Guide](./docs/how-to/workspace_import.md)
- [Agent Rules of Engagement](./.gemini/gemini.md)
- [Properties Override Hierarchy Guide](./docs/explanation/properties.md)

## 8. Active Work State & Plan (July 13, 2026)

- **Current Active Task (July 13, 2026)**:
  - [/] Add timeout parameter to run_command and enforce a 15-second timeout on reclaim_volume_permissions to prevent WSL hangs, and fix Windows PowerShell 5.1 syntax compatibility errors.
  - [x] Create GitHub Issue for Database Volume Persistence & CLI start Alias.
  - [x] Database Volume Persistence & CLI start Alias.
  - [x] Extend test coverage for logs export, wait milestones, and trace logging.
  - [x] Issue #449: Decouple `DiagnosticsService` monolithic structure into `ldm_core/diagnostics/` submodules. Resolved merge conflicts.
  - [x] Issue #483: Add and update Mermaid diagrams (Architecture & Properties).
  - [x] Epic #439: Broken down into Sub-issues #486, #487, #488, and #489.
  - [x] Issue #499: Add version badges/pills for CLI flags and features in documentation.
  - [ ] Issue #486: Implement Base Pipeline Architecture (`ldm_core.pipelines.base`).
  - [ ] Issue #492: Implement Lock Expiry and Resiliency for Workspace FileLock and ProjectLock.
  - [ ] Issue #493: Graceful Fallbacks and Headless Bypasses for OS Keyring Token Storage.
  - [ ] Issue #494: Add Backwards-Compatible Delegation Shims and Fix Mock Tests for Modular Diagnostics.
  - [ ] Issue #495: Implement Checksum-Based Caching for 5-Layer Properties Rebuilds.
  - [ ] Issue #496: Clean Up Corrupted Partial Output Files on Database Dump Command Failures.

- **Add CSRF Protection and API Token Authentication to Web Dashboard Server (Issue #441)**:
  - [x] Introduce Blueprint and `create_app` factory in `ldm_core/dashboard/server.py`.
  - [x] Add session key validation `before_request` hook.
  - [x] Inject `meta` tag placeholder in `index.html` and replace it in the index view.
  - [x] Implement Alpine.js client security initialization and `apiFetch` wrapper.
  - [x] Add `--token` CLI argument and mapping in `ldm_core/cli.py` and `ldm_core/handlers/dashboard.py`.
  - [x] Write unit tests verifying token authentication validation and bypass routes in `test_dashboard.py`.
  - [/] Create PR #478 for the feature branch.

- **Decouple CI checks from local commit loop (Issue #476)**:
  - [x] Configure stages `[pre-push, manual]` for heavy hooks (`mypy`, `pytest`, `bandit`, `deptry`) in `.pre-commit-config.yaml`.
  - [x] Update `ldm dev-setup` to install both pre-commit and pre-push hook types.
  - [x] Split generic Quality Gate step in `.github/workflows/ci.yml` into distinct, parallel/sequential steps.
  - [x] Add mypy and type stubs to `requirements-dev.txt` for GHA/local direct execution.
  - [x] Document hooks setup inside `docs/how-to/development.md`.
  - [x] Create PR #477 for the feature branch.

- **Safe SELECT-only SQL execution command (Issue #473)**:
  - [x] Create generic `is_query_safe` validator method in `DatabaseService`.
  - [x] Implement database container credentials resolution and output formats (`table`, `csv`, `json`).
  - [x] Add `db query` subparser group, execution routing, and preprocess gates in `cli.py`.
  - [x] Write comprehensive unit/integration tests in `test_database.py`.
  - [x] Document the new command in `docs/reference/advanced_cli.md`.
  - [x] Create PR #475 for the feature branch.

- **Secure Plaintext Token Storage and Restrict Configuration File Permissions (Issue #438)**:
  - [x] Integrate Python's standard `keyring` package to securely store and retrieve Liferay Tunnel tokens (`lfr_tunnel_token`) in the OS-native credential vault.
  - [x] Restrict global config file (`~/.ldmrc`) creation permissions to `0600` and its parent directory to `0700` inside `save_global_config_safe`.
  - [x] Enforce owner-only `0600` permissions on Liferay Tunnel credentials file (`~/.lfr-tunnel/token`) during retrieval and interactive creation.
  - [x] Add comprehensive unit tests in `test_share.py` and `test_utils.py` verifying keyring storage integration, interactive saving fallback, and file mode constraints.

- **Fix Path Traversal (Tar Slip) Vulnerability in Archive Extraction (Issue #437)**:
  - [x] Create generic `is_safe_path` helper in `ldm_core/utils.py` verifying that all extracted archive member names and symbolic/hard link targets resolve within the destination target boundary without any traversal.
  - [x] Refactor `safe_extract` in `ldm_core/utils.py` to check all ZipFile and TarFile member names and link destinations using `is_safe_path` before extraction.
  - [x] Refactor `_extract_snapshot_archive` in `ldm_core/handlers/snapshot.py` to check snapshot TarFile members and symlinks using `is_safe_path`.
  - [x] Add comprehensive unit tests in `ldm_core/tests/test_utils.py` covering traversal/absolute path exclusions, relative link boundary validations, and Zip/Tar slip block triggers.

- **Stream database snapshot dumps directly to disk to prevent OOM memory exhaustion (Issue #442)**:
  - [x] Add `stdout_file` parameter support to `run_command` in `ldm_core/utils.py` and `ldm_core/handlers/base.py` to allow streaming stdout directly to a file descriptor.
  - [x] Refactor database backup snapshot dump routine in `ldm_core/handlers/snapshot.py` to open the backup file and stream stdout directly to it.
  - [x] Implement error boundaries in `snapshot.py` to delete empty/corrupted backup files on failure.
  - [x] Add unit test in `ldm_core/tests/test_snapshot.py` asserting that the `stdout_file` parameter is passed and utilized during snapshot creation.

- **Secure binary downloads by enforcing verified SSL context and hash checks (Issue #444)**:
  - [x] Refactor `download_file` in `ldm_core/utils.py` to write to a `.download_tmp` file and atomically rename on success to prevent corrupt/partial downloads.
  - [x] Refactor `_ensure_binary` in `ldm_core/handlers/share.py` to use the centralized `download_file` helper and remove unverified SSL contexts.
  - [x] Add unit tests in `ldm_core/tests/test_utils.py` verifying successful roundtrips and atomic file cleanup on failure.
  - [x] Update `test_share.py` unit tests to mock `download_file`.

- **Fix silent fallback and lack of locking in JSON config parser (Issue #446)**:
  - [x] Create generic cross-platform `FileLock` utility in `ldm_core/utils.py`.
  - [x] Create safe config loader (`load_global_config_safe`) with detailed JSON Decode exception diagnostics, and atomic saver (`save_global_config_safe`) with lock protection.
  - [x] Refactor config operations in `ldm_core/defaults.py`, `ldm_core/handlers/config.py`, `ldm_core/handlers/ai.py`, and `ldm_core/handlers/share.py` to use safe loaders and savers.
  - [x] Write unit tests verifying roundtrips, JSON syntax warnings, and lock acquisition failure handling.

- **Implement workspace project lock concurrency protection (Issue #448)**:
  - [x] Add `ProjectLock` cross-platform file locking utility in `ldm_core/utils.py`.
  - [x] Wrap `detect_project_path` in `ldm_core/handlers/base.py` to automatically validate and acquire the project lock for state-mutating commands.
  - [x] Implement comprehensive unit tests covering successful lock acquisition, releasing, context manager access, and concurrency violations.

- **Harden run_command return values and check-less error boundaries (Issue #445)**:
  - [x] Catch volume hydration command failures in `_sync_volume` in `ldm_core/handlers/snapshot.py` and log warnings.
  - [x] Write unit tests in `ldm_core/tests/test_snapshot.py` asserting correct error handling when `run_command` returns `None`.

- **Vanilla Liferay start option & guides (Colleague request)**:
  - [x] Add a `--vanilla` flag to `ldm run` and `ldm init` commands to bypass project seeding.
  - [x] Add a documentation guide `docs/how-to/vanilla_start.md` explaining fresh vanilla Liferay start configurations (database, volumes, versions).
  - [x] Update `README.md` and `docs/README.md` to link and document the vanilla run command.
  - [x] Write unit tests to cover the --vanilla flag.

- **Bypass SSL/certificate generation during .ldmp packaging and local builds**:
  - [x] Add a `--no-ssl` flag to `run-e2e-ldm.sh` in the AICA repository, allowing users and CI workflows to run LDM in plain HTTP mode and bypass local `mkcert` CA trust issues.
  - [x] Add `release_tag` input to `package-ldmp.yml` workflow_dispatch to allow manually rebuilding and attaching the `.ldmp` package to a specific release tag.
  - [x] Update `package-ldmp.yml` workflow to use `--no-ssl` and plain HTTP, and remove `mkcert` installation step.
  - [x] Update `package-ldmp.sh` in AICA to stage client extension zip files under `client-extensions/` instead of `deploy/` so LDM can scan and auto-deploy them.
  - [x] Update package-ldmp.yml to use <http://localhost:8080> instead of <http://aica-e2e.local> to bypass Traefik plain-HTTP routing limitations (Skipped: build failure was due to unmerged CodeMirror 6 bug).
  - [ ] Merge [PR #177](https://github.com/peterrichards-lr/liferay-ai-commerce-accelerator/pull/177) on AICA to resolve the CodeMirror 6 build failure, then rerun the manual LDM Package Release (.ldmp) workflow on master.

- Implemented Config Integrity & Validation (Pre-Flight Properties Analyzer) for Issue #127 (statically checks unclosed quotes, malformed JDBC URLs, conflicting database configs, and missing mount paths during properties rebuilding).

- **Fix compilation bypassing in generated GitHub Actions workflow (Issue #182)**:
  - [x] Update `cmd_init_ci` in `ldm_core/handlers/system.py` to add `setup-java` (JDK 21) and `setup-node` (Node.js 22) steps, and change environment start step to `ldm import . --non-interactive --build` to ensure workspace code is built and synced before packaging.

- **Implement resource inclusion metadata flags in snapshot/package manifest (Issue #183)**:
  - [x] Update `cmd_snapshot` in `ldm_core/handlers/snapshot.py` to dynamically check and write `includes_database`, `includes_volume_assets`, `includes_client_extensions`, and `includes_osgi_modules` keys in the snapshot `meta` file.

- **Interactive Configuration Management (Web Dashboard & TUI) for Issue #126**:
  - [x] Implement backend REST API endpoints for property edits and deletions in `ldm_core/dashboard/server.py`.
  - [x] Update frontend Properties Inspector drawer in `ldm_core/resources/dashboard/index.html` to support inline editing and additions.
  - [x] Add `--tui` / `-t` CLI option to `ldm config edit` command in `ldm_core/cli.py`.
  - [x] Implement TUI interactive configuration menu in `ldm_core/handlers/config.py`.
  - [x] Add unit tests for API endpoints and TUI logic.

- **Bypassing git clone for empty/vanilla .ldmp remote packages (Issue #160)**:
  - [x] Implement fallback to git clone when release package is <10KB.
  - [x] Remove duplicate quickstart execution in test suite.
  - [x] Run test suite and pre-commit checks to confirm everything is clean.
  - [x] Commit, push, and create PR for `bugfix/160-empty-ldmp-import-fallback` (PR #161, squash-merged).

- Released `v2.11.53` successfully (restored release PR workflow and resolved hypersonic workspace database restore bypass).
- Released `v2.11.52` successfully (immediate search reindexing on running containers via OSGi Gogo telnet command).
- Released `v2.11.46` successfully (upgraded Gitleaks hook to `v8.30.1` to resolve the Go 1.24 WASM panic in CI runners, and canceled hung jobs).
- Released `v2.11.45` failed during the CI/CD pipeline due to Gitleaks pre-commit hook panic under Go 1.24 (wasm invalid table access).
- Released `v2.11.43` successfully (implemented sequential properties override hierarchy (5-Layers) with CSS-style `# !important` precedence, CLI command overrides, and web dashboard diagnostics).
- Released `v2.11.42` successfully (resolved the GITHUB_ACTIONS env var root check in E2E tests).
- Released `v2.11.34` successfully (integrated automatic stop in non-interactive/yes mode and `--leave-running` option for workspace imports).
- Released `v2.11.33` successfully (integrated CWD home directory warning, `--stop-running` flag for import, and portable packaging documentation updates).
- Released `v2.11.31` successfully (integrated quickstart templates overrides, automatically start sharing under `ldm import`, standalone `ldmp` package exports, and test suite/pre-commit fixes).
- **Current Active Task (July 8, 2026)**:
  - [x] Create comprehensive mock unit test in `test_runtime.py` to verify Liferay Fragment `.ldmp` override patching using `urllib` Headless API.
  - [x] Tag and release LDM `v2.15.7` to publish the fragment override functionality.
  - [x] Fix CI validation workflow failure (`Sync Compatibility Table`) by removing deleted `INSTALLATION.md` pathspec.
  - [x] Change "Useful Commands" from detail to info so they display by default.

- **Current Active Task (July 8, 2026)**:
  - [x] Fix `Could not verify mounts automatically: 'LiferayManager' object has no attribute 'manager'` bug in `ldm_core/handlers/base.py` caused by recent inheritance refactoring.
  - [x] Correct mistaken `reindex_required` key regression in `flag_reindex`.
  - [x] Create PR #422 (`fix/mount-manager-error`) with the fix.
  - [x] Merge PR #422 and perform a patch release (`v2.15.12`).

- **Current Active Task (July 8, 2026)**:
  - [x] Investigate why LDM `v2.15.11` fragment overrides resulted in default values for AICA site.
  - [x] Refactor `_patch_fragment_overrides` loop in `ldm_core/handlers/runtime.py` to poll until fragments are found, waiting for Site Initializer completion.
  - [x] Create PR #423 (`fix/site-initializer-race-condition`) with the fix.
  - [ ] Merge PR #177 on `liferay-ai-commerce-accelerator` to fix its CI pipeline and repackage `.ldmp` release with the missing `fragment-overrides.json`.
  - [x] Merge PR #423 and PR #424 (Snapshot `.ldm` bug) and release.

- Released `v2.11.30` successfully (integrated Visual Diagnostics Web Dashboard (P3), Dynamic JVM Self-Tuning (P2), and solved GITHUB_ACTIONS env var mock test coverage mismatch).
- **Current Active Task (July 7, 2026)**:
  - [x] Fix Docker EADDRINUSE conflict for CX deployments exposing port 80/443 when running locally in `--no-ssl` mode. Always inject Traefik HTTP routing labels and safely dynamically shift conflicting direct host bindings.
  - [x] Fix GitHub Actions URL parsing bug where `parsed.netloc` was extracting the `x-access-token` hostname instead of `github.com`, causing `ldm package` to fail its `--repo` autodiscovery during CI pipelines.

- **Current Active Task (July 3, 2026)**:
  - [x] Create and push missing v2.11.85 tag to trigger CI/CD pipeline.
  - [x] Fix ldm infra-setup to dynamically resolve the database_mode config and automatically start liferay-db-global when shared mode is requested (PR #342).

- **Current Active Task (July 2, 2026)**:
  - [x] Implement Global Shared Database default fallbacks (Issue #225)
  - [x] Restore JIT Zip Repackaging for OAuth profile URLs (Issue #309)
  - [x] Fix documentation timestamps (Issue #301)
  - [x] Create PR #310 consolidating these features on master.

- **Current Active Task (July 1, 2026)**:
  - [x] Liferay Tunnel multiple port parsing support (`-ports 8080,3001` via `lcp.json`)
  - [x] Docker Share Host automatic Traefik multi-port proxy support.
  - [x] Infer `ssl=True` if `host_name` differs from localhost during `ldm import`.
  - [x] Dynamic injection of `LIFERAY_ROUTES_CLIENT_EXTENSION_...` for automatic Liferay -> Microservice routing.
  - [x] `routes` volume mount integration in core Liferay container.

- **Current Active Task (June 30, 2026)**:
  - [x] All 6 prioritized resource reduction & database mode issues have been fully implemented, tested, and committed to their respective feature branches.
  - [x] Pushed all 5 feature branches to remote origin:
    - `feat/230-docker-logging-limits`
    - `feat/227-db-connection-pool-limits`
    - `feat/228-elasticsearch-limits`
    - `feat/129-smart-cache-hydration` (contains both Issue #129 and Issue #225)
    - `feat/226-database-mode-subcommand`
  - [ ] Run the automated PR creation script (`python3 /Users/peterrichards/.gemini/antigravity-cli/brain/f6b11c7a-acde-4796-a5bc-5a712add681a/scratch/create_prs.py`) to create the 5 pull requests on GitHub using the updated GITHUB_PAT token loaded after terminal/IDE restart.

### Plan

1. **Sequential Property Overrides Hierarchy (5-Layers) with `!important` Precedence**:
   - [x] Sourcing layers in order of lowest to highest precedence:
     1. **Pre-warmed Seed** (built-in baseline `portal-ext.properties`)
     2. **`.ldmp` package overrides** (stored in `[project]/.liferay-docker/ldmp-portal-ext.properties` on import/restore)
     3. **Global Common properties** (`~/.ldm/common/portal-ext.properties`)
     4. **Local Workspace Common properties** (`[workspace]/common/portal-ext.properties`)
     5. **Project-level Customizations** (`[project]/files/portal-ext.properties`)
   - [x] Support CSS-style `# !important` overrides:
     - Properties marked with `# !important` or `!important` (preceding comment or inline comment) in any layer take priority over non-important properties.
     - If multiple layers mark the same property as `!important`, the highest layer in the hierarchy wins.
   - [x] Implement `.ldmp-portal-ext.properties` backup creation inside `[project]/.liferay-docker/` directory on `.ldmp` workspace import / snapshot restore.
   - [x] Save a backup of the initial properties as `[project]/.liferay-docker/original-portal-ext.properties` during project creation/import to support revert capabilities.
   - [x] Refactor `sync_common_assets` to read and merge properties dynamically from all active layers in precedence order with `!important` logic.
   - [x] Add CLI commands/switches for property management:
     - `--revert-properties`: Restore `files/portal-ext.properties` from `original-portal-ext.properties`.
     - `--reset-properties`: Discard project-level manual edits and rebuild purely from current active layers (Seed + LDMP + Global Common + Local Common).
     - `--rebuild-properties`: Reconstruct/sync the project's properties cleanly, preserving project customizations.
     - `--dry-run-properties` / `--dry-run`: Show how properties will be built/merged without writing to disk.
   - [x] Integrate a visual `Properties Inspector` in the Diagnostics Web Dashboard displaying:
     - The current active properties and their values.
     - The winning source layer name (color-coded).
     - The override cascade history (every layer's value/importance).
   - [x] Add unit tests verifying merging priority, `!important` overrides, and management switches.
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
9. **Auto-resolve Project Registry Collision in Non-Interactive/Yes Mode**:
   - [x] Allow `-y` / `--non-interactive` flag to automatically overwrite registry collisions (acting as auto-confirm).
   - [x] Update unit tests in `ldm_core/tests/test_base.py` to assert that non-interactive mode auto-overwrites.
10. **Validate pre-commit checks in release script**:
    - [x] Modify `scripts/release.py` to run `pre-commit run --all-files` before committing to catch non-Python format and lint failures.
11. **Automate tag creation after PR merge in release script**:
    - [x] Modify `scripts/release.py` to poll the PR merge status, checkout master, pull, and push the release tag automatically.
12. **Fix E2E verification scripts for project collision check**:
    - [x] Pipe "n" to `ldm run` instead of passing `-y` in `verify_e2e_refactor.sh` and `verify_e2e_refactor.ps1` to trigger the collision check error.
13. **Improve lfr-tunnel error reporting and log propagation**:
    - [x] Update `_poll_tunnel_health` in `ldm_core/handlers/share.py` to extract and print the local `~/.lfr-tunnel/client-<subdomain>.log` content on failure when using the native `lfr-tunnel` provider.
    - [x] Improve docker container log extraction in `_poll_tunnel_health` to print full registration error/instructions instead of only the first line.
    - [x] Ensure that on subprocess startup exit code 1, stderr is fully printed and client logs are read.
    - [x] Add unit tests verifying error output logic and log reading functionality. [Completed]
14. **Implement Unified Project Management & Automation Playbook**: Save `docs/PLAYBOOK.md`, add project-scoped rule to `.agents/AGENTS.md`, and align `bug_report.yml` and `feature_request.yml` templates with the playbook standard. [Completed]
15. **Sanitize Container Names Containing Spaces**:
    - [x] Update `ComposerService` to sanitize container names for Liferay, Database, and Tunnel sidecar services (e.g. replacing spaces with hyphens).
    - [x] Add unit tests in `ldm_core/tests/test_composer.py` to assert correct sanitization of space-separated container names (e.g., `Zukunft Digital` to `Zukunft-Digital`).
    - [x] Run test suite to verify success. [Completed]
16. **Implement Simple Log Grep & Level Filter Option**:
    - [x] Add CLI arguments `--grep` / `-g` for matching log lines to `ldm logs` parser in `ldm_core/cli.py`.
    - [x] Add CLI arguments `--level` / `-l` (with values: DEBUG, INFO, WARN, WARNING, ERROR, FATAL) to `ldm logs` parser in `ldm_core/cli.py`.
    - [x] Update `cmd_logs` in `ldm_core/handlers/runtime.py` to stream and filter the logs.
    - [x] Support `--grep-i` for case-insensitivity, and `--grep-v` for inverted matching.
    - [x] Support severity threshold filtering for `--level`, preserving stack traces (multi-line logs).
    - [x] Handle streaming/filtering safely for both follow (`-f`) and non-follow modes.
    - [x] Add unit tests verifying log filtering logic. [Completed]
17. **Fix Cloud Hydration & DB Restore Issues (Tim's Colleague Report)**:
    - [x] Implement the missing `prompt_for_tag` method in `AssetService` to prompt the user interactively when `--tag` is not passed during hydration.
    - [x] Sanitize resolved database container names in `snapshot.py` via `sanitize_id` to replace spaces with hyphens (e.g. `zukunft-digital-db`).
    - [x] Add double quotes around the `db_container` variable in database restore shell commands to prevent command parameter split failures.
    - [x] Add corresponding unit tests in `test_assets.py` and `test_snapshot.py` to verify prompt behavior and container quoting. [Completed]
18. **Secure Liferay Tunnel PAT Documentation**:
    - [x] Add a detailed guide on securing LFT_CLIENT_TOKEN (Restricted Secrets File and OS Credential Manager alternatives) to `docs/how-to/sharing_tunnels.md`. [Completed]
19. **Extend LDM MCP Server**:
    - [x] Add `grep` and `level` log filtering support to the `get_logs` FastMCP tool in `ldm_core/handlers/mcp.py`.
    - [x] Expose container lifecycle control FastMCP tools: `start_project`, `stop_project`, and `restart_project`.
    - [x] Add unit tests verifying the new FastMCP tools and parameters.
20. **Prevent LDM AI Command Hallucinations**:
    - [x] Add `get_cli_help` FastMCP tool in `ldm_core/handlers/mcp.py` to retrieve LDM CLI usage.
    - [x] Dynamically inject host operating system metadata into `system_instruction` in `ldm_core/handlers/ai.py`.
    - [x] Update `system_instruction` in `ldm_core/handlers/ai.py` to command the model to validate CLI commands against `get_cli_help`.
    - [x] Add unit tests for `get_cli_help` tool in `ldm_core/tests/test_mcp.py`.
    - [x] Update `docs/how-to/ai_mcp_guide.md` to document the `get_cli_help` tool. [Completed]
21. **Fix safe_rmtree Windows read-only file permission errors**:
    - [x] Update `safe_rmtree` in `ldm_core/utils.py` to handle read-only files by passing an `onerror` handler to `shutil.rmtree` on Windows.
    - [x] Add a unit test in `ldm_core/tests/test_utils.py` to verify deletion of read-only files.
22. **Restore Home Directory CWD Warning**:
    - [x] Update `detect_project_path` in `ldm_core/handlers/base.py` to use `BaseHandler._warned_home` instead of `self.manager._warned_home` to avoid attribute crashes that suppress the warning.
    - [x] Add a unit test verifying that the home directory CWD warning is triggered.
23. **Create Unit Tests for System Services (Nuke and Rescue)**:
    - [x] Create `ldm_core/tests/test_system.py` to test system commands.
    - [x] Verify `nuke` behavior (forced, aborted config deletion).
    - [x] Verify `rescue` behavior (global, project-specific, lockfile cleanup).
24. **Optimize Hydration Prompts (Avoid Redundant Project/Version Selects)**:
    - [x] Resolve project_id early in `cmd_hydrate` and propagate it to sub-calls (`hydrate_cloud_backup`, `cmd_restore`, `cmd_reset`, `cmd_stop`, `cmd_run`).
    - [x] Avoid prompting for version/tag on existing project hydration.
    - [x] Add unit tests verifying prompt/version bypass behavior.
25. **Fix safe_rmtree permission propagation & reclaim_volume_permissions UID/GID dynamics**:
    - [x] Update `safe_rmtree` in `ldm_core/utils.py` to propagate exceptions in `remove_readonly` if deletion fails on retry, allowing JIT permission reclamation to trigger.
    - [x] Dynamically resolve `uid` and `gid` in `reclaim_volume_permissions` to the current user's UID/GID (using `os.getuid()` / `os.getgid()`) when on Unix platforms.
    - [x] Add unit tests in `ldm_core/tests/test_utils.py` to verify this behavior.
26. **Implement properties syntax auto-repair in ldm rescue**:
    - [x] Update `cmd_rescue` in `ldm_core/handlers/system.py` to check and auto-repair broken trailing backslash continuations in `portal-ext.properties` files.
    - [x] Add corresponding unit tests in `ldm_core/tests/test_system.py` to verify self-healing properties rescue functionality.
27. **Clone-Bypassing `.ldmp` Workspace Import**:
    - [x] Add `--clone-only` CLI flag to `ldm import` command to force standard clone behavior.
    - [x] Implement remote checking for `.ldmp` package in GitHub Releases when importing from remote Git URL.
    - [x] Bypass cloning and pre-seeding when `.ldmp` is found, directly downloading and restoring it.
    - [x] Fix failing unit tests in `test_workspace.py` (releases API mock setup and missing release fallback validation).

28. **Resolve Workspace/CLI Liferay version tags using official releases.json mapping**:
    - [x] Implement `resolve_liferay_docker_tag` in `ldm_core/utils.py` to match partial/product versions (e.g. `2026.q1.7` or `dxp-2026.q1.7`) against `releases.json` and return the correct Docker image tag (e.g. `2026.q1.7-lts`).
    - [x] Update `workspace_root` Liferay Workspace version resolution in `ldm_core/handlers/workspace.py` to use `resolve_liferay_docker_tag`.
    - [x] Update CLI/meta version parsing in `ldm_core/handlers/runtime.py` to also resolve user-supplied or meta-defined tags to their official Docker tags.
    - [x] Add unit tests verifying the version resolution and caching logic.

29. **Enhance 'ldm reindex' to Support Immediate Runtime Reindexing**:
    - [x] Add CLI arguments `--force-boot` / `--reboot` in `ldm_core/cli.py`.
    - [x] Update `cmd_reindex` in `ldm_core/handlers/runtime.py` to check container state.
    - [x] Implement telnet Gogo execution helper to trigger immediate reindex on running containers.
    - [x] Add unit tests verifying runtime immediate reindexing and fallback reboot options.

30. **Simplify release.py script**:
    - [x] Restrict release initiation to master branch.
    - [x] Stage, commit, and tag directly on master (Failed due to master branch protection rules).
    - [x] Restore release branch creation and PR workflows to comply with branch protection.
    - [x] Commit script updates to a feature branch, raise PR, auto-merge.

31. **Fix CodeQL stack trace exposure vulnerabilities (py/stack-trace-exposure)**:
    - [x] Catch Exception in dashboard API endpoints (`api_project_properties`, `api_update_project_property`, `api_delete_project_property`) and log them on the server while returning sanitized/safe messages to the client.
    - [x] Add corresponding unit tests in `ldm_core/tests/test_dashboard.py` to verify that these endpoints correctly return sanitized/safe error messages on failure.

32. **Implement ldm config ssl-mode command (Issue #165)**:
    - [x] Add CLI parser logic in `cli.py` for `ldm config ssl-mode [hosts|share]`.
    - [x] Implement `cmd_ssl_mode` in `config.py` supporting swapping properties and syncing client extension `.env` files.
    - [x] Add unit tests in `test_config.py` verifying correct functionality.
    - [x] Add documentation for `ssl-mode` in `docs/how-to/sharing_tunnels.md`.

33. **Implement --no-home-warn flag and config option**:
    - [x] Add global CLI flag `--no-home-warn` in `ldm_core/cli.py` to suppress home directory CWD warnings.
    - [x] Support `no_home_warn` config property in `~/.ldmrc` (using defaults manager or direct config check).
    - [x] Update `detect_project_path` in `ldm_core/handlers/base.py` to check both CLI flag and config defaults before printing the warning.
    - [x] Add unit tests verifying warning suppression when the flag or the config is active.

34. **Fix environment variable pollution in test_share.py**:
    - [x] Use `clear=True` in `patch.dict(os.environ)` to prevent host `LFT_CLIENT_TOKEN` from failing token priority tests.

35. **Tear down conflicting stack on project registry collision overwrite (Issue #178)**:
    - [x] Update `check_registry_collisions` in `ldm_core/handlers/base.py` to stop/down the old project stack if `overwrite` is `True` and `docker-compose.yml` exists.
    - [x] Update unit tests in `ldm_core/tests/test_base.py` to assert that `run_command` is called with `down` when overwriting project registry entries.
36. **Implement Uncommitted Git Changes Protection (#170)**:
    - [x] Create `check_uncommitted_changes` in `ldm_core/handlers/base.py` running git status porcelain checks.
    - [x] Trigger checks in `cmd_import` and `cmd_restore` handlers, prompting or requiring `--force` if dirty.
    - [x] Add unit tests verifying git status warning and force override behavior.

37. **Implement Pre-Flight Network & Port Collision Checks (#172)**:
    - [x] Add port conflict parsing and checking logic in `cmd_run` before executing docker compose up.
    - [x] Loop over exposed host ports in `docker-compose.yml` and check availability if container is not running.
    - [x] Add global `--force` flag with `-f` option resolving parser conflicts via `conflict_handler="resolve"` for subcommands.
    - [x] Add unit tests verifying port collision detection and clean halting.

38. **Implement Colorless (--no-color) and ASCII (--no-unicode) Switches (#181)**:
    - [x] Add `--no-color` and `--no-unicode` / `--ascii` to CLI parsers (`base_parent`, `base_sub_parent`).
    - [x] Declare `"no_color": "false"` and `"no_unicode": "false"` defaults in `CONVENTION_DEFAULTS`.
    - [x] Resolve settings from CLI arguments, config files, and environment variables (`NO_COLOR`, `LDM_NO_COLOR`, `LDM_NO_UNICODE`) in `LiferayManager.__init__`.
    - [x] Add regex-based ANSI escape sequence stripping in `UI._print` and `UI.ask` when `UI.NO_COLOR` is active.
    - [x] Add ASCII character fallback forcing in `UI._print` and `UI.ask`, and character replacements for `UI.Spinner` when `UI.NO_UNICODE` is active.
    - [x] Add unit tests verifying colorless and ASCII formatting output.
39. **Support downgrades and targeting specific versions in ldm system upgrade (Issue #179)**:
    - [x] Add `conflict_handler="resolve"` to `upgrade` parser in `ldm_core/cli.py` and add `--version` option.
    - [x] Update `check_for_updates` in `ldm_core/utils.py` to support specific version tag checks.
    - [x] Update `cmd_upgrade` in `ldm_core/handlers/diagnostics.py` to support validation, downgrade checks, warnings, and confirm prompts.
    - [x] Add unit tests in `ldm_core/tests/test_diagnostics.py` to verify formatting, downgrade behavior, prompts, and force flag requirements.

40. **Support waiting for asynchronous OSGi and Client Extension deployments in ldm wait (Issue #186)**:
    - [x] Add CLI arguments `--wait-for-deployables` and `--wait-for-bundles` to the `wait` parser in `ldm_core/cli.py`.
    - [x] Implement local scan logic to detect bundle Symbolic Names from manifests and client extensions from YAML configs in `ldm_core/handlers/runtime.py`.
    - [x] Implement deployable directory check and OSGi Gogo shell poller in `cmd_wait` inside `ldm_core/handlers/runtime.py`.
    - [x] Fix unit tests in `ldm_core/tests/test_runtime.py` to resolve StopIteration on time.time() mock due to mock exhaustion/extra checks.
41. **Scaffold GitHub Actions workflow for LDM package releases (Issue #187 & Issue #188)**:
    - [x] Register `system init-ci` subcommand and its arguments (`--repo`, `--workflow-name`, `--trigger`, `project`) in `ldm_core/cli.py`.
    - [x] Add `--snapshot` argument to `package` parser in `ldm_core/cli.py` (Issue #188).
    - [x] Define default `"ci_trigger": "release"` in `CONVENTION_DEFAULTS` inside `ldm_core/defaults.py` to allow global configuration overrides.
    - [x] Implement `cmd_init_ci` in `ldm_core/handlers/system.py` to auto-detect git remotes and generate a customized release workflow YAML.
    - [x] Enhance `cmd_package` in `ldm_core/handlers/snapshot.py` to create the output directory automatically and resolve specific snapshot targets (Issue #188).
    - [x] Add unit tests verifying `init-ci` execution and targeted snapshot packaging logic.
42. **Reorganize LDM documentation index (Issue #189)**:
    - [x] Update `docs/README.md` to categorize documentation into Core Reference, Operational Guides, and Developer Guides.
    - [x] Add missing links to recently introduced documentation (Import & Packaging, properties hierarchy, AI MCP guide, advanced CLI options, playbook, compatibility, etc.).
    - [x] Verify that all links are correct and the markdown file passes pre-commit checks.
43. **Fix diagnostics upgrade/downgrade unit tests**:
    - [x] Patch `sys.argv` in `test_upgrade_downgrade_non_interactive_with_force` and `test_upgrade_downgrade_interactive_confirm` to ensure the `.py` suffix check triggers properly when running under `py.test` / venv.

44. **Restructure documentation entry points**:
    - [x] Create a root `README.md` with a clean, concise introduction, macOS/Linux quick installation, quick start commands, and signposts.
    - [x] Move detailed Conventions and Key Features out of the main index to `docs/explanation/conventions.md`.
    - [x] Simplify `docs/README.md` to be a categorized table of contents and documentation index, removing duplicate sections.
    - [x] Run pre-commit checks to verify markdown formatting and link integrity.

45. **Enhance ldm ps / status command with detailed view and exit codes**:
    - [x] Add `-d` / `--detailed` option to the `status` parser in `ldm_core/cli.py`.
    - [x] Update `cmd_status` in `ldm_core/handlers/diagnostics.py` to support `detailed` container listing.
    - [x] Update `cmd_status` to exit with code `0` if the specified project is running, and `1` if stopped or not found.
    - [x] Add unit tests verifying detailed output format and specific exit codes.

46. **Add Unit Tests for Snapshot Component Lists and Metadata Tracking (Issue #197)**:
    - [x] Fix NameError bug in `snapshot.py` line 509 by replacing the global `run_command` call with `self.manager.run_command`.
    - [x] Add `test_cmd_snapshot_component_lists` in `ldm_core/tests/test_snapshot.py` to assert correct discovery/metadata writing for client extensions, OSGi modules, and active services.
    - [x] Run full test suite and pre-commit checks.

47. **Update CONTRIBUTING.md with Structured Issue Tracking and PR Linking Workflow**:
    - [x] Update `CONTRIBUTING.md` to make issue creation mandatory, define the plan/review steps, and document branching and commit auto-close conventions.

48. **Update AICA package-ldmp.sh to Dynamically Generate Component Metadata**:
    - [x] Update `scripts/package-ldmp.sh` in the `liferay-ai-commerce-accelerator` repository to extract lists of staging zip/jar files and active services, writing them to the `meta` file.
    - [x] Resolve CodeMirror 6 esbuild bundling regression by downgrading package.json/yarn.lock to CodeMirror 5.65.16.
    - [x] Add dependabot ignore rules for `codemirror` and add `./gradlew build -x test` compilation step to CI workflow to prevent future bundling regressions.

49. **Strict Python Dependency Hash Verification (Issue #176)**:
    - [x] Implement `_verify_dependency_integrity(package_name)` in `DiagnosticsService` to locate the dist-info metadata RECORD file and verify SHA-256 hashes of all python/critical source files.
    - [x] Add virtual environment dependency diagnostics to `DoctorRunner` in `diagnostics.py`.
    - [x] Add pytest unit tests in `ldm_core/tests/test_diagnostics.py` to cover mismatch detection, missing packages, and missing file warnings.

50. **Fix SSL Certificate Sync Lag for Docker Desktop on Windows/macOS (Issue #204)**:
    - [x] Implement `new_files_written` tracking in `setup_ssl` (inside `infra.py`).
    - [x] Add a 2-second synchronization delay in `setup_ssl` when new SSL certificates or Traefik configs are generated.
    - [x] Ensure the delay is only triggered on Windows, macOS, or WSL to compensate for hypervisor/gRPC-FUSE/VirtioFS filesystem sync lag.
    - [x] Update unit tests in `ldm_core/tests/test_stack.py` to assert that `time.sleep(2)` is called.

51. **Guardrail: Safe Downgrade Prevention for Liferay/PostgreSQL Versions (Issue #169)**:
    - [x] Register `--force-downgrade` command line option globally in `base_sub_parent`.
    - [x] Add Liferay/PostgreSQL version downgrade validation at start of `sync_stack` in `runtime.py`.
    - [x] Save successfully run Liferay/PostgreSQL versions to project metadata before bringing up containers.
    - [x] Create comprehensive unit tests in `test_downgrade.py` verifying detection, force flag, and metadata update.

52. **Guardrail: Automated CLI Arguments & Documentation Drift Detection (Issue #174)**:
    - [x] Create introspection script `scripts/check_cli_drift.py` that extracts all CLI option strings from `get_parser()`.
    - [x] Compare CLI option strings against documentation files (`docs/guides/CLI_REFERENCE.md`) and fail if undocumented.
    - [x] Add the drift check to pre-commit hook config.
    - [x] Add pytest unit tests for the drift checking logic.

53. **Guardrail: Pre-Flight System Resource & Disk Space Checks for Hydration (Issue #168)**:
    - [x] Implement available free disk space check before extracting snapshot archives.
    - [x] Raise a descriptive error and abort execution if free space is less than 1.5x the compressed archive size.
    - [x] Add pytest unit tests verifying pre-flight check failure and success pathways.

54. **Guardrail: Liferay database auto-upgrade options on version changes (Issue #209)**:
    - [x] Detect Liferay version upgrades by comparing current tag with `last_run_liferay_version` in `sync_stack`.
    - [x] Add CLI flags `--upgrade-db`, `--no-upgrade-db`, `--backup-on-upgrade`, and `--no-backup-on-upgrade`.
    - [x] Offer automated database backup snapshot and auto-upgrade options interactively.
    - [x] Inject `LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true` environment variable if auto-upgrade is enabled.
    - [x] Add test suite `ldm_core/tests/test_upgrade.py` verifying all upgrade conditions.
    - [x] Document options in `docs/guides/CLI_REFERENCE.md`.

55. **Guardrail: Two-Way CLI Introspection & Documentation Drift Check (Issue #204)**:
    - [x] Extend `verify_cli_drift` in `ldm_core/utils.py` to check that documented options in markdown files exist in the parser.
    - [x] Automatically catch and fail on any stale, renamed, deprecated, or removed CLI options that are still listed in documentation.
    - [x] Add pytest unit tests verifying detection of stale/removed options.

56. **Guardrail: Mandatory `--dry-run` Support for Destructive Handlers (Issue #173)**:
    - [x] Implement `--dry-run` short-circuiting in `cmd_nuke` and `cmd_rescue` (in `system.py`).
    - [x] Implement `--dry-run` check and logging in `cmd_prune` (in `diagnostics.py`).
    - [x] Implement `--dry-run` short-circuiting in `cmd_down`, `cmd_reset`, and `cmd_reseed` (in `runtime.py`).
    - [x] Add comprehensive unit tests verifying dry-run actions for nuke, rescue, prune, down, reset, and reseed without making any modifications.
    - [x] Ensure all unit tests, pre-commit checks, and style guidelines pass.

57. **Guardrail: AI Action Circuit Breaker & Rate Limiting for MCP Server (Issue #171)**:
    - [x] Define global variables for tracking mutation timestamps and tripped status in `ldm_core/handlers/mcp.py`.
    - [x] Implement sliding window rate limit handler `_check_circuit_breaker()` resolving limit and window values from environment variables.
    - [x] Secure mutating MCP tools `start_project`, `stop_project`, and `restart_project` with circuit breaker guards while allowing diagnostic tool calls to function.
    - [x] Add comprehensive unit tests verifying rate limits, custom threshold/window configurations, and tripped state lockouts.

58. **Guardrail: Scope Sprawl Protection and Anti-Churn Mandate (Issue #175)**:
    - [x] Implement GitHub Actions workflow job `pr-sprawl-check` in `ci.yml` that triggers on pull requests to verify change sizing.
    - [x] Ensure changed file limits are gated strictly for bugfixes (branches starting with `fix/` / `bugfix/` or PR title starts with `fix:` / `bugfix:`) exceeding 10 files.
    - [x] Support bypass override keywords (`[bypass sprawl]` or `[bypass limit]`) in the PR title or description to allow manual bypass.
    - [x] Update `CONTRIBUTING.md` to document PR sprawl guardrails, atomic limitations, and bypass keywords.
    - [x] Update project-scoped rules of engagement `.agents/AGENTS.md` to enforce the mandate for AI development.

59. **Smart Cache & Hydration Optimization (Issue #129)** [Completed]:
    - [x] Add hash-based file change detection for heavy volume document library archives (`volume.tgz`) during snapshot/cloud hydration restores.
    - [x] Cache volume hashes in target folder `.ldm_volume.sha256` to skip identical redundant extractions.
    - [x] Add unit tests verifying hash checking, skip extraction logic, and hash file output.

60. **Shared Global Docker Database Containers (Issue #225)** [Completed]:
    - [x] Implement global database initialization `liferay-db-global` (PostgreSQL) inside Traefik SSL Proxy network stack.
    - [x] Omit local isolated `db` container definitions in Compose setup when running in shared database mode.
    - [x] Override JDBC connection URL to point Liferay to namespaced database target schema on `liferay-db-global`.
    - [x] Verify and dynamically create namespaced target database on stack boot.
    - [x] Update snapshot database dump/restore commands to target namespaced database name on `liferay-db-global`.
    - [x] Add unit tests verifying shared database compose and global database setup logic.

61. **Implement Docker logging size and rotation limits for LDM projects (Issue #230)** [Completed]:
    - [x] Add log driver limit defaults (max-size: 10m, max-file: 3) to `ComposerService` in `ldm_core/handlers/composer.py`.
    - [x] Support global overrides `log_max_size` and `log_max_file` in `CONVENTION_DEFAULTS` inside `defaults.py` to allow custom logging rotation.
    - [x] Update unit tests in `ldm_core/tests/test_composer.py` to assert that generated services have correct logging config blocks.

62. **Optimize local database connection pool sizes for Liferay stacks (Issue #227)** [Completed]:
    - [x] Limit default connection pool settings in database properties builder (maxActive=15, minIdle=2, maxIdle=5).
    - [x] Support config overrides `db_max_active`, `db_min_idle`, and `db_max_idle` in `CONVENTION_DEFAULTS` inside `defaults.py`.
    - [x] Add unit tests verifying database properties are output with these limits.

63. **Reduce global Elasticsearch container default memory and thread limit (Issue #228)** [Completed]:
    - [x] Lower default global Elasticsearch memory footprint to 512MB (-Xms512m -Xmx512m).
    - [x] Support config overrides `elasticsearch_heap_size` in global defaults configuration.
    - [x] Inject `-e "processors=1"` inside global search initialization to limit CPU thread consumption.
    - [x] Add unit tests verifying global search initialization options.

64. **CLI Defaults & `database-mode` Subcommand (Issue #226)** [Completed]:
    - [x] Add `ldm config database-mode [isolated|shared]` subcommand to get/set local database profile.
    - [x] Support `--global` flag to configure the default database profile globally.
    - [x] Enforce validation options (choices: isolated, shared) and update subcommand preprocessing.
    - [x] Add unit tests verifying config database-mode subcommand get, local set, and global set actions.

65. **Implement Native Project Fork Command (Issue #252 & Issue #253)** [Completed]:
    - [x] Register `fork` subcommand and arguments in `ldm_core/cli.py`.
    - [x] Implement `cmd_fork` in `ldm_core/handlers/workspace.py` providing snapshot-based duplication, automatic container and hostname namespacing, and free port allocation.
    - [x] Add unit tests verifying metadata mutations, port auto-resolutions, and restore calls in `test_workspace.py`.

66. **Codebase Review & Refactoring for Technical Debt (Issue #258)** [Completed]:
    - [x] Perform codebase audit and generate pre-audit coverage report.
    - [x] Refactor duplicate subprocess execution branches in `_run_lcp_cmd` to streamline Liferay Cloud command execution.
    - [x] Document codebase conventions and command execution rules in `DEVELOPMENT.md`.
    - [x] Run E2E smoke tests and pre-commit checks to verify formatting, linters, coverage, and tests pass cleanly.

## Client Extension Routing Rules

When modifying `client-extension.yaml` files, **NEVER change or remove `.serviceAddress: localhost:3001` or `.serviceScheme`** manually to fix Docker or LDM routing issues. Liferay automatically updates the shared routes context with the correct internal endpoint when the generated `.zip` file is copied to the Liferay `osgi/client-extensions` deploy folder. Modifying these properties will override the auto-registration and break the deployment.

- **Current Active Task (July 6, 2026)**:
  - [x] Migrate `ldm-cx-samples` delivery to `.ldmp` package file.
  - [x] Create GitHub Actions CI/CD to build `.ldmp` from `ldm-cx-samples`.
  - [x] Update LDM `AssetService` to read `.ldmp` for `--samples`.
  - [x] Create an E2E test in `ldm-cx-samples` using `--samples` switch.

- **Current Active Task (July 7, 2026)**:
  - [x] Fix Java version validation (Issue #370) to allow JDK 21+.
  - [x] Review redundant `init-local-env.sh`, `package-ldmp.sh` bash scripts, replaced by native `ldm hydrate` (Issue #371) and `ldm package` (Issue #372).
  - [x] Implement native `ldm set-version` (Issue #373) to mutate `gradle.properties` natively.
  - [x] Execute automated release script to cut `v2.15.0` version bump.

- **Current Active Task (July 9, 2026)**:
  - [x] Create Technical Debt issues on GitHub using `gh` CLI.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-13* | *Last Reviewed: 2026-07-10*
