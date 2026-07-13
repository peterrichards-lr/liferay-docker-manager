# Implementation Plan: Polished Diagnostics Dashboard

This track focuses on refactoring the `ldm doctor` command to provide a more professional, high-level summary (RAG status) by default, while allowing deep-dive inspection through granular flags.

## 1. Problem Statement

Currently, `ldm doctor` outputs a significant amount of technical detail (versions, paths, permissions) every time it is run. For a quick health check, this can be overwhelming. Users want a "dashboard" view that gives them immediate "Go/No-Go" feedback on the environment.

## 2. Proposed Solution

Transition `ldm doctor` to a **Summary-First** model:

- **Default View**: A concise Red/Amber/Green (RAG) dashboard showing the health of major subsystems (Docker, System, Project).
- **Detail Flags**: Specific CLI arguments (e.g., `--docker`, `--system`, `--project`) to view the expanded technical details for those sections.

## 3. Implementation Steps

### Phase 1: Data Model Refactoring

- Refactor `DiagnosticsService.cmd_doctor` to separate **Data Collection** from **Reporting**.
- Create a `DoctorResult` object/dictionary that stores findings with a severity level (`HEALTHY`, `WARNING`, `CRITICAL`).

### Phase 2: RAG Dashboard Implementation

- Implement a new `_print_doctor_summary` method in `DiagnosticsService`.
- Use a compact table or list view for the default output:
  - `[ OK ] Docker Engine (v24.0.0)`
  - `[WARN] Project: aica-e2e (Missing 2 subdomains)`
  - `[FAIL] System Memory (Allocation < 8GB)`

### Phase 3: Granular CLI Arguments

- Update `ldm_core/cli.py` to add subsection flags to the `doctor` command:
  - `--docker`: Detailed Docker daemon, compose, and network status.
  - `--system`: Detailed CPU, RAM, and permission checks.
  - `--project`: Detailed project metadata, path, and DNS validation.
- Update `cmd_doctor` to filter its output based on these flags.

### Phase 4: Verification & UX

- Update `ldm_core/tests/test_diagnostics.py` to verify the new summary output.
- Ensure `ldm doctor -v` still shows the full detailed output for backwards compatibility with "log-gathering" requests.

## 4. Definition of Done

- `ldm doctor` output is < 10 lines by default for a healthy system.
- `ldm doctor --docker` shows the technical details previously shown in the Docker section.
- Unit tests verify both summary and filtered detail modes.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-13* | *Last Reviewed: 2026-07-02*
