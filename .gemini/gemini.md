# Gemini Project Memory - Liferay Docker Manager (LDM)

This file serves as the persistent state and technical knowledge base for the AI assistant working on the LDM project.

## 🛠️ Core Architectural Mandates (Hardened v1.6.26)

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

- [ ] Code passes `./lint.sh` (Ruff, Markdown, Bandit, Pytest).
- [ ] All 25 unit tests pass.
- [ ] Version-aware separator logic is maintained.
- [ ] Explicit Traefik network labels are applied.
- [ ] Documentation (`README.md`, `ROADMAP.md`) is updated.
- [ ] Project Memory (`gemini.md`) is updated.
