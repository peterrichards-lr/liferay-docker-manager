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

### 2. Networking & Routing (Traefik v3)

- **Explicit Network Labels**: Every container managed by LDM MUST have the `traefik.docker.network=liferay-net` label.
- **Metadata DNA**: Every Liferay container MUST have the `com.liferay.ldm.project` label. This is essential for `ldm status` and `ldm prune`.
- **macOS Loopback**: Infrastructure (Traefik) on macOS MUST bind to `0.0.0.0` to support multi-IP loopback.

### 3. Shared Infrastructure & Extraction

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
- **Task: System Hardening & Release Management**
  - [x] **Git History**: Purged `image.png` from entire repository history using `filter-branch`.
  - [x] **Hardening**: Implemented Tool Path Integrity check in `ldm doctor`.
  - [x] **Hardening**: Implemented self-healing permission fixer for SSL certificates.
  - [x] **Documentation**: Updated Rules of Engagement with Git and Branching strategy.
--- End of Context from: /users/peterrichards/.gemini/gemini.md ---
>>>>>>> 695ef4a (docs: update gemini.md session state)
