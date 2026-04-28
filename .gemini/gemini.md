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

## 3. Testing & Hard-Gates

- **Unit Test Requirement**: All new logic must have corresponding unit tests.
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

## Gemini Added Memories

- I must update gemini.md before proposing any changes to serve as a persistent state, allowing me to resume my work if an interruption occurs.
- **Task: Initialize Beta Release Cycle**
  - [x] **Bug (Search)**: Standardized on `_PERIOD_` for search environment variables.
  - [x] **Bug (Precedence)**: Fixed `version_to_tuple` to correctly compare beta vs stable releases.
  - [x] **Bug (CI)**: Fixed download URL redundancy in GitHub Release notes.
  - [x] **Release**: Initialized v2.4.26-beta.2.
--- End of Context from: /users/peterrichards/.gemini/gemini.md ---
