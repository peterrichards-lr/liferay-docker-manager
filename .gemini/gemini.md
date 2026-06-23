# Gemini Rules of Engagement

> [!NOTE]
> **Purpose of this file**: This file defines the **Agent Rules of Engagement & Memories** specifically for AI coding assistants (like Gemini/Antigravity) operating in this repository. It tracks workflow guidelines, verification gates, and persistent task memories. Human developers do not need to edit this file.

--- Context from: /users/peterrichards/.gemini/gemini.md ---

## 1. Permission, Scope & Workflow

- **NO multi-file edits** in a single turn without a pre-approved written plan.
- **Atomic Changes**: Break down complex tasks into small, logical units. Do not move to "Step 2" until "Step 1" is verified.
- **Visual Confirmation**: Always use the VS Code Diff view to present changes before applying them.
- **Logic-First Planning**: For any function or logic block >10 lines, output a `<plan>` tag with the step-by-step algorithm. Wait for a "Proceed" command before writing code.
- **Virtual Environment Execution**: The agent MUST run tests, linters, and git commit operations with the Python virtual environment (`.venv`) activated, or explicitly invoke the binaries inside `.venv/bin/` to prevent dependency mismatch and pre-commit hook failures on host environments.

## 2. Code Quality, Architecture & Deduplication

