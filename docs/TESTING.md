# 🧪 LDM Test & Validation Strategy

To ensure that the Liferay Docker Manager (LDM) remains "Industrial-Grade," it is rigorously tested across macOS (Colima/OrbStack/Docker Desktop), Windows (Native/WSL2), and Linux.

## 🛡️ Compatibility Matrix (Verified Environments)

| Architecture | Host OS | Docker Provider | Hardening | Verified |
| :--- | :--- | :--- | :--- | :--- |
| **Apple Silicon** | macOS 14+ | **OrbStack** | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | ✅ |
| **Apple Silicon** | macOS 14+ | **Docker Desktop** | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=apple) | ✅ |
| **Apple Intel/M** | macOS 13+ | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ |
| **Windows PC** | Windows 11 | **Native WSL2** | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | ✅ |
| **Linux Node** | Ubuntu 22.04 | **Docker Engine** | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | ✅ |

### Reference Hardware & Lab Specs

The following physical machines are used for the official verification of LDM releases:

* **MacBook Pro (Apple Silicon)**: Apple M1 Pro, 32 GB RAM, macOS Tahoe 26.4 (25E246).
* **Windows Workstation (x86_64)**: Intel(R) Core(TM) i7-7800X CPU @ 3.50GHz, 16GB RAM, Windows 11 Pro (23H2).
* **Linux Workstation (Native)**: Apple MacBook Pro 11,3, Intel Core i7-4960HQ x 8, 16GB RAM, Fedora Linux 43 (Workstation Edition).

---

## 🏗️ The Four-Tier Testing Methodology

We test the LDM against the four "Pillars of Demo Success" to guarantee it works on your laptop exactly as it does in our lab.

### 1. The "Cold Start" Test (Orchestration)

* **Goal:** Verify LDM can pull images and start containers from a zero-state environment.
* **Validation:** Run `ldm run [project]`. The site must be reachable at its custom hostname within 5 minutes.

### 2. Volume & Permission Test (Hardening)

* **Goal:** Ensure host files are correctly mapped and writable by the `liferay` user.
* **Validation:** LDM must successfully run its "Permission Fixer" and verify mounts via a token check on macOS/Colima.

### 3. Client Extension Lifecycle (Logic)

* **Goal:** Confirm that SSCE builds and routing are healthy.
* **Validation:** User must be able to hot-deploy a CX zip and see the subdomain (e.g. `ext.forge.demo`) resolve.

### 4. The "Portability" Test (Cross-Platform)

* **Goal:** Ensure a **Snapshot** created on one OS works perfectly when transferred to another.
* **Validation:** Export a bundle on macOS and run it on Windows. Branding and data must remain consistent.

---

## 📋 Functional Validation Checklist (v1.5.6)

### Phase 1: Environment & Diagnostics

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **1.1 LDM Doctor** | Run `ldm doctor` | Correctly identifies Docker CPUs/RAM. Reports "Global Config" status. Missing `liferay-net` is a **Warning** (not error) with setup hints. |
| **1.2 DNS Alignment** | Point host to wrong IP | `ldm doctor` warns if hostname doesn't match Traefik's bound IP. |
| **1.3 Infra Setup** | `ldm infra-setup --search` | Starts Traefik (on 0.0.0.0) and ES8 sidecar. Idempotent. |

### Phase 2: Developer Workflow

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **2.1 init-common** | `ldm init-common` | Creates `common/` folder in **Current Directory** with gold-standard assets. |
| **2.2 Ghost Mounts** | Run on new project | LDM proactively creates `data/`, `deploy/`, etc. |
| **2.3 WSL Browser** | `ldm run` in WSL | Automatically opens the Windows host browser without "UNC path" warnings. |

### Phase 3: Switching Engines (macOS)

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **3.1 Engine Cleanup** | `ldm infra-down` before switch | Clears the macOS socket bridge and global proxy from the active engine. |
| **3.2 Context Swap** | Stop Engine A, Start B | `ldm doctor` identifies the new provider correctly. |

### Phase 4: Teardown & Hygiene

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **4.1 SSL Hygiene** | Run `ldm down` | Correctly removes the certificates referenced in the project metadata (`ssl_cert`) and the matching Traefik YAML. |
| **4.2 Prune Logic** | `ldm prune` | Safely identifies and removes orphaned containers without affecting active projects. |

---

## 🏁 Final Sign-off

* [ ] All tests pass on the target Host OS.
* [ ] `ldm doctor` shows ✅ for all critical components.
* [ ] Site is reachable at `https://<hostname>` with a Green Lock 🔒.
