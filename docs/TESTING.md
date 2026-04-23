# LDM Testing & Validation Strategy

This document outlines the strategy for ensuring Liferay Docker Manager (LDM) remains stable across its supported physical lab (Silicon/Intel Mac, Windows/WSL2, Native Linux).

## 🧪 Automated Testing & Strategy

LDM uses a multi-layered automated verification suite:

1. **Unit Tests (Pytest):** Core logic verification for path resolution, version parsing, and configuration generation.
2. **Security Scans (Bandit):** Automated detection of common security issues.
3. **CI Smoke Tests:** GitHub Actions verifying initialization, doctor, and upgrades.
4. **Contract-Based Verification**: `test_architectural_contracts.py` verifies output (YAML/properties) against mandates.

### 🏗️ Architectural Mandates

All refactoring must preserve the following LDM contracts:

- **Metadata DNA**: Containers MUST have the `com.liferay.ldm.project` label.
- **Domain Trust**: Custom hostnames MUST trigger proactive **Environment Variable** injection.
- **Search Hardening**: Sidecar ES MUST be disabled when Shared Search is active.
- **Database Reliability**: JDBC settings MUST stay in `portal-ext.properties`.
- **Persistence**: `osgi/state` must remain host-mapped for single-node instances.

---

## 📋 Functional Validation Checklist (v2.4.0+)

This checklist is ordered sequentially to minimize environment setup overhead. Follow the phases in order.

### Phase 1: Tool & Security Readiness

*Verifies the tool's integrity and basic help systems before any infrastructure is touched.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :--- | :--- | :--- | :--- | :--- |
| 1.1 | **No-Sudo Guard** | `sudo ldm run` | Run as root to trigger the safety gate. | Command blocked with security warning. |
| 1.2 | **Exit Code Integrity**| `ldm doctor --skip-project; echo $?` | Check the shell return code directly. | Returns `0` if healthy, `1` if critical. |
| 1.3 | **Shell Completion** | `ldm completion zsh` | Verify the output contains completion functions. | Generates a valid script. |
| 1.4 | **Native Manual** | `man ldm` | Requires `ldm init-common` to have run once. | Manual page opens correctly. |
| 1.5 | **Self-Repair** | `ldm upgrade --repair -y` | Simulated by running on a source-install (will show "Source" warning). | Successfully reaches preparation phase. |

### Phase 2: Global Infrastructure

*Verifies the shared Traefik and Elasticsearch components.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :--- | :--- | :--- | :--- | :--- |
| 2.1 | **Infra Setup** | `ldm infra-setup --search` | Monitor `docker ps` to see new global containers. | Starts Traefik and ES8. Idempotent. |
| 2.2 | **Infra Restart** | `ldm infra-restart --search` | Monitor `docker ps` uptime. | Restarts Traefik and ES8 cleanly. |
| 2.3 | **DNS Alignment** | Point host to wrong IP | **Requires a project.** Run `ldm init test-dns --host-name broken.local -y --tag-latest`. Edit `/etc/hosts` and point `broken.local` to `10.0.0.99`. | `ldm doctor` warns about IP mismatch. |
| 2.4 | **Auto-Healing DNS** | `ldm doctor --fix-hosts` | Run this while the sabotage from 2.3 is active. | Prompts for sudo; fixes the entry. |

### Phase 3: Project Lifecycle (Init & Seeding)

*Verifies project creation, seeding, and collision detection.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :--- | :--- | :--- | :--- | :--- |
| 3.1 | **Explicit Init** | `ldm init test-init -y --tag-latest` | Verify a new folder `test-init` is created. | Scaffolds folders/metadata immediately. |
| 3.2 | **Missing Tag Guard**| `ldm run test-fail -y` | Run without `--tag-latest` or `-t`. | Fails gracefully with "No Liferay tag specified." |
| 3.3 | **Fresh Project Run**| `ldm run test-run -y --tag-latest` | Starts a fresh project without prior init. | Starts project from seed without prompts. |
| 3.4 | **Project Collision** | `ldm init test-init` | Run this in a *different* directory than 3.1. | Blocks; identifies original path. |
| 3.5 | **Hostname Collision**| `ldm run --host-name test-init.local`| Use a hostname already assigned to another project. | Blocks execution due to registry conflict. |
| 3.6 | **Captcha Switch** | `ldm init test-captcha -y --tag-latest --no-captcha`| Check `osgi/configs/com.liferay.captcha...config`. | Generates config to disable Omni-Admin CAPTCHA. |
| 3.7 | **Ghost Mounts** | `ls -R test-init/osgi` | Check for `state/` and `configs/` folders. | Dirs created by LDM before Docker mounts. |
| 3.8 | **Memory Units** | `grep "memory:" test-init/docker-compose.yml` | Set `--mem-limit 2048` during run. | YAML uses `2048M` (Mandatory unit). |
| 3.9 | **License Discovery** | `ldm doctor test-init` | Drop any `.xml` file into `test-init/deploy/`. | Doctor identifies the XML as a license. |
| 3.10| **Sample Hydration** | `ldm init test-samples --samples -y --tag-latest` | Check `test-samples/client-extensions/` for ZIP files. | Scaffolds project and populates sample assets. |

