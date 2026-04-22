# LDM Testing & Validation Strategy

This document outlines the strategy for ensuring Liferay Docker Manager (LDM) remains stable across its supported physical lab (Silicon/Intel Mac, Windows/WSL2, Native Linux).

## 🧪 Automated Testing & Strategy

LDM uses a multi-layered automated verification suite:

1. **Unit Tests (Pytest):** Core logic verification for path resolution, version parsing, and configuration generation.
   - *Candidates for Automation*: CLI parsing (intermixed flags), human-readable timing formatters, tag discovery logic (using mock HTTP responses).
2. **Security Scans (Bandit):** Automated detection of common security issues like hardcoded paths or insecure subprocess calls.
3. **CI Smoke Tests:** GitHub Actions that verify the tool can initialize, run the `doctor`, and perform mock upgrades in a clean environment.
4. **Filesystem Mocks**: We use `unittest.mock` to simulate cross-platform filesystem scenarios (e.g., native manual symlink creation, project discovery on external volumes) without modifying the host system.
5. **Contract-Based Verification**: A dedicated suite (`test_architectural_contracts.py`) that verifies the *output* of LDM handlers (YAML, properties) against architectural mandates without using mocks.

### 🏗️ Architectural Mandates

All refactoring and feature development must preserve the following LDM contracts:

- **Metadata DNA**: Every Liferay container MUST have the `com.liferay.ldm.project` label.
- **Domain Trust**: Custom hostnames MUST trigger proactive **Environment Variable** injection for node naming and redirect IPs.
- **Search Hardening**: Sidecar ES MUST be disabled via high-priority Environment Variables when Shared Search is active.
- **Database Reliability**: JDBC and Hibernate settings MUST be managed via `portal-ext.properties` to ensure mixed-case key integrity.
- **Persistence**: `osgi/state` must remain host-mapped for single-node instances to preserve seeding benefits.

> [!NOTE]
> **Automation Limitations**: Operations requiring `sudo` (e.g., `fix-hosts`, atomic binary swaps) or live authenticated connections (Cloud Fetch) are difficult to automate in standard CI runners and are prioritized for manual sign-off on physical lab hardware.

---

## 📋 Functional Validation Checklist (v2.4.0+)

This checklist is ordered sequentially to minimize environment setup overhead. Follow the phases in order.

### Phase 1: Tool & Security Readiness

*Verifies the tool's integrity and basic help systems before any infrastructure is touched.*

| ID | Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- | :--- |
| 1.1 | **No-Sudo Guard** | `sudo ldm run` | Command is blocked with a security warning. |
| 1.2 | **Exit Code Integrity**| `ldm doctor --skip-project; echo $?` | Returns `0` if healthy, `1` if critical issues found. |
| 1.3 | **Shell Completion** | `ldm completion zsh` | Generates a valid script. TAB completion works. |
| 1.4 | **Native Manual** | `man ldm` | System manual page opens correctly via `~/.ldm/man`. |
| 1.5 | **Self-Repair** | `ldm upgrade --repair -y` | Successfully re-downloads current version binary. |

### Phase 2: Global Infrastructure

*Verifies the shared Traefik and Elasticsearch components.*

| ID | Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- | :--- |
| 2.1 | **Infra Setup** | `ldm infra-setup --search` | Starts Traefik and ES8. Idempotent. |
| 2.2 | **DNS Alignment** | Point host to wrong IP | `ldm doctor` warns if hostname doesn't match Traefik IP. |
| 2.3 | **Auto-Healing DNS** | `ldm doctor --fix-hosts` | Prompts for elevation and appends missing domains. |

### Phase 3: Project Lifecycle (Init & Seeding)

*Verifies project creation, seeding, and collision detection.*

| ID | Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- | :--- |
| 3.1 | **Explicit Init** | `ldm init test-init -y --tag [tag]` | Scaffolds folders/metadata; no pre-flight checks. |
| 3.2 | **Non-Interactive Run**| `ldm run [id] -y` | Starts project from seed without any prompts. |
| 3.3 | **Project Collision** | `ldm init [id]` in new dir | Blocks execution; identifies original project path. |
| 3.4 | **Hostname Collision**| `ldm run --host-name [used]`| Blocks if another project uses that Virtual Hostname. |
| 3.5 | **Ghost Mounts** | Check project folders | Essential dirs (`osgi/state`, `data`) created before mount. |
| 3.6 | **Memory Units** | `ldm run --mem-limit 2048` | `docker-compose.yml` uses `2048M` (v2.4.0 Mandate). |
| 3.7 | **License Discovery** | `ldm doctor` | Correctly identifies XML license in project `deploy/`. |

