# 🧪 LDM Logic Validation Plan

This document outlines the systematic verification of the **Liferay Docker Manager (LDM)** logic refinements implemented in March 2026. The focus is on ensuring that stability fixes, surgical monitoring, and Liferay Cloud integration work correctly across all verified environments.

---

## 🛡️ Phase 1: Environment & Diagnostics

**Goal:** Verify that LDM correctly identifies host capabilities and potential "hidden killers."

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **1.1 LDM Doctor** | Run `ldm doctor` | Reports correct Docker provider (e.g., OrbStack, Docker Desktop) and identifies CPU/Memory accurately. |
| **1.2 Doctor Script** | Run `./scripts/ldm-doctor.sh` | Passes **Volume Mounting** test. Reports memory accurately without false warnings if >= 7.5GB. |
| **1.3 macOS Bridge** | Check `docker ps` for `docker-socket-proxy`. | On macOS, this singleton container should be running whenever an SSL-enabled stack is active. |

---

## 🏗️ Phase 2: Developer Workflow (`init-from`)

**Goal:** Verify the link between a live workspace (`ldm-cx-samples`) and the LDM project.

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **2.1 Initialization** | `ldm init-from /path/to/ldm-cx-samples my-dev-stack` | Project is created. Metadata contains the `workspace_path`. |
| **2.2 State Access** | Run `ldm down my-dev-stack` | The `osgi/state` folder is a **Bind Mount**, allowing LDM to clear it for maintenance. |
| **2.3 Perm Enforcement** | Check host `data/` and `deploy/` folders. | Folders exist and have `775` permissions with ownership assigned to UID 1000. |
| **2.4 Mount Verification**| Run project on external volume. | LDM detects if `/Volumes` is not shared and provides the correct `colima` command. |
| **2.5 Neg. Monitoring** | Modify a `.java` or `.yaml` file in source. | LDM detects the change but **DOES NOT** trigger a sync or restart. |
| **2.6 CX Sync** | Place a `.zip` in `client-extensions/*/dist/`. | LDM follows the mandatory 3-step sequence: Copy to root, Expand for builds, Move to OSGi. |
| **2.7 Module Sync** | Place a `.jar` in `modules/*/build/libs/`. | LDM syncs the jar to `deploy/` and triggers `cmd_deploy`. |
| **2.8 Monitor Restart** | Terminate and run `ldm monitor my-dev-stack`. | Monitoring resumes using the workspace path stored in metadata. |
| **2.9 Asset Export** | Copy tested ZIPs/JARs to `references/samples/`. | Assets are staged for the Demo Workflow test. |

---

## 🪵 Phase 3: Log Management

 (`log-level`)

**Goal:** Verify Log4j 2 structure and hot-reloading capability.

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **3.1 Template Integrity** | Open `osgi/portal-log4j/portal-log4j-ext.xml`. | Structure is valid Log4j 2. Appender name is `XML_FILE`. `monitorInterval="5"` is present. |
| **3.2 Hot-Reload Add** | `ldm log-level my-dev-stack --category com.liferay.portal --level DEBUG` | `<Logger>` entry is added to XML. Container **DOES NOT** restart. |
| **3.3 Hot-Reload Remove** | `ldm log-level my-dev-stack --category com.liferay.portal --remove` | `<Logger>` entry is removed. Minimal XML structure is preserved. |

---

## ☁️ Phase 4: Cloud Integration (`cloud-fetch`)

**Goal:** Verify remote metadata mapping and command scoping.

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **4.1 Project Filtering** | `ldm cloud-fetch --list-envs` | If `cloud_project_id` exists in meta, only that project's envs are shown. |
| **4.2 Flag Injection** | Run any `cloud-fetch` command with `-v`. | Verify the `lcp` CLI call includes `--project` and `--environment` flags. |
| **4.3 Checksum** | Perform a `--download`. | MD5 checksum is calculated and verified against LCP metadata (if available). |

---

## 🚀 Phase 5: Demo Workflow (`--samples`)

**Goal:** Verify project hydration from the read-only `references/samples/` template.

> [!IMPORTANT]
> **Prerequisite:** This phase requires built assets (`.zip` and `.jar` files) to be present in `references/samples/`. Perform **Step 2.7** before starting this phase.

| Test Case | Steps | Expected Outcome |
| :--- | :--- | :--- |
| **5.1 Hydration Map** | `ldm run demo --samples` | ZIPs from `references/samples/client-extensions/` are moved to `osgi/client-extensions/`. |
| **5.2 Artifact Map** | (As above) | JARs/ZIPs from `references/samples/deploy/` are moved to project `deploy/`. |
| **5.3 Config Map** | (As above) | `.config` files from `references/samples/osgi/configs/` are moved to project `osgi/configs/`. |
| **5.4 Auto-Restore** | (As above) | LDM automatically restores the snapshot found in `references/samples/snapshots/`. |

---

## 🏁 Final Sign-off

* [ ] All Phase 1 tests pass on host OS.
* [ ] Liferay starts without `Unable to create lock manager` errors.
* [ ] Site is reachable at `https://localhost` (if SSL enabled).
* [ ] Monitoring only syncs built artifacts.
