# Tracks Registry (v2.5.0 - The Ecosystem Phase Continued)

This registry tracks the status and priority of independent development tracks for LDM v2.5.0.

| Track ID | Track Name | Priority | Status | Plan |
| :--- | :--- | :--- | :--- | :--- |
| `guided-onboarding` | Guided Onboarding (`ldm init`) | High | 🔄 In Progress | [Plan](./tracks/guided-onboarding/plan.md) |
| `visual-dashboard` | Visual Health Dashboard | Medium | 📝 Planned | [Plan](./tracks/visual-dashboard/plan.md) |
| `osgi-performance` | OSGi State Persistence | **High** | 📝 Planned | [Plan](./tracks/osgi-performance/plan.md) |
| `scenario-packs` | Shared Scenario Packs | High | 📝 Planned | [Plan](./tracks/scenario-packs/plan.md) |
| `ai-orchestration` | AI-Assisted Orchestration (`ldm ai`) | Medium | 📝 Planned | [Plan](./tracks/ai-orchestration/plan.md) |
| `cli-namespacing` | CLI Namespacing | Low | 📝 Planned | [Plan](./tracks/cli-namespacing/plan.md) |
| `multi-os-e2e` | Multi-OS E2E Matrix & JVM | Medium | 📝 Planned | [Plan](./tracks/multi-os-e2e/plan.md) |
| `diagnostics-dashboard` | Polished Diagnostics (`ldm doctor`) | Medium | 📝 Planned | [Plan](./tracks/diagnostics-dashboard/plan.md) |
| `cloud-push` | Cloud Push (`ldm cloud-push`) | **High** | 📝 Planned | [Plan](./tracks/cloud-push/plan.md) |

---

## ✅ Completed Tracks

- **StackHandler Modularization (`stack-refactor`)**: Extracted Liferay stack orchestration, generation, and assets into modular, testable components.
- **Snapshot Integrity Verification (`snapshot-integrity`)**: Automatically generates and verifies SHA-256 hashes of database and data volumes.
- **CLI Exit Code Standardization (`cli-exit-codes`)**: Configured LDM to return structured exit codes (0-4, 126) for CI/CD automation.
- **PaaS Workspace Recognition & Golden Path (`paas-workspace-recognition`)**: Interactive and non-interactive project hydration from Liferay Cloud workspace formats.
- **Documentation Audit & Restructure (`docs-restructure`)**: Decomposed monolithic docs into specialized modular files in `docs/guides/`.
- **Samples Support**: `--samples` command switch.
- **On-Demand Sample Hydration**: Automatic zip download and caching from GitHub releases.
- **Cloud Fetch (`ldm cloud-fetch`)**: Automation for Liferay Cloud environment variables, database, and asset sync.
