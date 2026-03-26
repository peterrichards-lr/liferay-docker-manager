# 🧪 LDM Test & Validation Strategy

To ensure that the **EcoPulse Smart City Demo** remains "Industrial-Grade," the Liferay Docker Manager (LDM) is rigorously tested across the most common Sales Engineering hardware and software configurations.

## 🛡️ Compatibility Matrix (Verified Environments)

| Architecture | Host OS | Docker Provider | Status |
| :--- | :--- | :--- | :--- |
| **Apple Silicon (M1/2/3)** | macOS 14+ | **OrbStack** (Recommended) | ![macOS-Silicon-Orb](https://img.shields.io/badge/macOS_Silicon-OrbStack-success?style=flat-square&logo=apple&logoColor=white&color=00B0FF) |
| **Apple Silicon (M1/2/3)** | macOS 14+ | **Docker Desktop** | ![macOS-Silicon-DD](https://img.shields.io/badge/macOS_Silicon-Docker_Desktop-success?style=flat-square&logo=apple&logoColor=white&color=00C853) |
| **Apple Intel (x86_64)** | macOS 13+ | **Colima** | ![macOS-Intel-Col](https://img.shields.io/badge/macOS_Intel-Colima-success?style=flat-square&logo=apple&logoColor=white&color=FFAB00) |
| **Windows PC** | Windows 11 | **Native WSL2** | ![Windows-WSL2](https://img.shields.io/badge/Windows_11-WSL2_Native-success?style=flat-square&logo=windows&logoColor=white&color=0078D4) |
| **Linux Node** | Ubuntu 22.04 | **Docker Engine** | ![Linux-Native](https://img.shields.io/badge/Linux-Native_Docker-success?style=flat-square&logo=linux&logoColor=white&color=333333) |

---

## 🏗️ The Four-Tier Testing Methodology

We test the LDM against the four "Pillars of Demo Success" to guarantee it works on your laptop exactly as it does in our lab.

### 1. The "Cold Start" Test (Orchestration)

* **Goal:** Verify LDM can pull images and start containers from a zero-state environment.
* **Validation:** Run `ldm run [project]`. The site must be reachable at `localhost:8080` within 5 minutes.
* **Hardware Focus:** Intel Macs (monitoring for thermal throttling/timeouts).

### 2. The "EcoPulse" Asset Test (Volume Mounting)

* **Goal:** Ensure the **PNG Asset Kit** (Logos/Favicons) is correctly mapped to the Liferay Document Library.
* **Validation:** The "EcoPulse" logo must appear in the site header and browser tab immediately upon first load.
* **Hardware Focus:** Windows WSL2 & macOS Silicon (testing `virtiofs` vs `gRPC` file sharing performance).

### 3. The "Schema & Logic" Test (Objects & Extensions)

* **Goal:** Confirm that the `GreenInitiative` Object and React Client Extensions are deployed and healthy.
* **Validation:** User must be able to submit a record via the "Community Impact" fragment and see it in the Liferay Backoffice.
* **Hardware Focus:** OrbStack & Colima (ensuring Client Extension compilation doesn't hit memory limits).

### 4. The "Portability" Test (Cross-Platform)

* **Goal:** Ensure a **Snapshot** created on Apple Silicon works perfectly when transferred to an Intel Mac or Windows PC.
* **Validation:** Export a bundle on ARM64 and run it on x86_64. The branding, data, and logic must remain 100% consistent.

---

## 🩺 Pre-Flight Diagnostics: `ldm-doctor`

Before running a live demo, we recommend running our diagnostic script. It checks for the "Hidden Killers" of Liferay instances:

```bash
./scripts/ldm-doctor.sh
```
