# 🧪 LDM Test & Validation Strategy

To ensure that the Liferay Docker Manager (LDM) remains "Industrial-Grade," it is rigorously tested across macOS (Colima/OrbStack/Docker Desktop), Windows (Native/WSL2), and Linux.

## 🛡️ Compatibility Matrix (Verified Environments)

| Architecture | Host OS | Docker Provider | Status |
| :--- | :--- | :--- | :--- |
| **Apple Silicon** | macOS 14+ | **OrbStack** | ![macOS-Silicon-Orb](https://img.shields.io/badge/macOS_Silicon-OrbStack-success?style=flat-square&logo=apple&logoColor=white&color=00B0FF) |
| **Apple Silicon** | macOS 14+ | **Docker Desktop** | ![macOS-Silicon-DD](https://img.shields.io/badge/macOS_Silicon-Docker_Desktop-success?style=flat-square&logo=apple&logoColor=white&color=00C853) |
| **Apple Intel/M** | macOS 13+ | **Colima** | ![macOS-Intel-Col](https://img.shields.io/badge/macOS-Colima_Hardened-orange?style=flat-square&logo=apple&logoColor=white) |
| **Windows PC** | Windows 11 | **Native WSL2** | ![Windows-WSL2](https://img.shields.io/badge/Windows_11-WSL2_Hardened-blue?style=flat-square&logo=windows&logoColor=white) |
| **Linux Node** | Ubuntu 22.04 | **Docker Engine** | ![Linux-Native](https://img.shields.io/badge/Linux-Native_Docker-success?style=flat-square&logo=linux&logoColor=white&color=333333) |

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
| **1.1 LDM Doctor** | Run `ldm doctor` | Correctly identifies Docker CPUs/RAM. Reports "Global Config" status. |
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

---

## 🏁 Final Sign-off

* [ ] All tests pass on the target Host OS.
* [ ] `ldm doctor` shows ✅ for all critical components.
* [ ] Site is reachable at `https://<hostname>` with a Green Lock 🔒.
