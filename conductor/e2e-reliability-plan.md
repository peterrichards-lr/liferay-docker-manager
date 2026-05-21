# E2E Reliability & UI Verification Plan

## Objective

To stabilize the LDM release pipeline by decoupling flaky, environment-sensitive UI tests (Playwright) from the core binary verification process, and to establish a reliable, automated compatibility matrix generation.

## Key Changes & Rationale

### 1. Decoupling Playwright (UI Tests)

**Problem:** The previous E2E verification script (`verify_e2e_refactor.sh`/`.ps1`) included a Playwright script to verify hot-deploy via Liferay's UI (checking for a "Test Collection" fragment). This test was highly flaky because Liferay's AutoDeploy scanner operates asynchronously and its performance varies wildly depending on the host VM (Colima, Docker Desktop, WSL2) and architecture. This caused false negatives, blocking stable LDM releases.

**Solution:**

* **Core E2E Scripts:** Removed Playwright dependencies from `verify_e2e_refactor.sh` and `verify_e2e_refactor.ps1`.
* **Gogo Shell Verification:** Hot-deploy verification was rewritten to use a minimal OSGi bundle (`test-bundle.jar`) instead of a fragment ZIP. The verification now connects directly to Liferay's internal Gogo shell (via `telnet localhost 11311`) to definitively prove the bundle `STARTED`, which validates the host-to-container volume mounts perfectly without relying on UI rendering.
* **Standalone UI Script:** The Playwright logic was preserved and extracted into a standalone script (`scripts/test_ui.py`). This allows developers to manually verify the UI health (Login, Control Panel navigation) when needed, without it being a hard gate for CI/CD.

### 2. Robust Compatibility Matrix (`COMPATIBILITY_TABLE.md`)

**Problem:** Managing verification reports across different architectures, OS versions, and Docker providers (Colima, OrbStack, Native, Docker Desktop) was manual and error-prone.

**Solution:**

* **`sync_compatibility.py`:** Developed a Python script to automatically parse `verify-*.txt` report files from `references/verification-results/`.
* **Standardization:** The script standardizes environment names, redacts sensitive paths/hostnames, and extracts the Docker Engine/Provider versions.
* **Archiving:** Old or duplicate reports are automatically moved to `references/verification-results/history/`. The root folder only contains the single latest passing report for each unique environment.
* **Matrix Generation:** The script dynamically generates the Markdown table in `docs/COMPATIBILITY_TABLE.md` (and injects it into `README.md`).
* **Hardening Labels:** We explicitly standardized the shield badges to use `-Hardening-` (e.g., `Colima-Hardening`, `WSL2-Hardening`) to clearly indicate environments that support our aggressive permission/security checks.

### 3. Dropping Legacy Support

**Decision:** Dropped support for older Python 3.9 environments (specifically observed on Apple Intel macOS 12 Monterey setups that lack modern Python installations).
**Rationale:** Attempting to maintain backwards compatibility with Python 3.9 required downgrading dependencies (`urllib3<2.0`) and stripping out modern Python 3.10+ features (like union type hints `str | None`). This hampered development and caused cascading failures. `pyproject.toml` now strictly enforces `requires-python = ">=3.10"`.

## Verification & Documentation Workflow

The process for testing LDM and updating the compatibility matrix has been fully automated through the following workflow:

1. **Execute Verification:**
    * Run the appropriate verification script for the target platform:
        * **macOS/Linux:** `./scripts/verify_e2e_refactor.sh`
        * **Windows:** `.\scripts\verify_e2e_refactor.ps1`
    * The script will automatically generate a timestamped report file (e.g., `verify-apple-silicon-macos-15-sequoia-colima-pass-e1b5b25c.txt`) in the root directory.

2. **Stage Results:**
    * If the script ran successfully, it will automatically attempt to move the report into `references/verification-results/`.
    * If run on an isolated machine, manually copy the generated `verify-*.txt` report into the `references/verification-results/` directory of the main repository.

3. **Process & Sync Documentation:**
    * From the repository root, run the synchronization script:

        ```bash
        python3 scripts/sync_compatibility.py
        ```

    * **What this does:**
        * Scans the `references/verification-results/` directory.
        * Identifies the newest report for each unique environment (Architecture + OS + Docker Provider).
        * Automatically moves all older or duplicate reports for that environment into `references/verification-results/history/`.
        * Parses the latest reports and regenerates the Markdown table in `docs/COMPATIBILITY_TABLE.md`.
        * Automatically updates the relative hyperlinks within the table to point directly to the raw, preserved `verify-*.txt` files in the `references/verification-results/` directory.
        * Automatically chains to `scripts/sync_docs.py` to inject the updated table into `docs/README.md`, `docs/TESTING.md`, and `docs/installation.md`.

4. **Standalone UI Verification (Optional):**
    * If frontend validation is required, activate the virtual environment created by the verification script and run the standalone Playwright test:

        ```bash
        source e2e-work-dir/.verify-venv/bin/activate
        python scripts/test_ui.py
        ```
