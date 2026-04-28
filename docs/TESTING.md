# LDM Testing Protocol

## 🛡️ Compatibility (Verified Environments)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Hardening | Verified | LDM Version | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 12 Monterey | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ❌ | `v2.4.26-beta.46` | [verify-apple-intel-macos-12-monterey-colima-fail-b7010ae7.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-colima-fail-b7010ae7.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ | `v2.4.26-beta.67` | [verify-apple-silicon-macos-26-tahoe-colima-pass-bfc80857.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-colima-pass-bfc80857.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **OrbStack** | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | ✅ | `v2.4.26-beta.43` | [verify-apple-silicon-macos-26-tahoe-orbstack-pass-b36484d1.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-orbstack-pass-b36484d1.txt) |
| **Linux Workstation** | Linux | **Native Docker** | ![Linux](https://img.shields.io/badge/Linux-Hardened-success?style=flat-square&logo=linux) | ✅ | `v2.4.26-beta.67` | [verify-linux-workstation-linux-native-docker-pass-82bba80c.txt](../references/verification-results/verify-linux-workstation-linux-native-docker-pass-82bba80c.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | ✅ | `v2.4.26-beta.31` | [verify-windows-pc-windows-11-native-wsl2-pass-d02fbff2.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-d02fbff2.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |
<!-- COMPATIBILITY_END -->

---

## Phase 1: Tool & Security Readiness

### 🤖 Automated (CI / E2E)

<<<<<<< HEAD
| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :-- | :--------------------- | :---------------------------------------------- | :-------------------------------------------------------------------------- | :------------------------------------------ |
| 1.1 | **No-Sudo Guard** | `sudo ldm run` | Run as root to trigger the safety gate. | Command blocked with security warning. |
| 1.2 | **Exit Code Integrity** | `ldm doctor --skip-project; echo $?` | Check the shell return code directly. | Returns `0` if healthy, `1` if critical. |
| 1.3 | **Shell Completion** | `ldm completion zsh` | Verify the output contains completion functions. | Generates a valid script. |
| 1.4 | **Native Manual** | `man ldm` | Requires `ldm init-common` to have run once. | Manual page opens correctly. |
| 1.5 | **Self-Repair** | `ldm upgrade --repair -y` | Simulated by running on a source-install (will show "Source" warning). | Successfully reaches preparation phase. |
<<<<<<< HEAD
=======
| 1.6 | **Version Management** | `LDM_DEV_MODE=true ldm version --bump beta -y` | Requires source clone. Verify `constants.py` and `pyproject.toml` are updated. | Increments beta version atomically. |
| 1.7 | **Version Promotion** | `LDM_DEV_MODE=true ldm version --promote -y` | Run after 1.6. Verify beta suffix is removed. | Prompts for promotion; results in stable version. |
| 1.8 | **Dev Guardrails** | `ldm version --bump patch` | Run from a non-git directory or without `LDM_DEV_MODE`. | Blocks execution with safety warning. |
| 1.9 | **Safety Hatch** | `ldm upgrade` | Run while on a beta version. | Prompts to switch back to stable tier. |
>>>>>>> 8b0a863 (feat: implement DNS cleanup and stable tier safety hatch [pre-release])
=======
| ID | Test Case | Steps | Expected Outcome |
| :-- | :--------------------- | :---------------------------------------------- | :------------------------------------------ |
| 1.2 | **Exit Code Integrity** | `ldm doctor --skip-project; echo $?` | Returns `0` if healthy, `1` if critical. |
| 1.3 | **Shell Completion** | `ldm completion zsh` | Generates a valid script. |
| 1.5 | **Self-Repair** | `ldm upgrade --repair -y` | Successfully reaches preparation phase. |

### 🛠️ Manual

| ID | Test Case | Steps | Expected Outcome |
| :-- | :--------------------- | :---------------------------------------------- | :------------------------------------------ |
| 1.1 | **No-Sudo Guard** | `sudo ldm run` | Command blocked with security warning. |
| 1.4 | **Native Manual** | `man ldm` (Run `ldm init-common` first) | Manual page opens correctly. |
| 1.6 | **Version Management** | `LDM_DEV_MODE=true ldm version --bump beta -y` | Increments beta version atomically. |
| 1.7 | **Version Promotion** | `LDM_DEV_MODE=true ldm version --promote -y` | Prompts for promotion; results in stable. |
| 1.8 | **Dev Guardrails** | `ldm version --bump patch` | Blocks execution with safety warning. |
| 1.9 | **Safety Hatch** | `ldm upgrade` (Run while on beta) | Prompts to switch back to stable tier. |

---
>>>>>>> 4561e48 (docs: restructure TESTING.md into automated and manual sections)

## Phase 2: Global Infrastructure

### 🤖 Automated (E2E)

<<<<<<< HEAD
| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :-- | :--------------------- | :---------------------------------- | :-------------------------------------------------------------------------------------------------------------------- | :------------------------------------------ |
| 2.1 | **Infra Setup** | `ldm infra-setup --search` | Monitor `docker ps` to see new global containers. | Starts Traefik and ES8. Idempotent. |
| 2.2 | **Infra Restart** | `ldm infra-restart --search` | Monitor `docker ps` uptime. | Restarts Traefik and ES8 cleanly. |
| 2.3 | **DNS Alignment** | Point host to wrong IP | **Requires a project.** Run `ldm init test-dns --host-name broken.local -y --tag-latest`. Edit `/etc/hosts` and point `broken.local` to `10.0.0.99`. | `ldm doctor` warns about IP mismatch. |
<<<<<<< HEAD
| 2.4 | **Auto-Healing DNS** | `ldm doctor --fix-hosts` | Run this while the sabotage from 2.3 is active. | Prompts for sudo; fixes the entry. |
=======
| 2.4 | **Auto-Healing DNS** | `ldm fix-hosts broken.local` | Run this while the sabotage from 2.3 is active. | Prompts for sudo; fixes the entry. |
| 2.5 | **Doctor DNS Fix** | `ldm doctor --fix-hosts` | Repeat sabotage from 2.3, then run doctor. | Batch fixes all missing entries. |
| 2.6 | **DNS Cleanup (Surgical)** | `ldm rm test-dns --clean-hosts` | Run after 2.1. Check hosts file for remaining entries. | Removes specific project entries. |
| 2.7 | **DNS Cleanup (Global)** | `ldm prune --clean-hosts` | Run after 7.4. Check hosts file. | Removes ALL LDM-managed entries. |
>>>>>>> 8b0a863 (feat: implement DNS cleanup and stable tier safety hatch [pre-release])
=======
| ID | Test Case | Steps | Expected Outcome |
| :-- | :--------------------- | :---------------------------------- | :------------------------------------------ |
| 2.1 | **Infra Setup** | `ldm infra-setup --search` | Starts Traefik and ES8. Idempotent. |
>>>>>>> 4561e48 (docs: restructure TESTING.md into automated and manual sections)

### 🛠️ Manual

| ID | Test Case | Steps | Expected Outcome |
| :-- | :--------------------- | :---------------------------------- | :------------------------------------------ |
| 2.2 | **Infra Restart** | `ldm infra-restart --search` | Restarts Traefik and ES8 cleanly. |
| 2.3 | **DNS Alignment** | Edit `/etc/hosts` with wrong IP | `ldm doctor` warns about IP mismatch. |
| 2.4 | **Auto-Healing DNS** | `ldm fix-hosts broken.local` | Prompts for sudo; fixes the entry. |
| 2.5 | **Doctor DNS Fix** | `ldm doctor --fix-hosts` | Batch fixes all missing entries. |
| 2.6 | **DNS Cleanup (Surg)** | `ldm rm test-dns --clean-hosts` | Removes specific project host entries. |
| 2.7 | **DNS Cleanup (Glob)** | `ldm prune --clean-hosts` | Removes ALL LDM-managed host entries. |

---

## Phase 3: Project Lifecycle

### 🤖 Automated (E2E)

| ID | Test Case | Steps | Expected Outcome |
| :-- | :--------------------- | :---------------------------------- | :------------------------------------------ |
| 3.3 | **Fresh Project Run** | `ldm run test-run -y --tag-latest` | Starts a fresh project from seed. |

### 🛠️ Manual

| ID | Test Case | Steps | Expected Outcome |
| :--- | :--------------------- | :------------------------------------------------- | :------------------------------------------ |
| 3.1 | **Explicit Init** | `ldm init test-init -y --tag-latest` | Scaffolds folders/metadata immediately. |
| 3.2 | **Missing Tag Guard** | `ldm run test-fail -y` (no tag) | Fails gracefully with "No tag specified". |
| 3.4 | **Project Collision** | `ldm init test-init` (in different dir) | Blocks; identifies original path. |
| 3.5 | **Hostname Collision** | `ldm run --host-name existing.local` | Blocks execution due to registry conflict. |
| 3.6 | **Captcha Switch** | `ldm init test-captcha --no-captcha` | Generates config to disable CAPTCHA. |
| 3.9 | **License Discovery** | Drop `.xml` into `deploy/` | Doctor identifies the XML as a license. |
| 3.10| **Sample Hydration** | `ldm init test-samples --samples` | Scaffolds project and populates samples. |

---

## Phase 4: Runtime Configuration & UX

### 🤖 Automated (E2E)

| ID | Test Case | Steps | Expected Outcome |
| :-- | :------------------ | :--------------------------- | :------------------------------------------ |
| 4.5 | **Fail-Fast Logic** | Delete `docker-compose.yml` | `ldm logs` stops with "Not a project". |

### 🛠️ Manual

| ID | Test Case | Steps | Expected Outcome |
| :-- | :------------------ | :--------------------------- | :------------------------------------------ |
| 4.1 | **Env Sync** | `ldm env test-init KEY=VAL` | YAML updated immediately without `run`. |
| 4.2 | **Redaction Check** | `ldm -v run test-init` (w/ secret) | Secret is masked as `[REDACTED]` in logs. |
| 4.3 | **WSL Browser** | `ldm run test-init` (WSL Only) | Opens host browser without UNC errors. |
| 4.4 | **Intermixed Flags** | `ldm ps -y test-init` | Global `-y` recognized after subcommand. |

---

## Phase 5: Data Integrity & Recovery

### 🤖 Automated (E2E)

| ID | Test Case | Steps | Expected Outcome |
| :-- | :---------------------- | :---------- | :------------------------------------------ |
| 5.1 | **SHA-256 Generation** | `ldm snapshot test-init` | File contains valid hash of `files.tar.gz`. |
| 5.2 | **SHA-256 Verify** | `ldm restore test-init` | Verify logs show "Integrity verified". |

### 🛠️ Manual

| ID | Test Case | Steps | Expected Outcome |
| :-- | :---------------------- | :---------- | :------------------------------------------ |
| 5.3 | **Corruption Guard** | Corrupt `files.tar.gz` manually | `ldm restore` fails with integrity error. |
| 5.4 | **Project Reset** | `ldm reset state test-init` | Clears state while container is stopped. |

---

## Phase 6: Advanced Integrations

### 🛠️ Manual

| ID | Test Case | Steps | Expected Outcome |
| :-- | :---------------------- | :---------- | :------------------------------------------ |
| 6.1 | **Multi-Node Scaling** | `ldm scale test-init liferay=2` | Disables host-mapping; injects cluster. |
| 6.2 | **Search Migration** | `ldm migrate-search` | Reconfigures ES settings automatically. |
| 6.3 | **Cloud Env Sync** | `ldm cloud-fetch project-id` | Fetches and merges remote variables. |

---

## Phase 7: Cleanup & Pruning

### 🤖 Automated (E2E)

| ID | Test Case | Steps | Expected Outcome |
| :-- | :---------------------- | :---------- | :------------------------------------------ |
| 7.1 | **Registry Cleanup** | `ldm down test-init --delete` | Project removed from the global registry. |

### 🛠️ Manual

| ID | Test Case | Steps | Expected Outcome |
| :-- | :---------------------- | :---------- | :------------------------------------------ |
| 7.2 | **SSL Hygiene** | Check `~/.ldm/infra/proxy/` | Configs removed after project teardown. |
| 7.3 | **Non-Interactive Prune** | `ldm prune -y` | Silently removes orphaned containers. |
| 7.4 | **Self-Healing Reg** | Delete project folder manually | Dead path detected and pruned from registry. |
