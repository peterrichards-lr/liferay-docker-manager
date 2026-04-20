# Gemini Project Memory - Liferay Docker Manager (LDM)

This file serves as the persistent state and technical knowledge base for the AI assistant working on the LDM project.

## 🛠️ Core Architectural Mandates (Hardened v1.6.35)

### 1. Configuration Priority (The "Liferay Way")

- **Direct Properties**: Critical infrastructure settings MUST be injected directly into `portal-ext.properties` located in the project's `files/` directory.
- **Bypass Env Vars**: Do NOT rely on environment variables for `web.server.*`, `elasticsearch.*`, or `cluster.link.*` settings. Newer Liferay versions have unreliable decoding for these variables (causing "Unable to decode part" warnings).
- **Multi-line Property Merging**: When updating `portal-ext.properties`, the tool MUST handle multi-line values (using backslash `\` continuations). Simple line-by-line regex will fail and corrupt the file by leaving orphan lines. Always use the atomic block-replacement logic found in `update_portal_ext`.
- **Environment Variable Separators**:
  - **Modern (2025.Q1+ / 7.4.13-u100+)**: Use single underscore (`_`).
  - **Legacy**: Use double underscore (`__`).
  - The tool must remain version-aware and switch separators automatically based on the target tag.

### 2. Networking & Routing (Traefik v3)

- **Explicit Network Labels**: Every container managed by LDM MUST have the `traefik.docker.network=liferay-net` label. Without this, Traefik v3 may fail to resolve the internal backend IP, resulting in persistent 404 errors.
- **API Version Negotiation**: Traefik MUST be at least **v3.6.1** to support automatic API version negotiation. Older versions (v3.0-v3.5) are hardcoded to Docker API v1.24 and will fail on modern Docker engines (v29+) with "client version 1.24 is too old" errors.
- **macOS Loopback**: Infrastructure (Traefik) on macOS MUST bind to `0.0.0.0` to support multi-IP loopback for custom virtual hostnames.
- **Bridge Reliability**: The `docker-socket-proxy` bridge on macOS must be verified for network connectivity during every `run` and `doctor` command.

### 3. macOS Infrastructure (Self-Healing Bridge)

- **Dynamic Socket Discovery**: Always use `get_docker_socket_path()` to find the host socket.
- **Mount Fallback**: If mounting the dynamic path fails with "operation not supported" (common on Colima/OrbStack), automatically fall back to the standard `/var/run/docker.sock`.
- **Cleanup on Conflict**: If a bridge container creation fails due to a mount error, the tool must explicitly `docker rm -f` the failed record before retrying the fallback path to avoid name conflicts.

### 4. Diagnostics & Health

- **License Verification**: LDM MUST proactively check for valid Liferay XML licenses in `common/`, `deploy/`, and `osgi/modules/` folders. It must warn the user if a license is missing for DXP/EE images but remain silent for Portal CE images.
- **Doctor Exit Codes**: `ldm doctor` must return **Exit Code 1** if critical issues are detected to support shell pipelines (`ldm doctor && ldm run`).
- **Infrastructure Log Scans**: `ldm doctor` proactively scans the last 20 lines of global infrastructure logs (Traefik, ES8, Proxy) for `ERROR` or `WARN` keywords to identify platform-level failures.
- **UTC Alignment**: Health check timestamps and "Still waiting" messages MUST use **UTC** to match Liferay container logs for easy correlation.
- **Metadata Health**: Proactively scrub project metadata (`.liferay-docker.meta`) of legacy/poisoned environment variables during the `run` sequence.

### 5. Liferay Standards & Performance

- **JVM Mandatory Flags**: All Liferay instances MUST include `-Dfile.encoding=UTF8` and `-Duser.timezone=GMT`. These are foundational for data consistency and internationalization.
- **Database character set**: MySQL/MariaDB databases MUST be created with `utf8mb4` character set and `utf8mb4_unicode_ci` collation. Liferay 2025.Q1+ strictly validates this on boot.
- **Table Case Sensitivity**: MySQL MUST be configured with `lower_case_table_names=1` to ensure cross-platform compatibility of database migrations and snapshots.
- **JIT Optimization**: For development speed, LDM proactively adds `-XX:TieredStopAtLevel=1` if the heap is explicitly set, significantly reducing boot times.

### 6. Security & Compliance

- **Nosec Disclosure**: Any use of `# nosec` in the codebase MUST be documented in `docs/SECURITY.md` with a clear explanation of the intent, disclosure of the risk, and description of the mitigation. This ensures transparency regarding intentional security trade-offs made for local development functionality.

## 🧪 Knowledge & Troubleshooting Snippets

### Why am I seeing a 404 on macOS?

- Check if `docker-socket-proxy` is connected to `liferay-net`.
- Verify the Traefik `traefik.docker.network` label is present.
- Ensure the Docker socket path is correctly mapped (use the self-healing bridge logic).

### Why is Liferay ignoring my Search/SSL settings?

- Ensure the settings are in `portal-ext.properties`, NOT just environment variables.
- Check for "Unable to decode part" warnings in the logs—this indicates an incompatible underscore format for that version.
- **Corrupted Portal-Ext**: Check `portal-ext.properties` for duplicate or orphaned lines. This happens if the merge logic fails to handle multi-line values (backslash continuations) correctly.

### How do I verify a local build?

- Use `./scripts/package-shiv.sh --install`.
- Run `ldm doctor` and verify the timestamp in the version line.

## 🏁 Definition of Done for Changes

### Commit Requirements

- **Pre-commit Compliance**: All commits REQUIRE the local pre-commit hooks to pass (`ruff`, `pytest`, `bandit`, `markdownlint`, `version-sync`). NEVER bypass these checks.
- **Documentation Synchronization**: All functional changes MUST be reflected in relevant documentation (`README.md`, `ROADMAP.md`, `SECURITY.md`, `LDM_ARCHITECTURE.md`).
- **Memory Persistence**: The `.gemini/gemini.md` file MUST be updated before proposing any changes to serve as the persistent state for the assistant.
- **Semantic Commits**: All commits must include a clear, suitable summary and a detailed description of the changes made and why.
- **Sudo-Free Operation**: LDM MUST NOT be run with the `sudo` prefix. All commands that require elevated privileges (e.g., host file updates, binary replacement) MUST request elevation internally via `sudo` or equivalent OS mechanisms to protect the integrity of the user's cache (`~/.shiv`).
- **History Management**: For bug fixes and refinements, prioritize squashing git history and retagging/re-releasing the current version (while LDM is in early-access staging) to maintain a high-signal commit history.

### Technical Checklist

- [ ] Code passes `./lint.sh` (Ruff, Markdown, Bandit, Pytest).
- [ ] All unit tests pass locally (`pytest ldm_core/tests/`).
- [ ] All `# nosec` usages are documented in `docs/SECURITY.md`.
- [ ] `ldm-doctor.sh` check passes (if infra changes were made).
- [ ] Version-aware separator logic is maintained.
- [ ] Explicit Traefik network labels are applied.
- [ ] Documentation is fully updated.
- [ ] Project Memory (`gemini.md`) is updated.
