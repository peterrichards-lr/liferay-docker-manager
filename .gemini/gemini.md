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
>>>>>>> fbe7738 (feat: implement environment slugs for automated verification reporting [pre-release])

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

### 6. Offline First & Asset Caching (Redline)

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

- **Logical Squashing**: Avoid creating a commit for every minor bugfix. Group related fixes and features into single, descriptive commits.
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
  - [x] **Hardening**: Improved verification scripts to fail immediately on error.
  - [x] **Reporting**: Added `ldm doctor --slug` for machine-readable environment strings.
  - [x] **Reporting**: Automated report naming based on environment slug and status.
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
  - [x] **Release**: Tagged as `v2.4.26-beta.51`.
>>>>>>> 8331e77 (fix: improve verify script template error and update table title)

>>>>>>> 8b0a863 (feat: implement DNS cleanup and stable tier safety hatch [pre-release])
--- End of Context from: /users/peterrichards/.gemini/gemini.md ---
>>>>>>> 695ef4a (docs: update gemini.md session state)
