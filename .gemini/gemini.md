# Gemini Project Memory - Liferay Docker Manager (LDM)

This file serves as the persistent state and technical knowledge base for the AI assistant working on the LDM project.

## 🛠️ Core Architectural Mandates (Hardened v2.3.6)

### 1. Configuration Priority (The "Liferay Way")

- **Database Configuration (Redline)**: All database-related settings (JDBC URL, Driver, Dialect, Credentials) MUST be injected into `portal-ext.properties` in the project's `files/` directory. This ensures mixed-case keys like `driverClassName` are correctly interpreted by Liferay.
- **Search & Elasticsearch (Redline)**: Search configuration MUST be managed via high-priority **Environment Variables** (e.g., `LIFERAY_ELASTICSEARCH_...`) and **OSGi `.config` substitution**. Do NOT inject search settings into `portal-ext.properties`.
- **80/20 Rule for .config**: Use static `.config` templates from `common/` for connectivity, and perform dynamic project-specific substitution (e.g., `indexNamePrefix`) during synchronization.
- **Domain & Infrastructure**: `web.server.*` and `cluster.link.*` settings MUST be injected via Environment Variables to keep the on-disk `portal-ext.properties` focused on application-level overrides.
- **Multi-line Property Merging**: When updating `portal-ext.properties`, the tool MUST handle multi-line values (using backslash `\` continuations).
- **Environment Variable Separators**:
  - **Modern (2025.Q1+ / 7.4.13-u100+)**: Use single underscore (`_`).
  - **Legacy**: Use double underscore (`__`).
  - The tool must remain version-aware and switch separators automatically.

<<<<<<< HEAD
### 2. Networking & Routing (Traefik v3)
=======
- **Prioritize DRY (Don't Repeat Yourself)**: Before creating a new function/helper, search the `@codebase` for existing utilities.
- **Refactor over Duplicate**: If redundant code is found, suggest a refactor into a shared service or utility rather than creating a new one.
- **Predictive Failure**: For every implementation, list two potential failure points (edge cases or performance) and how they were handled.
- **Predictable Layers**: Ensure logic stays within its designated layer (e.g., UI vs. Business Logic vs. Data) as defined in `spec.md`.
- **Script Parity**: All cross-platform utility, verification, and wrapper scripts (e.g., `.sh` vs `.ps1` or `.bat`) MUST be kept in perfect functional parity. Any hardening check or logic update applied to one must be immediately synchronized to the others.
<<<<<<< HEAD
>>>>>>> fbe7738 (feat: implement environment slugs for automated verification reporting [pre-release])
=======
- **Doctor Logic Synchronization**: Any environment-specific fix or workaround (e.g., volume mount permissions, socket detection) MUST be accompanied by a corresponding check in `ldm doctor` to ensure the tool proactively detects and suggests the fix.
>>>>>>> a2d9eae (docs: codify script versioning, doctor sync, and report context standards in gemini.md)

- **Explicit Network Labels**: Every container managed by LDM MUST have the `traefik.docker.network=liferay-net` label.
- **Metadata DNA**: Every Liferay container MUST have the `com.liferay.ldm.project` label. This is essential for `ldm status` and `ldm prune`.
- **macOS Loopback**: Infrastructure (Traefik) on macOS MUST bind to `0.0.0.0` to support multi-IP loopback.

<<<<<<< HEAD
### 3. Shared Infrastructure & Extraction
=======
- **Unit Test Requirement**: All new logic must have corresponding unit tests.
- **Coverage Expansion**: For every change, review existing tests (unit, e2e, smoke, etc.) and determine if they need to be updated or expanded to increase overall coverage.
- **The Deployment Gate**: You must never suggest a deployment command until you have explicitly asked me to confirm that all unit tests are passing.
- **Test-Driven Alignment**: Propose test cases *before* providing the implementation.
>>>>>>> 24fb006 (fix: resolve E2E verification regressions, infra permissions, and stabilize unit tests [pre-release])

- **Infra Isolation**: Global services (Traefik, Proxy, Global Search) MUST be managed by the `InfraHandler` mixin. Do not leak global orchestration logic into project-specific handlers.
- **Idempotency**: Infrastructure setup MUST be idempotent. Always check for existing (including stopped) containers using `docker ps -a` before attempting creation.

### 4. Diagnostics & Health

- **License Verification**: LDM MUST proactively check for valid Liferay XML licenses in `common/`, `deploy/`, and `osgi/modules/` folders.
- **Doctor Exit Codes**: `ldm doctor` must return **Exit Code 1** if critical issues are detected.
- **UTC Alignment**: Health check timestamps MUST use **UTC** to match Liferay container logs.

### 5. Performance & Seeding (v2)

- **Bootstrap Seeds**: LDM uses version-matched seeds (Database + Search Index + **OSGi State**). Any changes to the seeding engine MUST increment `SEED_VERSION` in `constants.py`.
- **Seeding Control**: The `--no-osgi-seed` flag MUST be respected to allow opt-out of state bootstrapping.
- **Workspace-Aware Seeding**: Seeding MUST be triggered automatically during `import`, `init-from`, and `cloud-fetch` if the Liferay version can be detected early.

<<<<<<< HEAD
### 6. Offline First & Asset Caching (Redline)
=======
- **Test Protocol**: Provide 3-5 manual/automated steps to verify in a live Liferay instance.
- **Redundancy Scan**: After a feature is complete, scan for any newly introduced duplicate code.
- **Mandatory Report Context**: Verification reports MUST include the full output of `ldm doctor --skip-project` at the beginning of the file to ensure complete environmental traceability.
>>>>>>> a2d9eae (docs: codify script versioning, doctor sync, and report context standards in gemini.md)

- **Cache-First Priority**: LDM MUST maintain a working offline experience. Any asset it downloads (seeds, samples, configuration templates) MUST be cached locally in `~/.ldm/references`.
- **Graceful Degradation**: If LDM detects it is offline:
  1. It MUST check the local cache for the required asset.
  2. If the asset is in the cache, use it immediately.
  3. If the asset is NOT in the cache and cannot be downloaded, LDM MUST flag the offline state to the user and **continue** with the offline/vanilla workflow without throwing errors.
- **Samples Exception**: The `--samples` workflow is the only exception. If samples are not cached and cannot be downloaded, LDM MUST inform the user that it is unable to proceed with the sample initialization and then stop gracefully.
- **No Blocking Errors**: Missing non-essential downloads (like seeds) MUST NOT prevent the tool from functioning; it should simply revert to the fresh-install logic.

### 7. Security & Compliance

<<<<<<< HEAD
- **Doctor Log Refinement**: Enhanced `_check_container_health_logs` to handle ECS-formatted Elasticsearch logs and suppressed benign "flood stage disk watermark" warnings.
- **Nosec Disclosure**: Any use of `# nosec` in the codebase MUST be documented in `docs/SECURITY.md`.
- **Contract Verification**: Refactoring MUST be verified against `ldm_core/tests/test_architectural_contracts.py` to ensure no silent loss of mandatory labels or properties.
=======
## 8. Git Management & Branching Strategy

<<<<<<< HEAD
- **Logical Squashing**: Avoid creating a commit for every minor bugfix. Group related fixes and features into single, descriptive commits.
<<<<<<< HEAD
=======
=======
- **Logical Squashing**: Avoid creating a commit for every minor bugfix or repeated attempts to fix the same issue. All turns and iterations required to reach a verified state for a single technical objective MUST be squashed into a single, descriptive commit.
>>>>>>> 9a0071d (docs: refine commit squashing rule in gemini.md)
- **Version Management**: Use the `ldm version` command (available in dev environments) to manage project versions.
  - `ldm version --bump beta` increments the pre-release number.
  - `ldm version --promote` converts a beta release to stable.
- **Script-Only Changes**: Do not increment the project version (`VERSION`) for changes limited to `scripts/`, `docs/`, or `.github/` unless they accompany a core logic change in `ldm_core/`.
>>>>>>> a2d9eae (docs: codify script versioning, doctor sync, and report context standards in gemini.md)
- **Release Automation**:
  - Use `[release]` in the commit summary to trigger a stable GitHub Release.
  - Use `[pre-release]` in the commit summary to trigger a Beta/Test build.
  - Ensure version tags (e.g., `v2.4.26` or `v2.4.26-beta.1`) match the `VERSION` in `ldm_core/constants.py`.
- **Branching Separation**:
  - **`master` / `main`**: Strictly for environmental hardening, stable maintenance, and verified hotfixes.
  - **Roadmap Items**: All large roadmap items, complex features, or experimental refactors MUST be developed in dedicated feature branches (e.g., `roadmap/feature-name`).
  - Merge roadmap branches to `master` only after full verification and peer approval.
  - **Cleanup**: Delete feature branches immediately after a successful merge to `master`.
- **Pre-Change State Persistence**: Before performing ANY file modification (code, documentation, or configuration), I MUST update the `Gemini Added Memories` section of this file with a detailed `<plan>` or task summary to ensure state is persisted in case of an interruption.

## Gemini Added Memories
>>>>>>> bb0c7fb (feat: harden environmental diagnostics and formalize project management [pre-release])

<<<<<<< HEAD
### 8. Robustness & State Management (Hardened v2.4.9)

- **Strict Path Resolution**: Project path detection (`detect_project_path`) MUST explicitly verify that target paths are not files to prevent initialization crashes (`NotADirectoryError`).
- **State Persistence**: Project metadata (e.g., the `seeded` flag) MUST be written to disk immediately after state-changing operations (like seed downloads) to prevent desynchronization between memory and disk.
- **Fail-Fast Initialization**: If `ldm init` encounters an existing file with the target project name, it MUST fail fast and exit rather than silently falling back to incorrect directories.
- **Regression Testing**: All critical bug fixes MUST be accompanied by targeted regression tests (utilizing `unittest.mock` and `pytest`) to ensure the issue is permanently resolved and cannot silently regress during future refactoring.

## 🚀 Release & Workflow Management

### 1. Release Gating ([release] keyword)

- **Explicit Releases**: The GitHub Release workflow is gated. Version tags (`v*`) trigger a **Pre-release** build.
- **Full Release**: To trigger a full GitHub release and update the 'latest' pointer, the commit message MUST contain the **`[release]`** keyword.

### 2. Verification Requirements

- **E2E Testing**: Significant changes to orchestration or infrastructure MUST be verified using `bash scripts/verify_e2e_refactor.sh`.
- **Automated Release E2E**: Commits containing `[release]` automatically trigger the **LDM Release E2E** workflow, which performs live-Docker verification of global infra, project labels, and status reporting on a clean GitHub runner.

## 🏁 Definition of Done for Changes

### Commit Requirements

- **Pre-commit Compliance**: All commits REQUIRE the local pre-commit hooks to pass (`ruff`, `pytest`, `bandit`, `markdownlint`, `version-sync`).
- **Documentation Synchronization**: All functional changes MUST be reflected in `README.md`, `ROADMAP.md`, `SECURITY.md`, and `LDM_ARCHITECTURE.md`.
- **Memory Persistence**: This `gemini.md` file MUST be updated before proposing any changes.
- **Semantic Commits**: All commits must include a clear summary and detailed description.
- **Release Keyword**: Include `[release]` in the commit message only when a full production release is intended.

### Technical Checklist

- [ ] Code passes `./lint.sh`.
- [ ] All unit tests pass (`pytest`).
- [ ] Architectural contracts verified (`python3 ldm_core/tests/test_architectural_contracts.py`).
- [ ] E2E suite verified (`bash scripts/verify_e2e_refactor.sh`).
- [ ] SEED_VERSION incremented (if seeding logic changed).
- [ ] Project labels (`com.liferay.ldm.project`) are applied.
- [ ] Documentation is fully updated.
=======
- I must update gemini.md before proposing any changes to serve as a persistent state, allowing me to resume my work if an interruption occurs.
<<<<<<< HEAD
- **Task: Verification Process & E2E Validation**
  - [x] **Step 1**: Run `bash scripts/verify_e2e_refactor.sh` to identify current failure points.
  - [x] **Step 2**: Analyze `references/verification-results/` for regression data.
  - [x] **Step 3**: Fix any identified issues in the core orchestrator or handlers.
    - [x] Fixed missing `--latest` flag in `ldm restore`.
    - [x] Fixed Elasticsearch snapshot registration race condition in `infra-setup`.
    - [x] Fixed Elasticsearch restart wait loop in `infra-setup`.
- **Task: System Hardening & Release Management**
  - [x] **Git History**: Purged `image.png` from entire repository history using `filter-branch`.
  - [x] **Hardening**: Implemented Tool Path Integrity check in `ldm doctor`.
  - [x] **Hardening**: Implemented self-healing permission fixer for SSL certificates.
  - [x] **Documentation**: Updated Rules of Engagement with Git and Branching strategy.
<<<<<<< HEAD
=======
  - [x] **Roadmap**: Implemented and documented automated version management utility in `roadmap/version-manager`.
  - [x] **Cleanup**: Formalized branch deletion after merge in all docs.
=======
- **Task: Cross-Platform Stabilization & Reporting**
  - [x] **Hardening**: Implemented self-healing permissions for macOS/Colima.
  - [x] **Reporting**: Improved macOS version detection and naming (Monterey, Sequoia, Tahoe).
  - [x] **Reporting**: Automated report naming based on environment slug and status.
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
  - [x] **Sync**: Refined `sync_compatibility.py` to be non-destructive (LATEST only with historical preservation).
  - [x] **Restoration**: Recovered historical verification results by running the refined sync script.
  - [x] **Rules**: Added Script Parity and Pre-Change Persistence mandates.
  - [x] **Release**: Tagged as `v2.4.26-beta.37`.
<<<<<<< HEAD
<<<<<<< HEAD
>>>>>>> 1ddd76e (feat: stabilize cross-platform reporting and refine compatibility sync)
=======
  - [ ] **Fix**: Improved Windows self-upgrade robustness (retry loop + process termination).
  - [ ] **Release**: Bump to `v2.4.26-beta.38`.
>>>>>>> 801ce59 (fix: improve Windows self-upgrade robustness with retry loop [pre-release])
=======
  - [x] **Fix**: Improved Windows self-upgrade robustness (retry loop + process termination).
  - [x] **Fix**: Resolved Linux cross-device link error (Errno 18) using shutil.move.
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.39`.
>>>>>>> 518ff1f (fix: resolve Linux cross-device link error and Windows upgrade robustness [pre-release])
=======
  - [x] **Fix**: Hardened `verify_e2e_refactor.sh` (macOS md5, FATAL detection, report preservation).
<<<<<<< HEAD
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.40`.
>>>>>>> d11415e (fix: harden verify script and stabilize reports)
=======
  - [x] **Release**: Tagged as `v2.4.26-beta.41`.
>>>>>>> 28dc971 (fix: resolve macOS md5 and report loss in verify script)
=======
  - [x] **Fix**: Resolved search backup permission error during restore (Errno 13).
  - [x] **Fix**: Corrected macOS detection in `ldm doctor --slug`.
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.42`.
>>>>>>> 406dc05 (fix: resolve search restore permissions and macOS detection [pre-release])
=======
  - [x] **Verification**: Verified stable on local Apple Silicon machine (Ubuntu + macOS logic).
  - [x] **Release**: Tagged as `v2.4.26-beta.43`.
<<<<<<< HEAD
>>>>>>> 2a7d95f (fix: resolve search restore permissions and macOS detection (Verified Stable))
=======
  - [x] **UI**: Improved project selection UI to disambiguate projects with same name by showing paths.
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.44`.
>>>>>>> 8d61a20 (ui: improve project selection with path hints for duplicates [pre-release])
=======
  - [x] **Fix**: Improved netcat detection on Windows by supporting `ncat` and consolidating path reporting.
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.45`.
>>>>>>> e316462 (fix: improve netcat detection on Windows (ncat support) [pre-release])
=======
  - [x] **Fix**: Targeted cleanup in `verify_e2e_refactor.sh` to prevent accidental deletion of unrelated projects and report files.
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.46`.
>>>>>>> dd16bf1 (fix: targeted cleanup and report preservation in verify script)
=======
  - [x] **Reporting**: Refined `sync_compatibility.py` to eliminate "Unknown" entries and improved fallback detection for Apple Silicon/Intel.
  - [x] **Reporting**: Improved macOS version detection (mapping Darwin major to macOS major) and updated compatibility table header.
<<<<<<< HEAD
  - [x] **Fix**: Restored Apple Silicon/Intel detection in `ldm doctor --slug`.
  - [x] **Fix**: Improved `verify_e2e_refactor.sh` robustness (ignore "not found" during cleanup).
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.49`.
>>>>>>> 1cbf92b (fix: restore Mac arch detection and improve verify script robustness [pre-release])
=======
  - [x] **Fix**: Improved Windows self-upgrade cleanup to avoid "batch file cannot be found" error.
  - [x] **Release**: Tagged as `v2.4.26-beta.50`.
>>>>>>> 2c9c613 (fix: improve Windows self-upgrade cleanup logic [pre-release])
=======
  - [x] **Reporting**: Updated compatibility table title to indicate "Standalone Binaries".
  - [x] **Fix**: Improved `verify_e2e_refactor.sh` to explicitly mention project template source and handle cleanup "not found" silently.
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.51`.
>>>>>>> 8331e77 (fix: improve verify script template error and update table title)
=======
  - [x] **Verification**: Final sync of compatibility table with latest historical and new reports.
  - [x] **Release**: Tagged as `v2.4.26-beta.52`.
>>>>>>> a0c06c7 (feat: finalized cross-platform reporting and stability fixes)
=======
  - [x] **Fix**: Truly surgical cleanup in `verify_e2e_refactor.sh` (LDM_WORKSPACE isolation + no side effects).
  - [x] **Release**: Tagged as `v2.4.26-beta.53`.
>>>>>>> 374bd37 (feat: truly surgical cleanup and report preservation in verify script)
=======
  - [x] **Reporting**: Improved macOS version detection (mapping Darwin major to macOS major with marketing names like Monterey, Ventura, etc.).
  - [x] **Fix**: Improved `verify_e2e_refactor.sh` to handle cleanup "not found" silently and avoid terminal noise.
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.55`.
>>>>>>> 8a6c340 (feat: improve macOS version naming and silence verify script cleanup [pre-release])
=======
  - [x] **Fix**: Added specific WSL troubleshooting hints for Docker connection failures and improved Native WSL2 vs Desktop detection.
  - [x] **Release**: Tagged as `v2.4.26-beta.56`.
>>>>>>> c5d1430 (fix: improve WSL Docker troubleshooting and provider detection [pre-release])
=======
  - [x] **Sync**: Refined `sync_compatibility.py` with robust normalization and deduplication.
=======
  - [x] **Sync**: Refined `sync_compatibility.py` with robust normalization, Tahoe/Sequoia mapping, and "Failure + Latest Pass" cleanup policy.
>>>>>>> 712a3cd (fix: implement pass-only cleanup policy and Tahoe mapping)
=======
  - [x] **Sync**: Refined `sync_compatibility.py` with robust normalization, Tahoe/Sequoia mapping, and "Surgical Archival Strategy".
>>>>>>> cddaea0 (fix: resolve syntax error in verify_e2e_refactor.ps1)
=======
  - [x] **Sync**: Refined `sync_compatibility.py` with doctor-first detection, LDM Version column, and "Surgical Archival Strategy".
>>>>>>> 3df02e0 (docs: use Windows icon for WSL2 badge and extract LDM version)
  - [x] **Fix**: Improved Windows self-upgrade and WSL troubleshooting (Docker socket detection).
  - [x] **Fix**: Implemented proactive Volume Write Test in `ldm doctor`.
  - [x] **Fix**: Added Colima LaunchAgent detection and tailored hints.
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.63`.
>>>>>>> 756c1ee (fix: stabilize environmental detection and improve macOS naming [pre-release])
=======
  - [x] **Fix**: Corrected `mkcert` download URL and added `--fail` to `curl` in `.github/workflows/ci.yml`.
<<<<<<< HEAD
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.64`.
>>>>>>> cdf720e (fix: correct mkcert download URL and error handling in CI)
=======
  - [x] **Docs**: Updated `TESTING.md` with an "Automation" column to distinguish between E2E, CI, and Manual tests.
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.65`.
>>>>>>> 4561e48 (docs: restructure TESTING.md into automated and manual sections)
=======
  - [x] **Fix**: Isolated the CI version synchronization check into a standalone script to resolve a segmentation fault (exit 139) in the main runtime.
  - [x] **Release**: Tagged as `v2.4.26-beta.66`.
>>>>>>> 1b15b0e (fix: resolve CI segmentation fault by isolating version check)
=======
  - [x] **Fix**: Isolated CI version synchronization check to resolve segmentation fault (exit 139).
  - [x] **Fix**: Hardened Windows verification script (`.ps1`) and fixed console encoding.
<<<<<<< HEAD
<<<<<<< HEAD
  - [x] **Fix**: Resolved syntax error in `verify_e2e_refactor.ps1` caused by omission placeholders.
<<<<<<< HEAD
  - [x] **Docs**: Updated `TESTING.md` with functional grouping (Automated vs Manual).
  - [x] **Docs**: Updated `LDM_ARCHITECTURE.md` to reflect proactive health checks and provider detection.
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.67`.
>>>>>>> f6ed162 (docs: restructure TESTING.md and update architecture doc)
=======
  - [x] **Release**: Tagged as `v2.4.26-beta.69`.
>>>>>>> 712a3cd (fix: implement pass-only cleanup policy and Tahoe mapping)
=======
  - [x] **Release**: Tagged as `v2.4.26-beta.70`.
>>>>>>> 553eaec (fix: implement report archival strategy and clean up compatibility table)
=======
  - [x] **Release**: Tagged as `v2.4.26-beta.74`.
>>>>>>> cddaea0 (fix: resolve syntax error in verify_e2e_refactor.ps1)
=======
  - [x] **Hardening**: Integrated `PSScriptAnalyzer` into `lint.sh` and GitHub CI to prevent PowerShell syntax regressions.
  - [x] **Release**: Tagged as `v2.4.26-beta.75`.
>>>>>>> f20a63f (fix: integrate PSScriptAnalyzer into CI and local linting)
=======
  - [x] **Hardening**: Integrated `PSScriptAnalyzer` into CI to prevent PowerShell syntax regressions.
  - [x] **Release**: Tagged as `v2.4.26-beta.78`.
>>>>>>> 3df02e0 (docs: use Windows icon for WSL2 badge and extract LDM version)
=======
  - [x] **Fix**: Resolved syntax error in `verify_e2e_refactor.ps1` caused by omission placeholders.
  - [x] **Hardening**: Integrated `PSScriptAnalyzer` into CI and ensured build failure on PowerShell syntax errors.
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
  - [x] **Release**: Tagged as `v2.4.26-beta.79`.
<<<<<<< HEAD
>>>>>>> f586913 (fix: resolve syntax error in verify_e2e_refactor.ps1 and harden CI linting)
=======
=======
  - [x] **Consolidation**: Grouped all stabilization fixes under a single release and cleaned up intermediate beta tags.
  - [x] **Release**: Tagged as `v2.4.26-beta.64`.
>>>>>>> 13fa024 (chore: consolidate stabilization fixes and clean up redundant beta tags)
=======
  - [x] **Consolidation**: Linearized and cleaned up project history; stabilization work now grouped under `beta.64` and `beta.65`.
  - [x] **Release**: Tagged as `v2.4.26-beta.65`.
>>>>>>> 3e6161d (chore: linearize project history and clean up redundant beta tags)
=======
  - [x] **Consolidation**: Fully linearized and cleaned up project history; stabilization work now re-indexed and tagged up to `beta.55`.
  - [x] **Release**: Tagged as `v2.4.26-beta.55`.
>>>>>>> fd2254b (chore: fully linearize project history and re-index beta tags)
- **Key Knowledge**:
  - **Provider Mapping**: Docker Desktop is strictly reserved for Windows 11 environments in our verification suite. On macOS, any `default` or `colima` context is mapped to **Colima** for reporting.
  - **Archival Strategy**: `references/verification-results/` contains only the single latest report per environment; all older or redundant "Unknown" reports are moved to `history/`.
- **Next Objective**:
  - [ ] **Feature**: Verify Elasticsearch 8 snapshot/restore logic using the `SnapshotState` mapping context to ensure cross-platform compatibility (mapping SUCCESS, FAILED, etc. correctly).
>>>>>>> 3d10163 (docs: add next focus and key environment knowledge to gemini.md)

>>>>>>> 8b0a863 (feat: implement DNS cleanup and stable tier safety hatch [pre-release])
--- End of Context from: /users/peterrichards/.gemini/gemini.md ---
>>>>>>> 695ef4a (docs: update gemini.md session state)
