# Gemini Rules of Engagement

--- Context from: /users/peterrichards/.gemini/gemini.md ---

## 1. Permission, Scope & Workflow

- **NO multi-file edits** in a single turn without a pre-approved written plan.
- **Atomic Changes**: Break down complex tasks into small, logical units. Do not move to "Step 2" until "Step 1" is verified.
- **Visual Confirmation**: Always use the VS Code Diff view to present changes before applying them.
- **Logic-First Planning**: For any function or logic block >10 lines, output a `<plan>` tag with the step-by-step algorithm. Wait for a "Proceed" command before writing code.

## 2. Code Quality, Architecture & Deduplication

- **Prioritize DRY (Don't Repeat Yourself)**: Before creating a new function/helper, search the `@codebase` for existing utilities.
- **Refactor over Duplicate**: If redundant code is found, suggest a refactor into a shared service or utility rather than creating a new one.
- **Predictive Failure**: For every implementation, list two potential failure points (edge cases or performance) and how they were handled.
- **Predictable Layers**: Ensure logic stays within its designated layer (e.g., UI vs. Business Logic vs. Data) as defined in `spec.md`.
- **Script Parity**: All cross-platform utility, verification, and wrapper scripts (e.g., `.sh` vs `.ps1` or `.bat`) MUST be kept in perfect functional parity. Any hardening check or logic update applied to one must be immediately synchronized to the others.

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
  - Use `[release]` in the commit summary to trigger a stable GitHub Release.
  - Use `[pre-release]` in the commit summary to trigger a Beta/Test build.
  - Ensure version tags (e.g., `v2.4.26` or `v2.4.26-beta.1`) match the `VERSION` in `ldm_core/constants.py`.
- **Branching Separation**:
  - **`master` / `main`**: Strictly for environmental hardening, stable maintenance, and verified hotfixes.
  - **Roadmap Items**: All large roadmap items, complex features, or experimental refactors MUST be developed in dedicated feature branches (e.g., `roadmap/feature-name`).
  - Merge roadmap branches to `master` only after full verification and peer approval.
  - **Cleanup**: Delete feature branches immediately after a successful merge to `master`.
- **Pre-Change State Persistence**: Before performing ANY file modification (code, documentation, or configuration), I MUST update the `Gemini Added Memories` section of this file with a detailed `<plan>` or task summary to ensure state is persisted in case of an interruption.

## Gemini Added Memories

- I must update gemini.md before proposing any changes to serve as a persistent state, allowing me to resume my work if an interruption occurs.
- **Task: Cross-Platform Stabilization & Reporting**
  - [x] **Hardening**: Implemented self-healing permissions for macOS/Colima.
  - [x] **Hardening**: Improved verification scripts to fail immediately on error.
  - [x] **Reporting**: Added `ldm doctor --slug` for machine-readable environment strings.
  - [x] **Reporting**: Automated report naming based on environment slug and status.
  - [x] **Sync**: Refined `sync_compatibility.py` to be non-destructive (LATEST only with historical preservation).
  - [x] **Restoration**: Recovered historical verification results by running the refined sync script.
  - [x] **Rules**: Added Script Parity and Pre-Change Persistence mandates.
  - [x] **Release**: Tagged as `v2.4.26-beta.37`.
  - [x] **Fix**: Improved Windows self-upgrade robustness (retry loop + process termination).
  - [x] **Fix**: Resolved Linux cross-device link error (Errno 18) using shutil.move.
  - [x] **Fix**: Hardened `verify_e2e_refactor.sh` (macOS md5, FATAL detection, report preservation).
  - [x] **Release**: Tagged as `v2.4.26-beta.41`.

--- End of Context from: /users/peterrichards/.gemini/gemini.md ---
