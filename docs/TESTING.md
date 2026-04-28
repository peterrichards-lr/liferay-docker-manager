# LDM Testing Protocol

## 🛡️ Compatibility (Verified Environments)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Hardening | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 12 | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ❌ | [verify-apple-intel-macos-12-colima-fail-b7010ae7.txt](../references/verification-results/verify-apple-intel-macos-12-colima-fail-b7010ae7.txt) |
| **Apple Intel** | macOS 12 Monterey | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ | [verify-apple-intel-macos-12-monterey-colima-pass-b8fa44ff.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-colima-pass-b8fa44ff.txt) |
| **Apple Silicon** | macOS 16 | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ | [verify-apple-silicon-macos-16-colima-pass-597605e2.txt](../references/verification-results/verify-apple-silicon-macos-16-colima-pass-597605e2.txt) |
| **Apple Silicon** | macOS 16 | **OrbStack** | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | ✅ | [verify-apple-silicon-macos-16-orbstack-pass-b36484d1.txt](../references/verification-results/verify-apple-silicon-macos-16-orbstack-pass-b36484d1.txt) |
| **Apple Silicon** | macOS 17 | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ | [verify-apple-silicon-macos-17-colima-pass-e1a2a2e1.txt](../references/verification-results/verify-apple-silicon-macos-17-colima-pass-e1a2a2e1.txt) |
| **Linux Workstation** | Linux | **Native Docker** | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | ✅ | [verify-linux-workstation-linux-native-docker-pass-842252b5.txt](../references/verification-results/verify-linux-workstation-linux-native-docker-pass-842252b5.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass-d02fbff2.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-d02fbff2.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |
<!-- COMPATIBILITY_END -->

## Phase 1: Tool & Security Readiness

*Verifies the tool's integrity and basic help systems before any infrastructure is touched.*

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

## Phase 2: Global Infrastructure

*Verifies the shared Traefik and Elasticsearch components.*

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

## Phase 3: Project Lifecycle (Init & Seeding)

*Verifies project creation, seeding, and collision detection.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :--- | :--------------------- | :------------------------------------------------- | :-------------------------------------------------------------------- | :------------------------------------------ |
| 3.1 | **Explicit Init** | `ldm init test-init -y --tag-latest` | Verify a new folder `test-init` is created. | Scaffolds folders/metadata immediately. |
| 3.2 | **Missing Tag Guard** | `ldm run test-fail -y` | Run without `--tag-latest` or `-t`. | Fails gracefully with "No Liferay tag specified." |
| 3.3 | **Fresh Project Run** | `ldm run test-run -y --tag-latest` | Starts a fresh project without prior init. | Starts project from seed without prompts. |
| 3.4 | **Project Collision** | `ldm init test-init` | Run this in a *different* directory than 3.1. | Blocks; identifies original path. |
| 3.5 | **Hostname Collision** | `ldm run --host-name test-init.local` | Use a hostname already assigned to another project. | Blocks execution due to registry conflict. |
| 3.6 | **Captcha Switch** | `ldm init test-captcha -y --tag-latest --no-captcha` | Check `osgi/configs/com.liferay.captcha...config`. | Generates config to disable Omni-Admin CAPTCHA. |
| 3.7 | **Ghost Mounts** | `ls -R test-init/osgi` | Check for `state/` and `configs/` folders. | Dirs created by LDM before Docker mounts. |
| 3.8 | **Memory Units** | `grep "memory:" test-init/docker-compose.yml` | Set `--mem-limit 2048` during run. | YAML uses `2048M` (Mandatory unit). |
| 3.9 | **License Discovery** | `ldm doctor test-init` | Drop any `.xml` file into `test-init/deploy/`. | Doctor identifies the XML as a license. |
| 3.10 | **Sample Hydration** | `ldm init test-samples --samples -y --tag-latest` | Check `test-samples/client-extensions/` for ZIP files. | Scaffolds project and populates sample assets. |

## Phase 4: Runtime Configuration & UX

*Verifies managing a running project and the user experience.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :-- | :------------------ | :--------------------------- | :----------------------------------------------------- | :------------------------------------------ |
| 4.1 | **Env Sync** | `ldm env test-init KEY=VAL` | Check `docker-compose.yml` environment section. | YAML updated immediately without `run`. |
| 4.2 | **Redaction Check** | `ldm -v run test-init` | Set `LIFERAY_DB_PASSWORD=secret` in metadata first. | Secret is masked as `[REDACTED]` in logs. |
| 4.3 | **WSL Browser** | `ldm run test-init` | **(Windows Only)** Check if default browser opens. | Opens host browser without UNC errors. |
| 4.4 | **Intermixed Flags** | `ldm ps -y test-init` | Verify it doesn't prompt for project selection. | Global `-y` recognized after subcommand. |
| 4.5 | **Fail-Fast Logic** | Delete `docker-compose.yml` | Run `ldm logs` on a project missing its compose file. | LDM stops with "Not a project" error. |

## Phase 5: Data Integrity & Recovery

*Verifies snapshots, restoration, and the new SHA-256 mandates.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :-- | :---------------------- | :-------------------------------- | :-------------------------------------------------- | :------------------------------------------ |
| 5.1 | **SHA-256 Generation** | `ldm snapshot test-init` | Check `snapshots/<timestamp>/` for `.sha256` file. | File contains valid hash of `files.tar.gz`. |
| 5.2 | **SHA-256 Verification** | `ldm restore test-init` | Verify logs show "Snapshot integrity verified". | Checksum checked before extraction. |
| 5.3 | **Corruption Guard** | Modify snapshot archive | `echo "bad" >> snapshots/files.tar.gz` | `ldm restore` fails with integrity error. |
| 5.4 | **Project Reset** | `ldm reset state test-init` | Verify `osgi/state` is emptied (but folder remains). | Clears state while container is stopped. |

## Phase 6: Advanced Integrations

*Verifies complex scaling and external sync.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :-- | :---------------------- | :-------------------------------- | :------------------------------------------------- | :------------------------------------------ |
| 6.1 | **Multi-Node Scaling** | `ldm scale test-init liferay=2` | Check `docker-compose.yml` for `LIFERAY_CLUSTER_LINK`. | Disables host-mapping; injects cluster env. |
| 6.2 | **Search Migration** | `ldm migrate-search` | Switch a "Sidecar" project to "Shared" search. | Reconfigures ES settings automatically. |
| 6.3 | **Cloud Env Sync** | `ldm cloud-fetch project-id` | Requires active `lcp login` on the host. | Fetches and merges remote variables. |

## Phase 7: Cleanup & Pruning

*Verifies teardown and filesystem hygiene.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :-- | :---------------------- | :------------------------------- | :------------------------------------------------- | :------------------------------------------ |
| 7.1 | **Registry Cleanup** | `ldm down test-init --delete` | Check `ldm ls` output. | Project removed from the global registry. |
| 7.2 | **SSL Hygiene** | `ls ~/.ldm/infra/proxy/` | Check for leftover routing `.toml` files. | Configs removed after project teardown. |
| 7.3 | **Non-Interactive Prune** | `ldm prune -y` | Delete a project folder manually, then run prune. | Silently removes orphaned containers. |
| 7.4 | **Self-Healing Registry** | `rm -rf test-init` | Manually delete folder, then run `ldm ls`. | dead path detected and pruned from registry. |
