# LDM Testing Protocol

## 🛡️ Compatibility (Verified Environments)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 12 Monterey | **OrbStack** `v1.5.1` | `v25.0.5` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo=apple) | `2.4.26-pre.13` | ✅ | [verify-apple-intel-macos-12-monterey-orbstack-pass.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-orbstack-pass.txt) |
| **Apple Silicon** | macOS 16 Tahoe | **Colima** `v0.10.1` | `29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardening-FFAB00?style=flat-square&logo=apple) | `2.17.0-pre.3` | ✅ | [verify-apple-silicon-macos-16-tahoe-colima-pass.txt](../references/verification-results/verify-apple-silicon-macos-16-tahoe-colima-pass.txt) |
| **Apple Silicon** | macOS 16 Tahoe | **OrbStack** `v2.1.1` | `29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo=apple) | `2.17.0-pre.3` | ✅ | [verify-apple-silicon-macos-16-tahoe-orbstack-pass.txt](../references/verification-results/verify-apple-silicon-macos-16-tahoe-orbstack-pass.txt) |
| **Linux Workstation** | Fedora 44 | **Native Docker** | `28.0.4` | ![Linux](https://img.shields.io/badge/Linux-Hardening-success?style=flat-square&logo=linux) | `2.17.0` | ✅ | [verify-linux-workstation-fedora-44-native-docker-pass.txt](../references/verification-results/verify-linux-workstation-fedora-44-native-docker-pass.txt) |
| **Linux Workstation** | Ubuntu 24.04 | **Native Docker** | `28.0.4` | ![Linux](https://img.shields.io/badge/Linux-Hardening-success?style=flat-square&logo=linux) | `2.17.0` | ✅ | [verify-linux-workstation-ubuntu-24.04-native-docker-pass.txt](../references/verification-results/verify-linux-workstation-ubuntu-24.04-native-docker-pass.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** `v4.35.0` | `29.4.0` | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardening-00C853?style=flat-square&logo=windows) | `2.17.0-pre.3` | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** `WSL 2.4.4` | `29.3.0` | ![WSL2](https://img.shields.io/badge/WSL2-Hardening-blue?style=flat-square&logo=windows) | `2.17.0-pre.3` | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass.txt) |

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
| 2.2 | **Shared DB Mode** | `ldm init <Name> --no-up --no-seed --database-mode shared --db postgresql` | Valid compose (no `depends_on` on an undefined service), JDBC URL targets `liferay-db-global`, derived database name is lowercase, `database_mode` persisted to `meta`. |
| 2.3 | **Shared DB Mode (MySQL)** | `ldm init <Name> --no-up --no-seed --database-mode shared --db mysql` | Succeeds. JDBC URL targets `liferay-db-mysql-global:3306` -- **not** `liferay-db-global`, which would aim a MariaDB driver at the PostgreSQL container -- derived database name is lowercase, no `depends_on` on an undefined service. |
| 2.4 | **Shared Search Mode** | `ldm init <Name> --no-up --no-seed --search-mode shared` | `search_mode` persisted to `meta`; an `ElasticsearchConfiguration.config` written under `osgi/configs` with `productionModeEnabled`, the global cluster address and a **lowercase** `indexNamePrefix`; that directory mounted into the container. |

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
| 3.2 | **Effective Names** | `ldm info <non-ASCII name>` | Heading shows the verbatim project name; every `Liferay:`/`Database:`/`Tunnel:` row shows the transcoded name Docker holds, and none shows the verbatim form. |
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
4. **All Test Files MUST Live Under `ldm_core/tests/`**:
   * `testpaths` in `pyproject.toml` and the explicit `python -m pytest ldm_core/tests/` invocation in `.github/workflows/ci.yml` both scan that single directory. A test file placed anywhere else is **silently never executed** — it does not fail, it simply never runs.
   * This applies even to tests covering code outside the package. Tests for `scripts/` modules belong here too: see `ldm_core/tests/test_sync_compatibility.py` and `test_manage_target_nodes.py`, which reach their target via `sys.path.insert(0, <repo>/scripts)` and then import the module by its bare name.
   * `test_no_test_files_outside_suite_directory` in `ldm_core/tests/test_architectural_contracts.py` enforces this, and fails the suite if a test-defining file appears elsewhere. Standalone scripts that merely *look* like tests (e.g. `scripts/test_ui.py`, a manual Playwright driver) are exempt, because they define no collectable test functions or classes.

5. **Never Touch the Developer's Real `~/.ldm`**:
   * The `isolate_ldm_home` autouse fixture in `ldm_core/tests/conftest.py` points `LDM_HOME` at a fresh temporary directory for every test. Do not remove it, and do not write tests that depend on the real home.
   * Before it existed, a full run registered pytest tempdirs as real projects — entries like `tmp58psgp9w` and `test-project` appeared in the developer's `ldm list` — overwrote `~/.ldm/last-command.log` (destroying the trace needed to diagnose whatever was last run), and *deleted* real registry entries whose paths no longer resolved.
   * `LDM_HOME` is the only lever available: `get_actual_home()` rebuilds `/Users/<user>` from `SUDO_USER`/`USER` on macOS and ignores `HOME` entirely.
   * Tests that patch `get_actual_home` themselves are unaffected — a patch replaces the function, so it never consults the environment.
   * This isolates the **filesystem only**. Docker is covered separately, by the guard below.
   * `test_suite_never_touches_the_developers_real_ldm_home` in `ldm_core/tests/test_architectural_contracts.py` enforces that the fixture is in force.

6. **Never Touch the Machine's Docker** (LDM-#1409):
   * The `block_real_docker` autouse fixture in `ldm_core/tests/conftest.py` **fails** any test that reaches the real daemon, naming the test and the offending command. The boundary is enforced, not merely documented.
   * It was measured before it existed: **117 Docker invocations from 74 tests** in one run. Among them a unit test running `docker exec` inside the developer's own `liferay-search-global`, four doing `docker rm -f liferay-proxy-global`, one creating a real `wsl` Docker context pointing at `ssh://dev@192.168.1.10`, and the `docker run --rm -v <pytest tmpdir> alpine` calls that leaked `Created` containers holding bind mounts on deleted paths (`docker system prune` does not collect those).
   * **The hook is at the `subprocess` boundary, not at `CommandRunner`.** Only 81 of those 117 calls went through `CommandRunner.run`; the rest come from the ~20 places in `ldm_core/{snapshot,runtime,workspace}` that call `subprocess` directly. A guard on the wrapper alone polices two thirds of the problem and reports success.
   * It **intercepts rather than mocks** — anything that is not a Docker argv is passed straight through to the real function, so the warning above about globally replacing stdlib machinery still holds and is not violated here.
   * Companion stubs remove the bulk of the traffic at its source: `stub_docker_environment_probes` answers `get_compose_cmd()` and `get_docker_socket_path()` without asking the machine and neutralises `reclaim_volume_permissions()` (which shells out to `alpine chown/chmod`), and `DockerService`'s module-scope `run_command` is stubbed so the whole facade (`is_running`/`exists`/`stop`/`rm`/`inspect`/…) cannot reach a daemon.
   * A test that genuinely needs Docker marks itself `@pytest.mark.needs_docker` and is allowed through. `ldm_core/tests/test_e2e_interactive.py` is the only such suite today; it drives the real CLI as a subprocess. Run without them:

     ```bash
     pytest -m "not needs_docker"
     ```

   * Three traps this repeatedly walked into, worth knowing before adding a patch:
     * `DockerService`'s statics call a **module-scope** `run_command` imported in `docker_service.py`, so `patch.object(self.manager, "run_command")` never reaches them. That is LDM-#1365; ten more instances of it survived in `test_infra.py` alone.
     * Some handlers import inside the function body (`cmd_target_status` does `from ldm_core.utils import run_command`), which re-resolves the name at call time and **ignores** a patch on the handler module's attribute. `test_cmd_target_status_offline` read as fully mocked while running a real `docker info`.
     * A class with two `setUp` definitions silently uses the second one. Adding a patcher to the first has no effect at all.
     * **A platform-gated Docker call is invisible to a single-platform measurement.** `pipelines/run.py` calls `reclaim_volume_permissions()` only when `platform.system() == "linux"`, so a macOS run of this suite measured *zero* daemon calls through it while Linux CI hit it from three tests. The 117-call baseline above is a macOS number and the Linux one is higher. When auditing for side effects, grep for `platform.system()` around the call path before trusting a clean local run — or force the branch, as `reclaim_volume_permissions` was verified: patch `platform.system` to `"Linux"` for the offending tests, confirm the guard fires, then confirm the stub silences it.

7. **Own Your Scratch Paths** (LDM-#1402):
   * Eight suites built their `paths` fixtures from the constant `/tmp/proj`, and let the code under test write into it. Two concurrent runs on one machine therefore raced, and one deleted a file the other was about to read — a failure indistinguishable from a real regression in whatever diff was under test.
   * They now derive from `TEST_TMP_ROOT` in `ldm_core/tests/tmproot.py`, unique per pytest process.
   * If you patch `get_actual_home` **and** set a project path, keep the project *under* that home. `_check_global_config_and_network` does `test_path.relative_to(get_actual_home())` and silently skips its whole volume check when the two are unrelated.

---

## 🚀 Local E2E Platform Verification Scripts (Multi-OS)

### Port-conflict diagnostics

When a port check cannot proceed, both scripts name what holds the port
(LDM-#1428) rather than aborting with only the port number. The output goes to
the durable report, not just the console.

**Two sources are queried, and both are needed.** Neither can answer the
question alone:

| Source | Answers |
|---|---|
| `docker ps --filter publish=<port>` | which **container** holds it — the only source that can |
| `lsof` / `ss` / `netstat` / `Get-NetTCPConnection` | which **host process** holds it, when no container does |

A container-published port is held on the host by the runtime's **forwarder**,
which never identifies the container:

| Runtime | What the native tool names |
|---|---|
| Colima / Lima | `ssh` (the Lima SSH mux — it forwards ports over SSH) |
| Docker Desktop (macOS) | `com.docker.backend` |
| Docker Desktop / WSL2 | `wslrelay` / `vpnkit` |
| Native Linux | `docker-proxy` |

Measured on Colima with a container publishing 5601, `lsof` reports `ssh` while
`docker ps` reports the container. Printing only the `lsof` line would send the
operator chasing a process that is working perfectly, so a recognised forwarder
is labelled as such and the reader is pointed back at the container.

It fires at three points: before the check starts (a foreign listener would make
the assertion pass for the wrong reason), when the holder cannot be created, and
when LDM exits with the wrong code — that last one distinguishes "LDM failed to
detect a real conflict" from "the fixture never held the port".

### Disk space pre-flight

Both scripts refuse to start unless Docker has room to finish (LDM-#1406). The
default floor is **15 GB**; override with `LDM_VERIFY_MIN_DISK_GB`.

The floor was **10 GB** until LDM-#1430, and the gate is `-lt`, so a machine
with exactly 10 GB passed — then died mid-snapshot with `ENOSPC`, on a run where
`ldm prune` had reclaimed 2.957 GB immediately beforehand. The images alone are
~7.5 GB (`liferay/dxp` ~5.3, `postgres` ~0.7, `elasticsearch` ~1.5) before the
running stack grows; 10 GB covered the pull and nothing after it.

The check asks **both Docker and the host** (LDM-#1435). Neither is sufficient
alone:

| View | Catches | Misses |
|---|---|---|
| Docker (`docker run alpine df /`) | the VM's own smaller disk | host exhaustion |
| Host (`df` on the engine's storage path) | a full host volume | the VM limit |

Docker's disk is usually a **sparse image on the host filesystem**, so the space
it reports is a promise the host may be unable to keep. Measured on a developer
machine at one moment: Docker reported **77.9 GB** free while the host volume
had **2.8 GB** at 100% capacity. The pre-flight passed and the run died with
`ENOSPC` mid-snapshot.

The host side measures the volume **backing the engine**, not `$HOME`: storage
is often relocated. On one machine `~/.colima` is a symlink to an external
drive, where the home volume showed 154 GB free and the volume Docker actually
uses showed 480 GB — checking `$HOME` there would fail a run with ample space.

The Docker side asks **Docker**, not the host:

```bash
docker run --rm alpine df -P -k /
```

On Docker Desktop, Colima and OrbStack the engine's storage lives inside a VM
with its own, far smaller disk. Measured on a developer machine mid-verification:

| View | Free |
|---|---|
| Host (`df /`) | 109.2 GB |
| Docker VM | 12.5 GB |

A host-side check would have waved that run through. This is the same reasoning
as `Doctor._check_absolute_disk_space` (LDM-#1095), and using a throwaway
container keeps the `.sh` and `.ps1` implementations identical rather than
needing two host-specific ones.

**The check runs twice** (LDM-#1430). A single up-front check cannot cover a run
whose disk usage peaks late: between the pre-flight and the snapshot the run
pulls two large images, starts the stack, deploys a bundle and generates logs,
so the headroom at the check says little about the headroom at peak. The second
check runs immediately before the snapshot — the disk-hungry phase, writing a
database dump plus a tar of every payload directory — and needs 5 GB of
remaining headroom. Failing at a named check beats failing inside `tar`, and a
snapshot that cannot write its payload is not a snapshot (LDM-#1429).

The run refuses **before pulling anything**, so a machine that cannot finish
never produces a half-written report. That matters because a report which failed
for lack of disk still reads as a defect finding, and these reports are the
project's honest record of what was actually tested.

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
* Up-front announcement of a project targeting a remote compute node, and its
  deliberate suppression under `--json` (LDM-#1341 / #1093). Provoked with an
  RFC 5737 TEST-NET-1 target whose Docker context is then deleted, so the
  project is classified remote without anything ever opening an SSH connection.
* Late port-conflict handling: exit code `4` plus guidance naming the port a
  re-run would pick, rather than telling you to go stop a process (LDM-#1350).
* Teardown of resources and network isolation.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-27* | *Last Reviewed: 2026-08-27*
