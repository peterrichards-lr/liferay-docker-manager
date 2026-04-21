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

## 📋 Functional Validation Checklist (v2.1.31+)

### Phase 1: Environment & Diagnostics

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **1.1 LDM Doctor** | Run `ldm doctor` | Correctly identifies Docker CPUs/RAM. Reports "Global Config" status. |
| **1.2 Pipeline Exit Code** | `ldm doctor --skip-project; echo $?` | Returns `0` if healthy, `1` if critical issues found. |
| **1.3 DNS Alignment** | Point host to wrong IP | `ldm doctor` warns if hostname doesn't match Traefik's bound IP. |
| **1.4 Infra Setup** | `ldm infra-setup --search` | Starts Traefik (on 0.0.0.0) and ES8 sidecar. Idempotent. |
| **1.5 Self-Repair** | `ldm upgrade --repair -y` | Successfully re-downloads current version binary without prompts. |
| **1.6 License Verification** | `ldm doctor` (DXP) | Correctly parses XML license from `common/` or `deploy/`. |
| **1.7 Shell Completion** | `ldm completion zsh` | Generates a valid completion script. TAB completion works for subcommands. |
| **1.8 Native Manual** | `man ldm` (after setup) | System manual page opens correctly via `~/.ldm/man` path. |

### Phase 2: Developer Workflow & Automation

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **2.1 Non-Interactive Run** | `ldm run [id] -y --tag [tag]` | Starts project without any interactive prompts. |
| **2.2 Non-Interactive Env** | `ldm env [id] TEST=VAL -y` | Sets environment variable without service selection prompts. |
| **2.3 Ghost Mounts** | Run on new project | LDM proactively creates essential directories before bind-mounting. |
| **2.4 WSL Browser** | `ldm run` in WSL | Automatically opens the Windows host browser without "UNC path" warnings. |
| **2.5 Intermixed Flags** | `ldm prune -y` | Global flags are recognized correctly when placed after subcommands. |

### Phase 3: Orchestration Stability

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **3.1 Intel Mac Discovery** | `ldm run` on x86_64 Mac | Correctly prefers legacy `docker-compose` if v2 plugin is broken. |
| **3.2 Env Uniqueness** | Add redundant vars | Generated `docker-compose.yml` contains only unique environment keys. |
| **3.3 Fail-Fast Logic** | Remove docker-compose | LDM commands stop immediately with helpful installation hints. |
| **3.4 Resilient Tags** | Block releases.liferay.com | LDM correctly falls back to Docker Hub JSON API for tag discovery. |

### Phase 4: Maintenance & Hygiene

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **4.1 Non-Interactive Prune** | `ldm prune -y` | Silently removes orphaned resources without confirmation. |
| **4.2 SSL Hygiene** | Run `ldm down` | Correctly removes certificates and Traefik routing configs. |
| **4.3 SSL Renewal** | `ldm renew-ssl` | Surgically replaces existing certificates with fresh 2-year versions. |
| **4.4 Search Migration** | `ldm migrate-search` | Deletes internal indices and applies Global ES configs while stopped. |
| **4.5 Project Reset** | `ldm reset state` | Successfully clears `osgi/state` folder while stopped. |
| **4.6 Zero-Failure Upgrade** | `ldm upgrade --repair` | Downloads asset to `/tmp` first; prompts for internal `sudo` only for final move. |

### Phase 5: High-Risk Integrations (v2.1+)

These features involve complex state changes or rely on external APIs that may change unexpectedly. They require explicit end-to-end verification during major release cycles.

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **5.1 Cloud Env Sync** | `ldm cloud-fetch demo uat --sync-env` | Correctly fetches and applies `LCP.json` env vars despite `lcp` CLI plain-text formatting. |
| **5.2 Cloud DB Hydration** | `ldm cloud-fetch demo uat --download` | Safely downloads and extracts DB backups into `snapshots/` without path traversal issues. |
| **5.3 Multi-Node Lock Avoidance** | `ldm scale demo liferay=2` | Correctly disables host-mapped `osgi/state` and injects cluster-link environment variables. |
| **5.4 Search Migration Resilience** | `ldm migrate-search demo` | Liferay successfully rebuilds its index on the shared ES8 container on the next boot without falling back to the sidecar. |
| **5.5 Auto-Healing DNS (Elevation)** | `ldm doctor --fix-hosts` | Successfully prompts for `sudo` (or UAC on Windows) and appends missing subdomains without duplicating lines. |
| **5.6 Windows Deep Deletion** | `ldm rm demo --delete` (Native Windows) | Successfully wipes the project folder, even if it contains deeply nested `node_modules` or Docker-locked volumes. |

### Phase 6: Security & Policy Enforcement

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **6.1 No-Sudo Guard** | `sudo ldm run` | Command is blocked with a security warning and a link to troubleshooting. |
| **6.2 Double-Root Detection** | Login as `root`, run `ldm` | Correctly identifies already-elevated shell and blocks execution to protect `~/.shiv`. |
| **6.3 Redaction Check** | `ldm -v run` (with secrets) | Verify that `LIFERAY_DB_PASSWORD` or similar are masked with `[REDACTED]` in the debug output. |

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