### Phase 4: Runtime Configuration & UX

*Verifies managing a running project and the user experience.*

| ID | Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- | :--- |
| 4.1 | **Env Sync** | `ldm env [id] KEY=VAL` | `docker-compose.yml` updated immediately. |
| 4.2 | **Redaction Check** | `ldm -v run` (with secrets) | `LIFERAY_DB_PASSWORD` masked as `[REDACTED]`. |
| 4.3 | **WSL Browser** | `ldm run` in WSL | Opens Windows host browser without UNC warnings. |
| 4.4 | **Intermixed Flags** | `ldm ps -y` | Global flags recognized after subcommands. |
| 4.5 | **Fail-Fast Logic** | Delete `docker-compose.yml`| LDM stops immediately with helpful recovery hint. |

### Phase 5: Data Integrity & Recovery

*Verifies snapshots, restoration, and the new SHA-256 mandates.*

| ID | Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- | :--- |
| 5.1 | **SHA-256 Generation** | `ldm snapshot [id]` | `.sha256` file created alongside the archive. |
| 5.2 | **SHA-256 Verification**| `ldm restore [id]` | LDM verifies checksum before extraction. |
| 5.3 | **Corruption Guard** | Modify snapshot archive | `ldm restore` fails with integrity error. |
| 5.4 | **Project Reset** | `ldm reset state` | Clears `osgi/state` folder while stopped. |

### Phase 6: Advanced Integrations

*Verifies complex scaling and external sync.*

| ID | Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- | :--- |
| 6.1 | **Multi-Node Scaling** | `ldm scale [id] liferay=2`| Disables host-mapping; injects cluster-link env. |
| 6.2 | **Search Migration** | `ldm migrate-search` | Deletes local indices; applies Global ES config. |
| 6.3 | **Cloud Env Sync** | `ldm cloud-fetch uat` | Correctly fetches and merges `LCP.json` env vars. |

### Phase 7: Cleanup & Pruning

*Verifies teardown and filesystem hygiene.*

| ID | Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- | :--- |
| 7.1 | **Registry Cleanup** | `ldm down [id] --delete` | Project removed from registry (`ldm ls` is clean). |
| 7.2 | **SSL Hygiene** | Check Traefik configs | Routing config removed after project teardown. |
| 7.3 | **Non-Interactive Prune**| `ldm prune -y` | Silently removes orphaned containers/snapshots. |
| 7.4 | **Self-Healing Registry**| Delete project folder manually | `ldm ls` detects dead path and prunes registry. |

---

## 🛡️ Supported Environments

The following environments are actively verified as part of our testing lifecycle:

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Hardening | Verified |
| :--- | :--- | :--- | :--- | :--- |
| **Apple Silicon** | macOS 11+ | **OrbStack** | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | ✅ |
| **Apple Silicon** | macOS 11+ | **Docker Desktop** | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=apple) | ✅ |
| **Apple Silicon** | macOS 11+ | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ |
| **Apple Intel** | macOS 11+ | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ |
| **Windows PC** | Windows 11 | **Native WSL2** | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | ✅ |
| **Windows PC** | Windows 11 | **Docker Desktop** | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=windows) | ✅ |
| **Linux Workstation** | Fedora 43 | **Native Docker** | ![Fedora](https://img.shields.io/badge/Fedora-Hardened-success?style=flat-square&logo=linux) | ✅ |
| **Linux Node** | Ubuntu 22.04 | **Docker Engine** | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | ✅ |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |
<!-- COMPATIBILITY_END -->

---

## 🏁 Final Sign-off

- [ ] All tests pass on the target Host OS.
- [ ] `ldm doctor` shows ✅ for all critical components.
- [ ] Site is reachable at `https://<hostname>` with a Green Lock 🔒.
