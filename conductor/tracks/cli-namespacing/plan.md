# Implementation Plan: CLI Namespacing

## 1. Objective

Transition the LDM CLI from a flat command list to grouped namespaces to reduce cognitive load and improve discoverability.

## 2. Key Requirements

- **Namespace Grouping**: Group commands into logical domains: `system`, `infra`, `project`, `config`.
- **Legacy Compatibility**: Maintain the existing flat commands as aliases for power users.
- **Improved Help Output**: Organize the `--help` output by namespace for better readability.

## 3. Technical Design

### CLI Update (`ldm_core/cli.py`)

- Reorganize the `subparsers` using the `add_parser` method to create namespace-specific groups.
- Example structure:
  - `ldm system (renew-ssl, prune, upgrade, update-check)`
  - `ldm infra (setup, down, restart)`
  - `ldm project (run, stop, restart, down, logs, deploy, shell, gogo, browser, edit)`
  - `ldm (status, list, doctor)` - Top-level commands remain for quick access.

### Alias Logic

- Use a dictionary to map the old flat commands to their new namespace equivalents.
- If a flat command is used, transparently route it to the correct namespace handler.

## 4. Implementation Steps

1. **Step 1: Namespace Definition**: Finalize the list of namespaces and which commands belong where.
2. **Step 2: CLI Refactoring**: Update `cli.py` to implement the new sub-sub-parser structure.
3. **Step 3: Alias Mapping**: Create a mapping to ensure `ldm prune` still works as `ldm system prune`.
4. **Step 4: Help Output Refinement**: Update the main help formatter to display commands grouped by namespace.
5. **Step 5: Documentation Update**: Refresh all help files and `README.md` to reflect the new namespace structure.

## 5. Verification & Testing

1. Run `ldm system prune` and verify it works identically to `ldm prune`.
2. Run `ldm prune` and verify it still works as an alias.
3. Run `ldm --help` and verify the output is cleanly organized by namespace.
4. Verify that no existing scripts (like `scripts/run_smoke_tests.sh`) are broken by the change.
