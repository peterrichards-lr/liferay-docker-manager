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
  - **Branch Independence**: Roadmap branches MUST remain independent of each other. Never merge one roadmap branch into another. This ensures they can be verified and merged into `master` in any order.
  - **Active Sync**: While a roadmap branch is active, it MUST be periodically synchronized with the latest changes from `master` (via rebase or merge). This prevents code from going stale and minimizes future merge friction.
  - **Explicit Merge**: Roadmap branches MUST NOT be merged into `master` until full verification is complete and the user has provided an explicit request to merge.
  - **Cleanup**: Delete feature branches immediately after a successful merge to `master`.
- **Pre-Change State Persistence**: Before performing ANY file modification (code, documentation, or configuration), I MUST update the `Gemini Added Memories` section of this file with a detailed `<plan>` or task summary to ensure state is persisted in case of an interruption.

## Gemini Added Memories

- **Status: History Linearized & Build Stabilized (v2.4.26-beta.31)**
  - [x] **History**: Successfully linearized and professionally re-indexed beta history (v2.4.26-beta.1 through v2.4.26-beta.30).
  - [x] **Cleanup**: Purged massive git conflict marker pollution across core logic files caused by faulty reconstruction logic.
  - [x] **Fix (Infra)**: Restored critical infrastructure constants (`ELASTICSEARCH_VERSION`, `TRAEFIK_VERSION`) in `ldm_core/constants.py` that were accidentally overwritten, resolving E2E image pull failures.
  - [x] **Fix (Core)**: Restored missing `DevHandler` functional mixin to `LiferayManager`, re-enabling `cmd_version` and resolving binary build failures.
  - [x] **Environment**: Restored `.pre-commit-config.yaml` and verified that local linting hooks are active and passing.
  - [x] **Release**: Consolidated all improvements and fixes into clean **`v2.4.26-beta.31 [pre-release]`**.
  - [x] **Remote**: Master branch and tags force-pushed to origin for a clean, accurate baseline.

- **Next Focus: Targeted Environment Verification**
  - [ ] **Verification**: Execute full E2E verification of `v2.4.26-beta.31` across all target tiers:
    - macOS Monterey (Intel)
    - macOS Sequoia/Tahoe (Apple Silicon)
    - Windows 11 / WSL2
    - Linux Native (Workstation)
  - [ ] **Stabilization**: Collect verification reports and address any residual environment-specific edge cases.
  - [ ] **Promotion**: Move to stable release once all verification tiers are green.

--- End of Context from: /users/peterrichards/.gemini/gemini.md ---
