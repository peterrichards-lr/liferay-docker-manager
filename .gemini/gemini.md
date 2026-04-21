# Gemini Project Memory - Liferay Docker Manager (LDM)

This file serves as the persistent state and technical knowledge base for the AI assistant working on the LDM project.

## 🛠️ Core Architectural Mandates (Hardened v2.3.6)

### 1. Configuration Priority (The "Liferay Way")

- **Direct Properties**: Critical infrastructure settings MUST be injected directly into `portal-ext.properties` located in the project's `files/` directory.
- **Bypass Env Vars**: Do NOT rely on environment variables for `web.server.*`, `elasticsearch.*`, or `cluster.link.*` settings.
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

### 6. Security & Compliance

- **Nosec Disclosure**: Any use of `# nosec` in the codebase MUST be documented in `docs/SECURITY.md`.
- **Contract Verification**: Refactoring MUST be verified against `ldm_core/tests/test_architectural_contracts.py` to ensure no silent loss of mandatory labels or properties.

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
