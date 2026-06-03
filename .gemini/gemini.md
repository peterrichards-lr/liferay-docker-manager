# Gemini Rules of Engagement

> [!NOTE]
> **Purpose of this file**: This file defines the **Agent Rules of Engagement & Memories** specifically for AI coding assistants (like Gemini/Antigravity) operating in this repository. It tracks workflow guidelines, verification gates, and persistent task memories. Human developers do not need to edit this file.

--- Context from: /users/peterrichards/.gemini/gemini.md ---

## 1. Permission, Scope & Workflow

- **NO multi-file edits** in a single turn without a pre-approved written plan.
- **Atomic Changes**: Break down complex tasks into small, logical units. Do not move to "Step 2" until "Step 1" is verified.
- **Visual Confirmation**: Always use the VS Code Diff view to present changes before applying them.
- **Logic-First Planning**: For any function or logic block >10 lines, output a `<plan>` tag with the step-by-step algorithm. Wait for a "Proceed" command before writing code.
- **Virtual Environment Execution**: The agent MUST run tests, linters, and git commit operations with the Python virtual environment (`.venv`) activated, or explicitly invoke the binaries inside `.venv/bin/` to prevent dependency mismatch and pre-commit hook failures on host environments.

## 2. Code Quality, Architecture & Deduplication

