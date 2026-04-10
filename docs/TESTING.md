# LDM Testing & Validation Strategy

This document outlines the strategy for ensuring Liferay Docker Manager (LDM) remains stable across its supported physical lab (Silicon/Intel Mac, Windows/WSL2, Native Linux).

## 🧪 Automated Testing

LDM uses a multi-layered automated verification suite:

1. **Unit Tests (Pytest):** Core logic verification for path resolution, version parsing, and configuration generation.
2. **Security Scans (Bandit):** Automated detection of common security issues like hardcoded paths or insecure subprocess calls.
3. **CI Smoke Tests:** GitHub Actions that verify the tool can initialize, run the `doctor`, and perform mock upgrades in a clean environment.

---

## 📋 Functional Validation Checklist (v1.6.6)

### Phase 1: Environment & Diagnostics

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **1.1 LDM Doctor** | Run `ldm doctor` | Correctly identifies Docker CPUs/RAM. Reports "Global Config" status. |
| **1.2 Pipeline Exit Code** | `ldm doctor --skip-project; echo $?` | Returns `0` if healthy, `1` if critical issues found. |
| **1.3 DNS Alignment** | Point host to wrong IP | `ldm doctor` warns if hostname doesn't match Traefik's bound IP. |
| **1.4 Infra Setup** | `ldm infra-setup --search` | Starts Traefik (on 0.0.0.0) and ES8 sidecar. Idempotent. |
| **1.5 Self-Repair** | `ldm upgrade --repair -y` | Successfully re-downloads current version binary without prompts. |
| **1.6 License Verification** | `ldm doctor` (DXP) | Correctly parses XML license from `common/` or `deploy/`. |

### Phase 2: Developer Workflow & Automation

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **2.1 Non-Interactive Run** | `ldm run [id] -y --tag [tag]` | Starts project without any interactive prompts. |
| **2.2 Non-Interactive Env** | `ldm env [id] TEST=VAL -y` | Sets environment variable without service selection prompts. |
| **2.3 Ghost Mounts** | Run on new project | LDM proactively creates essential directories before bind-mounting. |
| **2.4 WSL Browser** | `ldm run` in WSL | Automatically opens the Windows host browser without "UNC path" warnings. |

### Phase 3: Orchestration Stability

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **3.1 Intel Mac Discovery** | `ldm run` on x86_64 Mac | Correctly prefers legacy `docker-compose` if v2 plugin is broken. |
| **3.2 Env Uniqueness** | Add redundant vars | Generated `docker-compose.yml` contains only unique environment keys. |
| **3.3 Fail-Fast Logic** | Remove docker-compose | LDM commands stop immediately with helpful installation hints. |

### Phase 4: Maintenance & Hygiene

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **4.1 Non-Interactive Prune** | `ldm prune -y` | Silently removes orphaned resources without confirmation. |
| **4.2 SSL Hygiene** | Run `ldm down` | Correctly removes certificates and Traefik routing configs. |
| **4.3 SSL Renewal** | `ldm renew-ssl` | Surgically replaces existing certificates with fresh 2-year versions. |
| **4.4 Search Migration**| `ldm migrate-search`| Deletes internal indices and applies Global ES configs while stopped. |

---

## 🛡️ Supported Environments

The following environments are actively verified as part of our testing lifecycle:

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Hardening | Verified |
| :--- | :--- | :--- | :--- | :--- |
| **Apple Silicon** | macOS 14+ | **OrbStack** | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | ✅ |
| **Apple Silicon** | macOS 14+ | **Docker Desktop** | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=apple) | ✅ |
| **Apple Silicon** | macOS 14+ | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ |
| **Apple Intel** | macOS 12+ | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ |
| **Windows PC** | Windows 11 | **Native WSL2** | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | ✅ |
| **Windows PC** | Windows 11 | **Docker Desktop** | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=windows) | ✅ |
| **Linux Workstation** | Fedora 43 | **Native Docker** | ![Fedora](https://img.shields.io/badge/Fedora-Hardened-success?style=flat-square&logo=linux) | ✅ |
| **Linux Node** | Ubuntu 22.04 | **Docker Engine** | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | ✅ |
<!-- COMPATIBILITY_END -->

---

## 🏁 Final Sign-off

* [ ] All tests pass on the target Host OS.
* [ ] `ldm doctor` shows ✅ for all critical components.
* [ ] Site is reachable at `https://<hostname>` with a Green Lock 🔒.