### Phase 4: Runtime Configuration & UX

*Verifies managing a running project and the user experience.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :--- | :--- | :--- | :--- | :--- |
| 4.1 | **Env Sync** | `ldm env test-init KEY=VAL` | Check `docker-compose.yml` environment section. | YAML updated immediately without `run`. |
| 4.2 | **Redaction Check** | `ldm -v run test-init` | Set `LIFERAY_DB_PASSWORD=secret` in metadata first. | Secret is masked as `[REDACTED]` in logs. |
| 4.3 | **WSL Browser** | `ldm run test-init` | **(Windows Only)** Check if default browser opens. | Opens host browser without UNC errors. |
| 4.4 | **Intermixed Flags** | `ldm ps -y test-init` | Verify it doesn't prompt for project selection. | Global `-y` recognized after subcommand. |
| 4.5 | **Fail-Fast Logic** | Delete `docker-compose.yml`| Run `ldm logs` on a project missing its compose file. | LDM stops with "Not a project" error. |

### Phase 5: Data Integrity & Recovery

*Verifies snapshots, restoration, and the new SHA-256 mandates.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :--- | :--- | :--- | :--- | :--- |
| 5.1 | **SHA-256 Generation** | `ldm snapshot test-init` | Check `snapshots/<timestamp>/` for `.sha256` file. | File contains valid hash of `files.tar.gz`. |
| 5.2 | **SHA-256 Verification**| `ldm restore test-init` | Verify logs show "Snapshot integrity verified". | Checksum checked before extraction. |
| 5.3 | **Corruption Guard** | Modify snapshot archive | `echo "bad" >> snapshots/files.tar.gz` | `ldm restore` fails with integrity error. |
| 5.4 | **Project Reset** | `ldm reset state test-init`| Verify `osgi/state` is emptied (but folder remains). | Clears state while container is stopped. |

### Phase 6: Advanced Integrations

*Verifies complex scaling and external sync.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :--- | :--- | :--- | :--- | :--- |
| 6.1 | **Multi-Node Scaling** | `ldm scale test-init liferay=2`| Check `docker-compose.yml` for `LIFERAY_CLUSTER_LINK`. | Disables host-mapping; injects cluster env. |
| 6.2 | **Search Migration** | `ldm migrate-search` | Switch a "Sidecar" project to "Shared" search. | Reconfigures ES settings automatically. |
| 6.3 | **Cloud Env Sync** | `ldm cloud-fetch project-id` | Requires active `lcp login` on the host. | Fetches and merges remote variables. |

### Phase 7: Cleanup & Pruning

*Verifies teardown and filesystem hygiene.*

| ID | Test Case | Steps | Validation Pointers | Expected Outcome |
| :--- | :--- | :--- | :--- | :--- |
| 7.1 | **Registry Cleanup** | `ldm down test-init --delete` | Check `ldm ls` output. | Project removed from the global registry. |
| 7.2 | **SSL Hygiene** | `ls ~/.ldm/infra/proxy/` | Check for leftover routing `.toml` files. | Configs removed after project teardown. |
| 7.3 | **Non-Interactive Prune**| `ldm prune -y` | Delete a project folder manually, then run prune. | Silently removes orphaned containers. |
| 7.4 | **Self-Healing Registry**| `rm -rf test-init` | Manually delete folder, then run `ldm ls`. | dead path detected and pruned from registry. |

---

## 🏁 Final Sign-off

- [ ] All tests pass on the target Host OS.
- [ ] `ldm doctor` shows ✅ for all critical components.
- [ ] Site is reachable at `https://<hostname>` with a Green Lock 🔒.
