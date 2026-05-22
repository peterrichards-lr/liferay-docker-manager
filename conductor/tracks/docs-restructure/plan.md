# Implementation Plan: Documentation Audit & Restructure

This track focuses on decomposing the massive monolithic `docs/README.md` (currently ~1,000 lines) into a modular, easily navigable guide system. As LDM has evolved from a simple script into a robust orchestrator, its documentation needs to shift from a "single page cheat-sheet" to a structured knowledge base.

## 1. Problem Statement

- **Information Overload**: The root `docs/README.md` is too long, making it difficult for users to find specific answers (e.g., "How does environment variable forwarding work?" vs. "How do I run a basic project?").
- **Buried Concepts**: Advanced concepts like the *Zero-Race Atomic Deployment Strategy*, *Wildcard SSL*, and *Intelligent DNS Engine* are hidden deep within the CLI command reference or the Architecture guide.
- **Maintenance Friction**: Modifying a 1,000-line markdown file creates unnecessary merge conflicts and makes linting (`markdownlint`) tedious.

## 2. Proposed Solution

Restructure the `docs/` directory by introducing a `guides/` subdirectory. The root `README.md` will be aggressively trimmed to serve purely as a **Landing Page**, focusing on the value proposition, quick start instructions, and a table of contents that points to specialized guides.

### Proposed Directory Structure

```text
docs/
├── README.md                 (Landing Page, Features, Quick Start, Index)
├── INSTALLATION.md           (Detailed OS-specific install steps)
├── LDM_ARCHITECTURE.md       (Technical deep-dive, Mermaid diagrams)
├── SECURITY.md               (Sudo policy, file permission handling)
├── TROUBLESHOOTING.md        (Common errors)
└── guides/                   (NEW FOLDER)
    ├── CLI_REFERENCE.md      (Full command dictionary, interactive tips, scripting)
    ├── CONFIGURATION.md      (Metadata, portal-ext, common/, env forwarding)
    ├── NETWORKING_DNS.md     (Traefik, SSL, fix-hosts, client extensions)
    ├── DATA_MANAGEMENT.md    (Snapshots, seeding, hydration, reset)
    └── DEVELOPMENT.md        (Building from source, testing, contributing)
```

## 3. Implementation Steps & Effort Analysis

**Overall Effort Required**: **Medium**. The technical risk is very low (no python code changes required), but the editorial effort is moderate. Care must be taken to ensure all relative links and anchor tags remain functional, and that the narrative flow of the extracted guides makes sense independently.

### Phase 1: Directory Setup & Root README Reduction

- Create the `docs/guides/` directory.
- Extract the following sections from `docs/README.md` to be relocated:
  - `Scripting & Automation`
  - `Command Reference` (the entire dictionary of commands)
  - `Configuration Files`
  - `Environment Variable Forwarding`
  - `Interactive Mode Tips`
  - `Development & Building`
- Rewrite the `Documentation` section in the root `README.md` to serve as a clean, categorized Table of Contents pointing to the new guides.

### Phase 2: Create `CLI_REFERENCE.md`

- Move the full `Command Reference`, `Interactive Mode Tips`, and `Scripting & Automation` sections here.
- Add an index/TOC at the top of this guide so users can quickly jump to commands like `wait`, `hydrate`, or `doctor`.

### Phase 3: Create `CONFIGURATION.md`

- Move `Configuration Files` (explaining `common/` and `services/`) and `Shared Configuration (LDM_COMMON_DIR)`.
- Move `Environment Variable Forwarding` (explaining `LDM_` prefix, `LXC_` passthroughs, and Service-Specific Targeting).

### Phase 4: Create `NETWORKING_DNS.md`

- Extract the `Unified Host & SSL Rules` and `Client Extension Routing & Wildcard SSL` sections from the CLI reference.
- Expand this guide to clearly explain how Traefik, `fix-hosts`, and SNI matching work together to provide zero-config HTTPS.

### Phase 5: Create `DATA_MANAGEMENT.md`

- Extract sections detailing `snapshot`, `restore`, `hydrate`, `re-seed`, and `cloud-fetch`.
- Move the explanation of the "Seeding (Instant Boot)" logic here.

### Phase 6: Consolidation & Polish

- Move the `Development & Building` section into a new `guides/DEVELOPMENT.md` or merge it with `CONTRIBUTING.md`.
- Run `markdownlint-cli2` across all files to ensure formatting compliance.
- Test all relative links locally.

## 4. Definition of Done

- `docs/README.md` is under 300 lines and serves as an effective landing page.
- `docs/guides/` contains at least 4 specialized markdown files.
- No information is lost from the original monolith.
- `markdownlint` passes cleanly on all modified files.
- The `ldm man` command (which generates the man page from the repo) is verified to still function or is updated to parse the new structure. *(Note: `scripts/sync_docs.py` might need a minor update to parse the guides instead of just `README.md` when generating the `ldm.1` man page).*
