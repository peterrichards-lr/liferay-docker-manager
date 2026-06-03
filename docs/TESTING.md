# LDM Testing Protocol

## 🛡️ Compatibility (Verified Environments)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.4.0` | ![Colima](https://img.shields.io/badge/Colima-Hardening-FFAB00?style=flat-square&logo=apple) | `2.11.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-colima-pass-708f2f1e.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-colima-pass-708f2f1e.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **OrbStack** `v2.1.1` | `29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo=apple) | `2.11.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-orbstack-pass-6c4fb01e.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-orbstack-pass-6c4fb01e.txt) |
| **Linux Workstation** | Fedora 43 | **Native Docker** | `29.4.1` | ![Linux](https://img.shields.io/badge/Linux-Hardening-success?style=flat-square&logo=linux) | `2.11.2` | ✅ | [verify-linux-workstation-fedora-43-native-docker-pass-cfe4adaf.txt](../references/verification-results/verify-linux-workstation-fedora-43-native-docker-pass-cfe4adaf.txt) |
| **Linux Workstation** | Ubuntu 24.04 | **Native Docker** | `29.3.0` | ![Linux](https://img.shields.io/badge/Linux-Hardening-success?style=flat-square&logo=linux) | `2.11.2` | ✅ | [verify-linux-workstation-ubuntu-24.04-native-docker-pass-15740f3b.txt](../references/verification-results/verify-linux-workstation-ubuntu-24.04-native-docker-pass-15740f3b.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** `v4.35.0` | `29.4.0` | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardening-00C853?style=flat-square&logo=windows) | `2.11.2` | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass-43079b65.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass-43079b65.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** `WSL 2.4.4` | `29.3.0` | ![WSL2](https://img.shields.io/badge/WSL2-Hardening-blue?style=flat-square&logo=windows) | `2.11.2` | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass-10e8e0f2.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-10e8e0f2.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support. ES 8.17.x+ required for Liferay 2025.Q2+ (ES 7 deprecated). |
<!-- COMPATIBILITY_END -->

---

## Phase 1: Tool & Security Readiness

### 🤖 Automated (CI / E2E)

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

## Phase 2: Global Infrastructure

### 🤖 Automated (E2E)

| ID | Test Case | Steps | Expected Outcome |
| :-- | :--------------------- | :---------------------------------- | :------------------------------------------ |
| 2.1 | **Infra Setup** | `ldm infra-setup --search` | Starts Traefik and ES8. Idempotent. |

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
| 3.4 | **Hot Deploy (OSGi)** | Drop `test-bundle.jar` into `deploy/` | Verifies deployment via Gogo shell (`lb`). |

### 🎭 Standalone UI Testing

UI health checks have been decoupled from the strict binary E2E suite to prevent false negatives caused by host VM rendering delays.

* **Run manually:** `python3 scripts/test_ui.py` (Verifies portal login and Control Panel navigation).

### 🛠️ Manual

| ID | Test Case | Steps | Expected Outcome |
| :-- | :---------------- | :---------------------------------- | :--------------------------------------- |
| 3.1 | **Explicit Init** | `ldm init test-init -y --tag-latest` | Scaffolds folders/metadata immediately. |
| 3.2 | **Missing Tag Guard** | `ldm run test-fail -y` (no tag) | Fails gracefully with "No tag specified". |
| 3.4 | **Project Collision** | `ldm init test-init` (in different dir) | Blocks; identifies original path. |
| 3.5 | **Hostname Collision** | `ldm run --host-name existing.local` | Blocks execution due to registry conflict. |
| 3.6 | **Captcha Switch** | `ldm init test-captcha --no-captcha` | Generates OSGi config and portal property to disable CAPTCHA. Reversible by running without the flag. |
| 3.7 | **Fast Login Switch** | `ldm run test-fast --fast-login` | Applies properties to bypass terms of use and password reset prompts. Warns if used with `--db hypersonic`. |
| 3.8 | **Feature Flags Switch** | `ldm run test-feature --feature dev LPS-122920` | Generates portal properties to enable specific Liferay feature flags. |
| 3.10 | **License Discovery** | Drop `.xml` into `deploy/` | Doctor identifies the XML as a license. |
| 3.10 | **Sample Hydration** | `ldm init test-samples --samples` | Scaffolds project and populates samples. |
| 3.11 | **Import Integrity** | `ldm import source.zip` | Verifies `source.zip.sha256` before extraction. |

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
| 5.5 | **Verification Bypass** | `ldm restore test-init --no-verify` | Restores tampered snapshot without error. |

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