- **Prioritize DRY (Don't Repeat Yourself)**: Before creating a new function/helper, search the `@codebase` for existing utilities.
- **Refactor over Duplicate**: If redundant code is found, suggest a refactor into a shared service or utility rather than creating a new one.
- **Predictive Failure**: For every implementation, list two potential failure points (edge cases or performance) and how they were handled.
- **Predictable Layers**: Ensure logic stays within its designated layer (e.g., UI vs. Business Logic vs. Data) as defined in `spec.md`.
- **Hybrid Volume Strategy**: Always use **Docker Named Volumes** for lock-sensitive directories (`data/`, `osgi/state/`) on macOS and external ExFAT storage. Use **Host Bind-Mounts** for directories requiring developer interaction (`deploy/`, `modules/`, `client-extensions/`). This ensures robust POSIX file locking while maintaining a smooth DX.
- **macOS Sync Wait**: When writing high volumes of files to the host (e.g. during backup extraction), LDM MUST wait at least 2 seconds before mounting those folders into a container. This prevents race conditions where the macOS VirtioFS driver reports a successful write to the host OS before the files are physically available to the Docker hypervisor.
- **macOS VirtioFS Resilience**: When writing a large number of files to the host (e.g. during snapshot extraction) that must be immediately visible to a Docker container, you MUST implement a **Sync Wait** (minimum 2 seconds). On macOS, the Docker hypervisor (VirtioFS/gRPC-FUSE) lags behind the host OS's file creation; failing to wait results in empty directories inside the container.
- **Script Parity**: All cross-platform utility, verification, and wrapper scripts (e.g., `.sh` vs `.ps1` or `.bat`) MUST be kept in perfect functional parity. Any hardening check or logic update applied to one must be immediately synchronized to the others.
- **Terminal UI Integrity**: When implementing long-running operations with spinners, you MUST use the `\033[K` ANSI code to clear the terminal line before each update to prevent character bleed. Truncation must be whitespace-aware to prevent word cutting.

## 3. Testing & Hard-Gates

- **Unit Test Requirement**: All new logic must have corresponding unit tests.
- **Coverage Expansion**: For every change, review existing tests (unit, e2e, smoke, etc.) and determine if they need to be updated or expanded to increase overall coverage.
- **The Deployment Gate**: You must never suggest a deployment command until you have explicitly asked me to confirm that all unit tests are passing.
- **Test-Driven Alignment**: Propose test cases *before* providing the implementation.

## 4. Collaborative Learning

- **Root Cause Analysis**: Explain the "Why" and "How" of every fix.
- **Architectural Check-in**: Confirm alignment with my mental model.

## 5. Liferay Client Extension (CX) Standards

- **YAML Integrity**: Cross-reference all code with `client-extension.yaml`.
- **Oauth2 & Context**: Use `Liferay.authToken`. No hardcoded credentials.
- **Workspace Awareness**: Respect `[workspace-root]/client-extensions/`.

## 6. Post-Completion "Definition of Done"

- **Test Protocol**: Provide 3-5 manual/automated steps to verify in a live Liferay instance.
- **Redundancy Scan**: After a feature is complete, scan for any newly introduced duplicate code.

## 7. Strategic Deployment Control

- **No Automatic Deploy**: Do not run or suggest `deploy` tasks as part of a general "build" command.
- **Dependency Awareness**: Before deployment, list the required order of execution (e.g., 1. OAuth2 CX, 2. Batch CX for Objects, 3. Frontend Custom Element).
- **Manual Trigger**: Always end a feature cycle by asking: "The code is ready and tested. Would you like me to provide the specific build/deploy commands for this extension now?"

## 8. Git Management & Branching Strategy

- **Logical Squashing**: Avoid creating a commit for every minor bugfix. Group related fixes and features into single, descriptive commits.
- **Release Automation**:
  - **Stable Strategy**: Use `[release]` in the commit summary to trigger a stable GitHub Release. This MUST be reserved for hardened features and verified bugfixes.
  - **Pre-Release Strategy**: Use `[pre-release]` in the commit summary to trigger a Beta/Test build (e.g., `v2.10.x-pre.y`).
  - **Experimental Mandate**: All brand new or experimental functionality (specifically the **Liferay Cloud Golden Path** / `ldm import from cloud`) MUST use pre-releases until a full E2E verification is completed by the user.
  - **Bumping**: Use `./ldm version --bump pre` to start or increment a pre-release cycle, and `./ldm version --promote` to convert a successful pre-release to a stable version.
  - Ensure version tags match the `VERSION` in `ldm_core/constants.py`.
- **Branching Separation**:
  - **`master` / `main`**: Strictly for environmental hardening, stable maintenance, and verified hotfixes.
  - **Roadmap Items**: All large roadmap items, complex features, or experimental refactors MUST be developed in dedicated feature branches (e.g., `roadmap/feature-name`).
  - **Branch Independence**: Roadmap branches MUST remain independent of each other. Never merge one roadmap branch into another. This ensures they can be verified and merged into `master` in any order.
  - **Active Sync**: While a roadmap branch is active, it MUST be periodically synchronized with the latest changes from `master` (via rebase or merge). This prevents code from going stale and minimizes future merge friction.
  - **Explicit Merge**: Roadmap branches MUST NOT be merged into `master` until full verification is complete and the user has provided an explicit request to merge.
  - **Cleanup**: Delete feature branches immediately after a successful merge to `master`.
- **Pre-Change State Persistence**: Before performing ANY file modification (code, documentation, or configuration), I MUST update the `Gemini Added Memories` section of this file with a detailed `<plan>` or task summary to ensure state is persisted in case of an interruption.

## Gemini Added Memories

- **Status: Liferay Cloud Golden Path & Demo Rescue (v2.10.x)**
  - [x] **Workspace Recognition**: Built heuristics into `ldm init-from` and `ldm import` to detect Liferay Cloud repositories automatically.
  - [x] **Guided Hydration**: Implemented an interactive wizard (and `--hydrate-from` automation switch) to orchestrate data and env-var synchronization.
  - [x] **Robust Ingestion**: Engineered a "Greedy Regex" parser to reliably extract Backup IDs from the LCP CLI across various OS/terminal environments.
  - [x] **Post-Download Organization**: Built logic to automatically recursively flatten LCP's nested UUID backup structures into standard LDM snapshots.
  - [x] **License Awareness**: Implemented expiration-aware license syncing to ensure valid developer licenses aggressively overwrite expired Cloud trial licenses.
  - [x] **UI Hardening**: Re-engineered the `Spinner` engine with ANSI `\033[K` line-wiping and terminal-aware whitespace truncation.
  - [x] **Automation Standardization**: Established a project-wide exit code contract (0-4, 126) and enforced non-interactive `-y` modes for all developer utilities.
  - [x] **PaaS Hydration Edge-Cases Resolved**:
    - **SQL Scrubbing**: Removed proprietary `\restrict` and `\unrestrict` meta-commands from LCP dumps to prevent `ON_ERROR_STOP=1` import failures.
    - **PostgreSQL Clean Slate**: Implemented a comprehensive `DO` block wipe loop (including `DELETE FROM pg_largeobject_metadata`) to guarantee a collision-free import environment without requiring the missing `postgres` superuser.
    - **PostgreSQL Socket Race**: Added `no such file or directory` to the silent retry loop in `_wipe_db`, ensuring LDM waits for the Docker Unix socket to appear before attempting schema wipes.
    - **Volume Permissions & Sync**: Replaced Alpine `cp` with a robust `tar` pipeline and forced `chown -R 1000:1000` to guarantee hidden file preservation and correct Liferay read permissions across the VM boundary.
    - **Virtual Host Override**: Enforced aggressive `UPDATE virtualhost` execution to map the local hostname dynamically.
    - **Robust Volume Hydration**: Re-engineered `cmd_restore` to synchronously populate the host project folder FIRST, followed by a mandatory 2-second **Sync Wait** to allow the macOS hypervisor (VirtioFS) to catch up, before finally pushing the confirmed host data into Docker Named Volumes via a `tar` pipeline.
    - **Volume Naming Consistency**: Implemented explicit naming in `ComposerService` to prevent Docker Compose from prefixing volumes with the project name (e.g. `modern-intranet_modern-intranet-data`), resolving critical hydration mismatches where LDM filled a "ghost" volume.
    - **Smart Store Detection**: Implemented automatic detection of `FileSystemStore` (simplified paths) vs `AdvancedFileSystemStore` (nested repositoryId paths) during Cloud imports to prevent document library 404s.
  - [x] **Self-Tuning JVM & Proactive Monitoring**:
    - **Auto-Tuning**: Built a resource scaling engine in `ComposerService` that automatically disables `TieredStopAtLevel=1` and increases `ReservedCodeCacheSize` during reindexing to prevent `CodeCache` exhaustion.
    - **Proactive Reindexing**: Integrated real-time log parsing into the primary startup spinner to report reindex progress and block the "Ready" signal until completion.
    - **Shared Engine**: Centralized reindex triggering in `BaseHandler.flag_reindex`, enabling the new `ldm reindex` command and `ldm run --reindex` flag.
  - [x] **UI & UX Refinement**:
    - **Consolidated Startup**: Unified redundant "Starting stack" lines into a single professional message with `BYELLOW` styling.
    - **macOS PTY Safety**: Refactored binary `sudo` updates to use `os.system` instead of Python subprocesses, permanently fixing the `unable to allocate pty` error on macOS terminal environments.
  - [x] **Git History Cleanup & Secrets Purge (v2.10.x)**
    - [x] **Secrets & Database Purge**: Perform interactive rebase from `ad00e868` to drop commit `1f262a59` and purge `modern-intranet` folder from `76ec299e`.
    - [x] **Update Ignore Settings**: Add `modern-intranet/` to `.gitignore`.
    - [x] **Tag and Push Hardening**: Purge and recreate release tags `v2.10.58` and `v2.10.59` on clean commits, then force push to remote.

  - [x] **Git History Consolidation (v2.10.25)**: Soft reset to `80407b4c` (v2.10.24) and consolidate 45 micro-commits into 5 clean logical changesets.
  - [x] **Release Cleanup**: Delete redundant tags/releases `v2.10.25` to `v2.10.59` locally and remotely on GitHub.
  - [x] **Actions Cleanup**: Delete failed/redundant GitHub Action workflow runs.

- **Next Focus: Roadmap Execution & CLI Namespacing**
  - [x] **Branch Cleanup**: Audit and delete fully merged roadmap branches (`cli-namespacing`, `guided-onboarding`, `extensible-profiles-architecture`).
  - [x] **Document Branching & Tagging Strategy**: Update CONTRIBUTING.md with branch-isolated pre-release rules.
  - [x] **Roadmap Synchronization**: Update docs/ROADMAP.md to align with completed v2.10.x features and restructure future roadmap items.
  - [x] **Conductor Plans Cleanup**: Review, consolidate duplicates, and remove completed plans.
  - [x] **Virtual Environment & Headers Mandate**: Add virtual environment developer mandates and agent rules, and clarify purpose headers in gemini configs.
  - [x] **Organize Conductor Tracks**: Move all individual track plans to the tracks/ subfolder and update tracks.md links.
  - [x] **Fix Samples Flow**: Delegate get_samples_root in LiferayManager, add test coverage to verify it executes successfully, and test the CLI samples flag.
  - [x] **Diagnostics & Venv Hardening**: Implemented virtualenv environment verification, refactored ldm doctor to provide a RAG summary dashboard by default with granular section filters (`--system`, `--docker`, `--project`), and expanded test coverage.
  - [x] **Test Coverage Hardening**: Expanded unit test coverage for the Snapshot/Restore Service (specifically `_wipe_db()`, `_execute_orchestrated_db_restore()`, and Smart Store Detection heuristics).
  - [x] **CLI Simplification (Namespacing)**: Refactoring flat commands into grouped namespaces (infra, cloud, config, system) with 100% backward compatibility via preprocess_args.
  - [x] **E2E Scripts Refactor**: Update verify_e2e_refactor.sh and verify_e2e_refactor.ps1 for CLI namespacing, legacy translations, and scaled instance logs.
  - [x] **Suppress Pip Warnings**: Add `--disable-pip-version-check` to E2E verification scripts.
  - [x] **Fix E2E Success Output**: Ensure E2E success marker is appended to the results report file in verify scripts.
  - [x] **Fix sync_compatibility.py**: Ensure the history directory is created if it does not exist.
  - [x] **Regenerate User Report**: Manually add the passing marker to the user's report and run sync_compatibility.py to rebuild the matrix.
  - [ ] **Linting Documentation**: Clarify auto-fix and `--check` options for `lint.sh` in `CONTRIBUTING.md`.
  - [ ] **Extensible Stack Profiles & External Database**:
    - [ ] Relocate plans folder to `docs/roadmap/plans/` (In Progress)
    - [ ] Create directory structure and loader logic for declarative stack profiles.
    - [ ] Implement `keycloak-sso` profile (realm-export mapping and OSGi configs).
    - [ ] Implement `clustered` profile (JGroups TCPPING and shared Named Volumes).
    - [ ] Implement `--db external` database parameter switch.
  - [x] **Hardening Phase 2: Secrets, Compose Validation, and Dependabot**:
    - [x] Implement `detect-secrets` hook in pre-commit and dev-requirements.
    - [x] Create `scripts/validate_compose.py` to validate compose templates.
    - [x] Create `.github/dependabot.yml` configured to check actions and python packages.
  - [x] **Workflow Hardening & Quality Gate Improvements**:
    - [x] Add `actionlint` to pre-commit and dev dependencies.
    - [x] Harden `ci.yml` (add dependency caching & `pip-audit`).
    - [x] Harden `generate-seeded-states.yml` (least-privilege permissions, caching, update action versions).
    - [x] Align `scheduled-verification.yml` with the virtualenv project mandate.

  - [x] **Verbosity Reduction**:
    - [x] Move inner-loop sync and monitoring logs in `workspace.py` to `UI.detail`.
    - [x] Move detailed SQL and archive progress statements in `snapshot.py` to `UI.detail`.
    - [x] Clean up intermediate setup logs in `runtime.py`.

  - [x] **Document Third-Party Tools**:
    - [x] Create `docs/THIRD_PARTY_TOOLS.md` detailing Docker, mkcert, openssl, telnet, nc/ncat, and lcp.
    - [x] Explain why they are needed, which are optional, and what features break without them.
    - [x] Clarify the specific diagnostic check of `nmap`/`ncat` (and its shift to Log4j file-based sync).
    - [x] Link to the new file from `docs/INSTALLATION.md` and `docs/LDM_ARCHITECTURE.md`.
    - [x] Update `ldm_core/handlers/diagnostics.py` to flag `nc/ncat` as Deprecated/Unused (status: True, no warnings/hints).

## 9. Founding Patterns of LDM

- **Sensible Defaults**: Whenever a standard Liferay convention exists, ldm uses it automatically (e.g., port 8080, managed DB name lportal).
- **Smart Context**: If you run a command from inside a project folder, ldm automatically detects the project context.
- **Interactive Fallback**: If a required piece of information (like a project name or a Liferay tag) is missing from your command and cannot be detected, ldm will prompt you interactively or show you a list of choices.
- **Graceful Abort**: You can type `q` at any interactive prompt to safely cancel the operation.

## 10. Scripting & Automation (Piped Input)

- **Automating Prompts**: LDM supports receiving answers to interactive prompts via standard input piping (e.g., `echo -e "n\nmy-project\n\n\n" | ldm run`).
- **Shell Precedence Pitfall**: When piping into a chained command, ensure the pipe binds directly to LDM. For example, `echo "y" | cd /tmp && ldm run` incorrectly pipes into `cd`. The correct syntax is `cd /tmp && echo "y" | ldm run`.

## 11. Pull Request & Review Feedback Loop

- **PR Creation**:
  - After successfully pushing a feature or roadmap branch, the agent should check if the `gh` (GitHub) CLI is available and authenticated.
  - If available, the agent should propose or create a Pull Request targeting the base branch (usually `master`) using `gh pr create --title "<summary>" --body "<details>"`.
- **Review & Feedback Loop**:
  - If the user rejects the PR or requests changes, they can specify this in the chat or ask the agent to inspect the PR feedback.
  - The agent must retrieve PR feedback using the GitHub CLI:

    ```bash
    gh pr view --json reviews,comments,statusCheckRollup
    ```

  - The agent will parse the PR review comments, map them to specific source files, output an implementation plan to address the feedback, and apply the fixes.
  - Once changes are committed and pushed, the agent should verify the PR status and notify the user that the feedback has been addressed.

--- End of Context from: /users/peterrichards/.gemini/gemini.md ---