- **Prioritize DRY (Don't Repeat Yourself)**: Before creating a new function/helper, search the `@codebase` for existing utilities.
- **Refactor over Duplicate**: If redundant code is found, suggest a refactor into a shared service or utility rather than creating a new one.
- **Predictive Failure**: For every implementation, list two potential failure points (edge cases or performance) and how they were handled.
- **Predictable Layers**: Ensure logic stays within its designated layer (e.g., UI vs. Business Logic vs. Data) as defined in `spec.md`.
- **Hybrid Volume Strategy**: Always use **Docker Named Volumes** for lock-sensitive directories (`data/`, `osgi/state/`) on macOS and external ExFAT storage. Use **Host Bind-Mounts** for directories requiring developer interaction (`deploy/`, `modules/`, `client-extensions/`). This ensures robust POSIX file locking while maintaining a smooth DX.
- **macOS Sync Wait**: When writing high volumes of files to the host (e.g. during backup extraction), LDM MUST wait at least 2 seconds before mounting those folders into a container. This prevents race conditions where the macOS VirtioFS driver reports a successful write to the host OS before the files are physically available to the Docker hypervisor.
- **macOS VirtioFS Resilience**: When writing a large number of files to the host (e.g. during snapshot extraction) that must be immediately visible to a Docker container, you MUST implement a **Sync Wait** (minimum 2 seconds). On macOS, the Docker hypervisor (VirtioFS/gRPC-FUSE) lags behind the host OS's file creation; failing to wait results in empty directories inside the container.
- **Script Parity**: All cross-platform utility, verification, and wrapper scripts (e.g., `.sh` vs `.ps1` or `.bat`) MUST be kept in perfect functional parity. Any hardening check or logic update applied to one must be immediately synchronized to the others.
- **Terminal UI Integrity**: When implementing long-running operations with spinners, you MUST use the `\033[K` ANSI code to clear the terminal line before each update to prevent character bleed. Truncation must be whitespace-aware to prevent word cutting.

## 3. Testing & Hard-Gates

- **Unit Test Requirement**: All new logic must have corresponding unit tests.
- **Coverage Expansion**: For every change, review existing tests (unit, e2e, smoke, etc.) and determine if they need to be updated or expanded to increase overall coverage.
- **The Deployment Gate**: You must never suggest a deployment command until you have explicitly asked me to confirm that all unit tests are passing.
- **Test-Driven Alignment**: Propose test cases *before* providing the implementation.

## 4. Collaborative Learning

- **Root Cause Analysis**: Explain the "Why" and "How" of every fix.
- **Architectural Check-in**: Confirm alignment with my mental model.

## 5. Liferay Client Extension (CX) Standards

- **YAML Integrity**: Cross-reference all code with `client-extension.yaml`.
- **Oauth2 & Context**: Use `Liferay.authToken`. No hardcoded credentials.
- **Workspace Awareness**: Respect `[workspace-root]/client-extensions/`.

## 6. Post-Completion "Definition of Done"

- **Test Protocol**: Provide 3-5 manual/automated steps to verify in a live Liferay instance.
- **Redundancy Scan**: After a feature is complete, scan for any newly introduced duplicate code.

## 7. Strategic Deployment Control

- **No Automatic Deploy**: Do not run or suggest `deploy` tasks as part of a general "build" command.
- **Dependency Awareness**: Before deployment, list the required order of execution (e.g., 1. OAuth2 CX, 2. Batch CX for Objects, 3. Frontend Custom Element).
- **Manual Trigger**: Always end a feature cycle by asking: "The code is ready and tested. Would you like me to provide the specific build/deploy commands for this extension now?"

## 8. Git Management & Branching Strategy

- **Logical Squashing**: Avoid creating a commit for every minor bugfix. Group related fixes and features into single, descriptive commits.
- **Release Automation**:
  - **Stable Strategy**: Use `[release]` in the commit summary to trigger a stable GitHub Release. This MUST be reserved for hardened features and verified bugfixes.
  - **Pre-Release Strategy**: Use `[pre-release]` in the commit summary to trigger a Beta/Test build (e.g., `v2.10.x-pre.y`). All pre-release tags (`v*.pre*`) MUST be created and pushed directly on their respective development/feature branches, never on `master`.
  - **Experimental Mandate**: All brand new or experimental functionality (specifically the **Liferay Cloud Golden Path** / `ldm import from cloud`) MUST use pre-releases until a full E2E verification is completed by the user.
  - **Bumping**: Use `./ldm version --bump pre` to start or increment a pre-release cycle, and `./ldm version --promote` to convert a successful pre-release to a stable version.
  - Ensure version tags match the `VERSION` in `ldm_core/constants.py`.
- **Branching Separation**:
  - **`master` / `main`**: Strictly for environmental hardening, stable maintenance, and verified hotfixes.
  - **Roadmap Items**: All large roadmap items, complex features, or experimental refactors MUST be developed in dedicated feature branches (e.g., `roadmap/feature-name`).
  - **Branch Independence**: Roadmap branches MUST remain independent of each other. Never merge one roadmap branch into another. This ensures they can be verified and merged into `master` in any order.
  - **Active Sync**: While a roadmap branch is active, it MUST be periodically synchronized with the latest changes from `master` (via rebase or merge). This prevents code from going stale and minimizes future merge friction.
  - **Explicit Merge**: Roadmap branches MUST NOT be merged into `master` until full verification is complete and the user has provided an explicit request to merge.
  - **Cleanup**: Delete feature branches immediately after a successful merge to `master`.
- **Pre-Change State Persistence**: Before performing ANY file modification (code, documentation, or configuration), I MUST update the `Gemini Added Memories` section of this file with a detailed `<plan>` or task summary to ensure state is persisted in case of an interruption.

## Gemini Added Memories

- **Status: Liferay Cloud Golden Path & Demo Rescue (v2.10.x)**
  - [x] **Workspace Recognition**: Built heuristics into `ldm init-from` and `ldm import` to detect Liferay Cloud repositories automatically.
  - [x] **Guided Hydration**: Implemented an interactive wizard (and `--hydrate-from` automation switch) to orchestrate data and env-var synchronization.
  - [x] **Robust Ingestion**: Engineered a "Greedy Regex" parser to reliably extract Backup IDs from the LCP CLI across various OS/terminal environments.
  - [x] **Post-Download Organization**: Built logic to automatically recursively flatten LCP's nested UUID backup structures into standard LDM snapshots.
  - [x] **License Awareness**: Implemented expiration-aware license syncing to ensure valid developer licenses aggressively overwrite expired Cloud trial licenses.
  - [x] **UI Hardening**: Re-engineered the `Spinner` engine with ANSI `\033[K` line-wiping and terminal-aware whitespace truncation.
  - [x] **Automation Standardization**: Established a project-wide exit code contract (0-4, 126) and enforced non-interactive `-y` modes for all developer utilities.
  - [x] **PaaS Hydration Edge-Cases Resolved**:
    - **SQL Scrubbing**: Removed proprietary `\restrict` and `\unrestrict` meta-commands from LCP dumps to prevent `ON_ERROR_STOP=1` import failures.
    - **PostgreSQL Clean Slate**: Implemented a comprehensive `DO` block wipe loop (including `DELETE FROM pg_largeobject_metadata`) to guarantee a collision-free import environment without requiring the missing `postgres` superuser.
    - **PostgreSQL Socket Race**: Added `no such file or directory` to the silent retry loop in `_wipe_db`, ensuring LDM waits for the Docker Unix socket to appear before attempting schema wipes.
    - **Volume Permissions & Sync**: Replaced Alpine `cp` with a robust `tar` pipeline and forced `chown -R 1000:1000` to guarantee hidden file preservation and correct Liferay read permissions across the VM boundary.
    - **Virtual Host Override**: Enforced aggressive `UPDATE virtualhost` execution to map the local hostname dynamically.
    - **Robust Volume Hydration**: Re-engineered `cmd_restore` to synchronously populate the host project folder FIRST, followed by a mandatory 2-second **Sync Wait** to allow the macOS hypervisor (VirtioFS) to catch up, before finally pushing the confirmed host data into Docker Named Volumes via a `tar` pipeline.
    - **Volume Naming Consistency**: Implemented explicit naming in `ComposerService` to prevent Docker Compose from prefixing volumes with the project name (e.g. `modern-intranet_modern-intranet-data`), resolving critical hydration mismatches where LDM filled a "ghost" volume.
    - **Smart Store Detection**: Implemented automatic detection of `FileSystemStore` (simplified paths) vs `AdvancedFileSystemStore` (nested repositoryId paths) during Cloud imports to prevent document library 404s.
  - [x] **Self-Tuning JVM & Proactive Monitoring**:
    - **Auto-Tuning**: Built a resource scaling engine in `ComposerService` that automatically disables `TieredStopAtLevel=1` and increases `ReservedCodeCacheSize` during reindexing to prevent `CodeCache` exhaustion.
    - **Proactive Reindexing**: Integrated real-time log parsing into the primary startup spinner to report reindex progress and block the "Ready" signal until completion.
    - **Shared Engine**: Centralized reindex triggering in `BaseHandler.flag_reindex`, enabling the new `ldm reindex` command and `ldm run --reindex` flag.
  - [x] **UI & UX Refinement**:
    - **Consolidated Startup**: Unified redundant "Starting stack" lines into a single professional message with `BYELLOW` styling.
    - **macOS PTY Safety**: Refactored binary `sudo` updates to use `os.system` instead of Python subprocesses, permanently fixing the `unable to allocate pty` error on macOS terminal environments.
  - [x] **Git History Cleanup & Secrets Purge (v2.10.x)**
    - [x] **Secrets & Database Purge**: Perform interactive rebase from `ad00e868` to drop commit `1f262a59` and purge `modern-intranet` folder from `76ec299e`.
    - [x] **Update Ignore Settings**: Add `modern-intranet/` to `.gitignore`.
    - [x] **Tag and Push Hardening**: Purge and recreate release tags `v2.10.58` and `v2.10.59` on clean commits, then force push to remote.

  - [x] **Git History Consolidation (v2.10.25)**: Soft reset to `80407b4c` (v2.10.24) and consolidate 45 micro-commits into 5 clean logical changesets.
  - [x] **Release Cleanup**: Delete redundant tags/releases `v2.10.25` to `v2.10.59` locally and remotely on GitHub.
  - [x] **Actions Cleanup**: Delete failed/redundant GitHub Action workflow runs.

- **Next Focus: Roadmap Execution & CLI Namespacing**
  - [x] **Branch Cleanup**: Audit and delete fully merged roadmap branches (`cli-namespacing`, `guided-onboarding`, `extensible-profiles-architecture`).
  - [x] **Document Branching & Tagging Strategy**: Update CONTRIBUTING.md with branch-isolated pre-release rules.
  - [x] **Conductor Plans Cleanup**: Review, consolidate duplicates, and remove completed plans.
  - [x] **Virtual Environment & Headers Mandate**: Add virtual environment developer mandates and agent rules, and clarify purpose headers in gemini configs.
  - [x] **Organize Conductor Tracks**: Move all individual track plans to the tracks/ subfolder and update tracks.md links.
  - [x] **Roadmap Synchronization**: Update docs/ROADMAP.md to align with completed v2.10.x features and restructure future roadmap items.
  - [x] **Fix Samples Flow**: Delegate get_samples_root in LiferayManager, add test coverage to verify it executes successfully, and test the CLI samples flag.
  - [x] **Diagnostics & Venv Hardening**: Implemented virtualenv environment verification, refactored ldm doctor to provide a RAG summary dashboard by default with granular section filters (`--system`, `--docker`, `--project`), and expanded test coverage.
  - [x] **Test Coverage Hardening**: Expanded unit test coverage for the Snapshot/Restore Service (specifically `_wipe_db()`, `_execute_orchestrated_db_restore()`, and Smart Store Detection heuristics).
  - [x] **CLI Simplification (Namespacing)**: Refactoring flat commands into grouped namespaces (infra, cloud, config, system) with 100% backward compatibility via preprocess_args.
  - [x] **E2E Scripts Refactor**: Update verify_e2e_refactor.sh and verify_e2e_refactor.ps1 for CLI namespacing, legacy translations, and scaled instance logs.
  - [x] **Suppress Pip Warnings**: Add `--disable-pip-version-check` to E2E verification scripts.
  - [x] **Fix E2E Success Output**: Ensure E2E success marker is appended to the results report file in verify scripts.
  - [x] **Fix sync_compatibility.py**: Ensure the history directory is created if it does not exist.
  - [x] **Regenerate User Report**: Manually add the passing marker to the user's report and run sync_compatibility.py to rebuild the matrix.
  - [x] **Linting Documentation**: Clarify auto-fix and `--check` options for `lint.sh` in `CONTRIBUTING.md`.
  - [x] **Fix External PR Formatting**: Checkout PR #1, run linting/formatting fixes, and push to contributor fork.
  - [x] **Fix External PR #1 Test Failure and Verification**:
    - [x] Correct patch decorator in ldm_core/tests/test_runtime.py
    - [x] Run pytest to verify all tests pass (including coverage check)
    - [x] Run E2E script verify_e2e_refactor.sh to verify tag validation warning
    - [x] Commit changes, run quality check/linters, and push to contributor's fork
  - [x] **Automated PR Tag Cleanup Workflow**:
    - [x] Create .github/workflows/cleanup-tags.yml to delete remote pre-release tags on PR merge
    - [x] Validate workflow configuration using actionlint
  - [x] **Release Stable Version v2.11.0**:
    - [x] Run ldm version --promote to update CHANGELOG.md, pyproject.toml, and constants.py
    - [x] Commit and push to origin master to trigger stable GitHub Release
  - [x] **Automate Compatibility Sync Pipeline**:
    - [x] Create scripts/sync_reports_pipeline.sh to automate reports sync, lint, commit, and PR creation
    - [x] Make the script executable and run quality/lint checks
  - [x] **Bugfixes for Showcase**:
    - [x] Force console encoding check in UI._print to prevent silent backslashreplace escaping on Windows
    - [x] Register missing fix-hosts subcommand in ldm_core/cli.py
    - [x] Run pytest to verify all tests pass
    - [x] Restore default SIGPIPE handler on Unix environments in cli.py to prevent BrokenPipeError tracebacks in pipelines
  - [x] **Extensible Stack Profiles & External Database**:
    - [x] Relocate plans folder to `docs/roadmap/plans/` (In Progress)
    - [x] Create directory structure and loader logic for declarative stack profiles.
    - [x] Implement `keycloak-sso` profile (realm-export mapping and OSGi configs).
    - [x] Implement `clustered` profile (JGroups TCPPING and shared Named Volumes).
    - [x] Implement `--db external` database parameter switch.
  - [x] **Hardening Phase 2: Secrets, Compose Validation, and Dependabot**:
    - [x] Implement `detect-secrets` hook in pre-commit and dev-requirements.
    - [x] Create `scripts/validate_compose.py` to validate compose templates.
    - [x] Create `.github/dependabot.yml` configured to check actions and python packages.
  - [x] **Workflow Hardening & Quality Gate Improvements**:
    - [x] Add `actionlint` to pre-commit and dev dependencies.
    - [x] Harden `ci.yml` (add dependency caching & `pip-audit`).
    - [x] Harden `generate-seeded-states.yml` (least-privilege permissions, caching, update action versions).
    - [x] Align `scheduled-verification.yml` with the virtualenv project mandate.

  - [x] **OSGi Performance Branch CI Fixes**:
    - [x] Pre-pull alpine in `ci.yml` to prevent connection timeouts/rate limits.
    - [x] Add `db_type=postgresql` to mock project metadata.
    - [x] Use block redirection to resolve ShellCheck SC2129 warnings in `ci.yml`.
    - [x] Add unit tests for `persist_osgi` validation in `test_runtime.py`.
    - [x] Create OSGi state persistence verification shell script `scripts/verify_osgi_persistence.sh`.
    - [x] Fix port conflict in `verify_osgi_persistence.sh` by using port 8085.
    - [x] Refine log parser and teardown in `verify_osgi_persistence.sh` to support DD-MMM-YYYY formats, use `Starting initial bundles`/`Started web bundles` as OSGi markers, and clear container logs via `down` between runs.
    - [x] Redirect `docker logs` stderr to stdout (`2>&1`) in `verify_osgi_persistence.sh` to capture ready logs and avoid console leakage.
    - [x] Create `docs/showcase/OSGI_STATE_PERSISTENCE.md` containing performance data and a Mermaid chart.
    - [x] Link the new performance page in `docs/showcase/README.md`.
  - [x] **Verbosity Reduction**:
    - [x] Move inner-loop sync and monitoring logs in `workspace.py` to `UI.detail`.
    - [x] Move detailed SQL and archive progress statements in `snapshot.py` to `UI.detail`.
    - [x] Clean up intermediate setup logs in `runtime.py`.

  - [x] **Document Third-Party Tools**:
    - [x] Create `docs/THIRD_PARTY_TOOLS.md` detailing Docker, mkcert, openssl, telnet, nc/ncat, and lcp.
    - [x] Explain why they are needed, which are optional, and what features break without them.
    - [x] Clarify the specific diagnostic check of `nmap`/`ncat` (and its shift to Log4j file-based sync).
    - [x] Link to the new file from `docs/INSTALLATION.md` and `docs/LDM_ARCHITECTURE.md`.
    - [x] Update `ldm_core/handlers/diagnostics.py` to flag `nc/ncat` as Deprecated/Unused (status: True, no warnings/hints).
  - [x] **Windows PowerShell Input Hang Fix**:
    - [x] Refactor `UI.ask` in `ldm_core/ui.py` to use native `input(prompt)` on Windows (`sys.platform == "win32"`) with a safe ASCII prompt, bypassing buffer-related console hangs.
    - [x] Add unit tests in `ldm_core/tests/test_ui.py` to verify `UI.ask` and `UI.confirm` behavior on Windows vs Unix.
  - [x] **E2E PowerShell Verification Parity**:
    - [x] Align `verify_e2e_refactor.ps1` to have 100% functional parity with `verify_e2e_refactor.sh`.
    - [x] Fix the scaling command in `verify_e2e_refactor.ps1` to use `--no-run` to match `verify_e2e_refactor.sh` and prevent container health wait hangs.
    - [x] Implement missing Cascading Defaults check and Status check in `verify_e2e_refactor.ps1`.
  - [x] **Global Infrastructure Port Conflict Check**:
    - [x] Check if ports 80, 443, and 18080 are in use on the host before starting `liferay-proxy-global` container.
    - [x] Dynamically search for and select alternative ports if a conflict is detected.
    - [x] Parameterize Traefik ports in `infra-compose.yml` using `LDM_HTTP_PORT`, `LDM_SSL_PORT`, and `LDM_ADMIN_PORT`.
    - [x] Inspect running proxy container to retrieve mapped ports dynamically for subsequent commands/starts.
    - [x] Print clear warnings about port changes, and display the correct access URL with the resolved port in the ready message.
  - [x] **Python Version Enforcement at Startup**:
    - [x] Enforce Python version >= 3.10 check at the very top of `ldm_core/cli.py` before any third-party or sub-package imports.
    - [x] Print a clear, helpful error message showing the current version and instructions on how to run it with a newer Python (e.g. `python3.12 ldm <args>`).
  - [x] **Dynamic Report Version Mapping in Sync Compatibility**:
    - [x] Update `scripts/sync_compatibility.py` to use the dynamically extracted report version instead of the hardcoded `"2.7.2"`.
  - [x] **Fix Sync Reports Pipeline Staged Status Check**:
    - [x] Update `scripts/sync_reports_pipeline.sh` status check regex to detect both staged and unstaged new, modified, or renamed reports (`A`, `M`, `R`, `?`).
    - [x] Fix `git add` pathspecs in `scripts/sync_reports_pipeline.sh` (remove incorrect root level `COMPATIBILITY_TABLE.md` and `README.md` pathspecs that cause atomic `git add` failures).
    - [x] Test the pipeline script execution to verify that it successfully detects the staged changes and proceeds with the flow.
    - [x] Fix MD028/MD012 markdownlint error in `docs/INSTALLATION.md` by removing the blank lines surrounding the `<!-- -->` separator between the two blockquotes.

  - [x] **Fix COMPATIBILITY_TABLE.md from Parsed Reports** (Done):
    - Parsed all 6 report files and extracted accurate version data.
    - Fixed Fedora row: LDM version updated from `2.4.26` → `2.11.2` (old May 1st report; user confirmed no re-run, bump applied for table consistency).
    - All other rows verified correct against their respective report files.
    - NOTE: Ubuntu 24.04 report is CI-generated by the LDM Platform Verification (Multi-OS) workflow on the `ubuntu-latest` runner.

  - [x] **Auto-Sync CI Reports to Compatibility Table** (Done):
    - Extended `scheduled-verification.yml` with a `sync-compatibility` job.
    - Triggers only on release tag pushes (`push` + `refs/tags/v*`).
    - Job-level `contents: write` + `pull-requests: write` override workflow-level `contents: read`.
    - Downloads ubuntu + fedora artifacts, removes stale Linux reports, copies only pass reports, runs `sync_compatibility.py`, opens a PR.
    - Validated clean with `actionlint`.

  - [x] **Static Compatibility Table Filenames**:
    - [x] Update `scripts/sync_compatibility.py` to use static filenames (`verify-{internal_slug}-{status_slug}.txt`) without hashes.
    - [x] Remove history directory tracking/archiving in `scripts/sync_compatibility.py` and delete stale files instead.
    - [x] Remove hash/timestamp suffix from `scripts/verify_e2e_refactor.sh` and `scripts/verify_e2e_refactor.ps1`.
    - [x] Clean up existing hashed files in `references/verification-results` and regenerate the table with static links.
  - [x] **Fix Ngrok Tests and Merge PR #27**:
    - [x] Mock `run_command` in `test_cmd_run_expose_prompt_save` to prevent Docker volume check failure.
    - [x] Run `./lint.sh --check` to verify all unit tests and quality gates pass.
    - [x] Commit and push the test changes to `feature/ngrok-expose`.
    - [x] Merge PR #27 to master via squash merge.
  - [x] **Document Secrets Prevention**:
    - [x] Update `docs/SECURITY.md` to document secrets prevention, Yelp's `detect-secrets` hooks, `.secrets.baseline` files, and `.gitleaksignore` patterns for developer projects.
  - [x] **Fix Dashboard Inline CSS**:
    - [x] Remove the unused `<style>` tag and replace inline `style="display: none;"` attributes with Alpine/Tailwind class bindings in `index.html` to prevent CSP violations.
  - [x] **Fix FileNotFoundError in Deleted CWD**:
    - [x] Handle `FileNotFoundError` gracefully when `Path.cwd()` is called in `find_dxp_roots()` and other path discovery/detection methods.
  - [x] **Implement Deletion Safety Checks**:
    - [x] Add robust safety validations in `safe_rmtree` to prevent deletion of git repositories, user home, system paths, active CWD, and LDM source files.
  - [x] **lfr-tunnel Integration**:
    - [x] Implement `lfr-tunnel` CLI integration under the `ldm share` subcommand namespace (including downloader, execution, status, and stop commands).
    - [x] Implement version validation checks to ensure the binary is installed and meets the minimum version requirement (auto-updating if outdated).
    - [x] Update `lint.sh` to use the python virtualenv executable for synchronizing scripts/docs to prevent ModuleNotFoundError.
    - [x] Fix Ruff linter errors (lambdas in `cli.py` and RET503 in `share.py`).
    - [x] Add `# nosec` to suppress Bandit warnings for unverified SSL context and urlopen schema in `share.py`.
    - [x] Fix Mypy `method-assign` warnings in `test_share.py` by adding `# type: ignore[method-assign]`.
    - [x] Add `--share` and `--share-subdomain` arguments to `ldm run` command in `cli.py`.
    - [x] Auto-start the tunnel in `sync_stack` / `_wait_for_ready` inside `runtime.py`.
    - [x] Write unit tests for metadata persistence and auto-start in `test_runtime.py`.
    - [x] Document `lfr-tunnel` in `docs/THIRD_PARTY_TOOLS.md` and rename/rewrite `NGROK_INTEGRATION.md` to `SHARING_AND_TUNNELS.md`.
    - [x] Refactor `ShareService` in `share.py` to support both `lfr-tunnel` and `ngrok` providers.
    - [x] Add CLI arguments `--provider` to `ldm share start` and `--share-provider` to `ldm run` in `cli.py`.
    - [x] Integrate unified providers in `runtime.py` and write unit tests in `test_share.py` and `test_runtime.py`.
      - [x] Clean up duplicate block in `runtime.py` (lines 444-451).
      - [x] Fix tests in `test_runtime.py`.
      - [x] Update documentation (THIRD_PARTY_TOOLS.md, SHARING_AND_TUNNELS.md, README.md).

  - [x] **Remote Import & Packaging (v2.11.x)**:
    - [x] Write design and packaging plan in `remote_import_and_packaging_plan.md` (updated with private repo auth).
    - [x] Implement remote URL detection (Git / HTTPS / ZIP) in `WorkspaceService.cmd_import`.
    - [x] Implement robust fail-fast checks for SSH and PAT authentication when accessing private repositories.
    - [x] Create unit tests and integration tests for remote import scenarios.

  - [x] **Liferay Tunnel Enhancement (v2.11.x)**:
    - [x] Integrate updated `lfr-tunnel` supporting multiple background tunnels concurrently.
    - [x] Support status query and health checking using `lfr-tunnel -status-json -subdomain <subdomain>`.
    - [x] Support stopping specific subdomain tunnels with `lfr-tunnel -stop -subdomain <subdomain>`.
  - [x] **Privileged Port Bind Check Fallback (v2.11.x)**:
    - [x] Catch PermissionError / EACCES in BaseHandler.check_port for non-root users.
    - [x] Implement connect_ex check fallback to verify if the port is actually in use.
    - [x] Add unit tests for privileged port check fallback in test_base.py.

  - [x] **Zero-Touch Autocomplete & Setup (P1)**:
    - [x] Register `setup-completion` parser command under the `system` namespace and update fallback mappings.
    - [x] Add delegation method in `manager.py`.
    - [x] Implement robust auto-detection of the active shell and shell profile path verification.
    - [x] Implement `.bak` backup generation for edited configuration profiles.
    - [x] Safely inject bounded autocomplete blocks into configurations.
    - [x] Write pytest unit tests and run tests and lint script.

  - [x] **Predefined Quickstarts (P1)**:
    - [x] Add `quickstart` top-level command parser and route mappings in `cli.py`.
    - [x] Create `QUICKSTART_TEMPLATES` accelerator mapping registry.
    - [x] Implement `cmd_quickstart` in `workspace.py` handling repository import, database seeding, stack startup, and dynamic sharing.
    - [x] Add pytest unit tests for quickstart configurations and commands execution.

  - [x] **Visual Diagnostics Web Dashboard (P3)**:
    - [x] Implement start, stop, snapshot, snapshots list, and restore Flask API endpoints.
    - [x] Upgrade dashboard UI with glassmorphic dark mode styling using Alpine.js and Tailwind CSS.
    - [x] Add pytest unit tests covering new endpoints and execution paths.

  - [x] **Import Stop-Running Flag Support**:
    - [x] Add `--stop-running` to the `import` command parser in `cli.py`.
    - [x] Refactor `cmd_import` in `workspace.py` to extract checking/stopping of running instances into a shared helper method `_ensure_stopped`.
    - [x] Handle the `--stop-running` flag in `_ensure_stopped` to automatically stop running containers.
    - [x] Update error messages in non-interactive mode to hint about the new `--stop-running` flag.
    - [x] Add unit tests verifying stop-running behavior under interactive/non-interactive conditions.

  - [ ] **Home Directory CWD Warning**:
    - [x] Add check at start of `detect_project_path` in `base.py` to check if `CWD` is home directory and warn.
    - [x] Enforce warn-once-per-execution constraint using a manager flag.
    - [ ] Add unit tests verifying warning is printed when CWD is home directory (and fix mock patching).

## 9. Founding Patterns of LDM

- **Sensible Defaults**: Whenever a standard Liferay convention exists, ldm uses it automatically (e.g., port 8080, managed DB name lportal).
- **Smart Context**: If you run a command from inside a project folder, ldm automatically detects the project context.
- **Interactive Fallback**: If a required piece of information (like a project name or a Liferay tag) is missing from your command and cannot be detected, ldm will prompt you interactively or show you a list of choices.
- **Graceful Abort**: You can type `q` at any interactive prompt to safely cancel the operation.

## 10. Scripting & Automation (Piped Input)

- **Automating Prompts**: LDM supports receiving answers to interactive prompts via standard input piping (e.g., `echo -e "n\nmy-project\n\n\n" | ldm run`).
- **Shell Precedence Pitfall**: When piping into a chained command, ensure the pipe binds directly to LDM. For example, `echo "y" | cd /tmp && ldm run` incorrectly pipes into `cd`. The correct syntax is `cd /tmp && echo "y" | ldm run`.

## 11. Pull Request & Review Feedback Loop

- **PR Creation**:
  - After successfully pushing a feature or roadmap branch, the agent should check if the `gh` (GitHub) CLI is available and authenticated.
  - If available, the agent should propose or create a Pull Request targeting the base branch (usually `master`) using `gh pr create --title "<summary>" --body "<details>"`.
- **Review & Feedback Loop**:
  - If the user rejects the PR or requests changes, they can specify this in the chat or ask the agent to inspect the PR feedback.
  - The agent must retrieve PR feedback using the GitHub CLI:

    ```bash
    gh pr view --json reviews,comments,statusCheckRollup
    ```

  - The agent will parse the PR review comments, map them to specific source files, output an implementation plan to address the feedback, and apply the fixes.
  - Once changes are committed and pushed, the agent should verify the PR status and notify the user that the feedback has been addressed.

--- End of Context from: /users/peterrichards/.gemini/gemini.md ---
