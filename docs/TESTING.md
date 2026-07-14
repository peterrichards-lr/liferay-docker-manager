# LDM Testing Protocol

## 🛡️ Compatibility (Verified Environments)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 12 Monterey | **OrbStack** `v1.5.1` | `v25.0.5` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo=apple) | `2.4.26-pre.13` | ✅ | [verify-apple-intel-macos-12-monterey-orbstack-pass.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-orbstack-pass.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardening-FFAB00?style=flat-square&logo=apple) | `2.15.16-pre.11` | ✅ | [verify-apple-silicon-macos-15-sequoia-colima-pass.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-colima-pass.txt) |
| **Linux Workstation** | Linux | **Unknown** | `29.4.0` | `Unknown` | `2.15.16-pre.11` | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass.txt) |

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

---

## 🧪 Unit Testing & Mocking Guidelines

To prevent test-runner hangs, memory exhaustion, and side-effect leakage in CI pipelines, follow these guidelines when writing unit tests:

1. **Avoid Global built-in / Path Mocking**:
   * Do not globally mock classes or standard libraries like `pathlib.Path.exists`, `pathlib.Path.read_text`, `builtins.open`, or `shutil.rmtree`.
   * Patching standard library methods globally can corrupt Python's internal mechanisms (such as timezone updates in `time.strftime` or mock tracking in `unittest.mock`) and lead to infinite recursion.
2. **Prefer Real Filesystem Sandboxing**:
   * Use `tempfile.TemporaryDirectory` to create actual sandbox environments for tests.
   * Let LDM interact with real, lightweight files on disk. The cleanups inside LDM and `TemporaryDirectory` contexts will automatically ensure that no files are left behind.
3. **Use Mock Side-Effects for Specific Interceptions**:
   * If files need to be simulated, write a side effect for a specific dependency (like mocking `safe_extract` to write dummy meta files directly to the temporary directory).

---

## 🚀 Local E2E Platform Verification Scripts (Multi-OS)

To verify the complete container lifecycle, volume mount synchronization, and CLI options natively on local developer machines:

### **1. macOS & Linux**

Run the Bash E2E verification script:

```bash
bash scripts/verify_e2e_refactor.sh
```

### **2. Windows**

Run the PowerShell E2E verification script (ensure your PowerShell ExecutionPolicy permits running scripts):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_e2e_refactor.ps1
```

### **What the scripts verify**

* Docker daemon connectivity and registry cleanups.
* Full project initialization, compose generation, and database sidecar startup.
* Automated snapshot extraction, integrity verification (SHA-256 signature generation), and directory structure restores.
* Metadata namespacing and port collision handling (confirming the **`ldm fork`** command works cleanly without conflicts).
* Teardown of resources and network isolation.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-13* | *Last Reviewed: 2026-07-02*
