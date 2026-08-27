---
name: ldm-architecture
description: Activate this skill whenever designing new features, modifying Docker compose logic, or interacting with Liferay environments.
---

# LDM Architecture Mandates

## Founding Patterns of LDM

These are the UX invariants every LDM command is built around. A new command
that breaks one of them is a design defect, not a matter of taste:

- **Sensible Defaults**: Where a standard Liferay convention exists, LDM adopts it automatically (port `8080`, managed database name `lportal`).
- **Smart Context**: A command run from inside a project folder detects the project context rather than demanding it be named.
- **Interactive Fallback**: When a required value (project name, Liferay tag) is neither supplied nor detectable, LDM prompts for it or offers a list of choices -- it does not simply error out.
- **Graceful Abort**: Typing `q` at any interactive prompt safely cancels the operation.

## Hybrid Volume Strategy (macOS / ExFAT)

To resolve critical filesystem locking deadlocks (e.g., `Unable to create lock manager` or `access_denied_exception`), LDM MUST use a split-volume approach:

- **Named Docker Volumes**: MUST be used for directories requiring POSIX file locking.
  - `/opt/liferay/data`
  - `/opt/liferay/osgi/state`
  - **Documented exception**: when the user opts into `--persist-osgi`, LDM
    deliberately maps `/opt/liferay/osgi/state` to a host bind-mount instead of
    a Named Volume, trading the POSIX-locking guarantee for dramatically faster
    subsequent startups (bypassing OSGi bundle resolution). LDM automatically
    invalidates and wipes this bind-mounted state if the underlying Liferay
    image tag changes, to prevent stale-bundle conflicts. See
    `docs/reference/advanced_cli.md` and `ldm_core/handlers/composer.py`
    (the `persist_osgi` branch of the compose volume builder). This is the
    only sanctioned exception to the Named Volume mandate above.
- **Host Bind-Mounts**: SHOULD be used for directories facilitating developer hot-reloads.
  - `/mnt/liferay/deploy`
  - `/mnt/liferay/files`
  - `/mnt/liferay/scripts`
  - `/opt/liferay/osgi/modules`
  - `/opt/liferay/osgi/client-extensions`
  - `/opt/liferay/osgi/log4j`
- **macOS Hypervisor Sync**: LDM MUST implement a minimum 2-second "Sync Wait" after extracting backups to the host and before hydrating Docker volumes. This compensates for VirtioFS/gRPC-FUSE sync lag.
- **Volume Naming Consistency**: LDM MUST explicitly set the `name:` property for all Named Volumes in the generated `docker-compose.yml`.
- **Volume Ownership Labels**: Every Named Volume MUST carry ownership metadata, mirroring the labels services already receive via `_inject_ldm_labels`:

  | Label | Value |
  |---|---|
  | `com.liferay.ldm.project` | the owning project |
  | `com.liferay.ldm.managed` | `true` |
  | `com.liferay.ldm.role` | `data`, `state`, or `unknown` |

  Set in `_named_volume_definition()` (`ldm_core/handlers/composer.py`). **Do not build a volume definition anywhere else** -- a second construction site is how the labels get silently dropped, and an unlabelled volume is invisible to cleanup forever (see below).

  `role` records how destructive removal would be, so `ldm prune` can reclaim disposable storage without ever sweeping a database:

  - `state` -- OSGi bundle state. Regenerated on the next boot; LDM already wipes it itself when the Liferay tag changes.
  - `data` -- database and project data. Destructive. Never removed by `--all`; always a separate, explicit confirmation.
  - `unknown` -- anything unrecognised, and deliberately **not** treated as disposable. A future volume suffix must be classified on purpose, never become sweepable by omission. Note `data` is matched before any broader suffix, so `<project>-db-db-data` cannot be misread.

  **Labels are applied only at volume creation.** Declaring labels in compose for a volume that already exists has no effect -- verified: `docker volume inspect` still reports `map[]`. Volumes predating LDM-#1267 therefore never acquire labels and are only reachable via the name-pattern fallback behind `ldm prune --legacy-volumes`. This is why dropping the labels at creation is unrecoverable rather than merely untidy.

## Infrastructure Enforcement

- **Database**: Standardize on PostgreSQL with mandatory healthchecks.
- **Search**: Use shared Global Search (ES8) by default; support Sidecar fallback isolation.
- **Self-Tuning JVM**: LDM MUST proactively scale JVM resources (e.g. `ReservedCodeCacheSize=512m`) and disable restrictive optimizations (e.g. `TieredStopAtLevel=1`) during "Production-grade" workloads like full search reindexing to prevent `NoSuchMethodException` and `CodeCache` exhaustion.
- **Logging**: Force `LIFERAY_LOG4J2_CONFIGURATION_FILE` injection to guarantee hot-reload capability.

## Terminal UI Integrity

- **Line Clearing**: Any long-running operation that renders a spinner or progress line MUST emit the `\033[K` ANSI erase-to-end-of-line code before each update. Without it, a shorter frame leaves characters from the previous frame on screen ("bleed").
- **Whitespace-Aware Truncation**: Truncating a status line to the terminal width MUST break on whitespace, never mid-word.

Both are already implemented by the `Spinner` engine (`ldm_core/ui.py:244`,
line-clearing at `:283-296`, truncation at `:273-279`). Route new progress
reporting through that engine rather than hand-rolling carriage returns.

## Automation Standards

To support CI/CD pipelines and headless automation, all LDM commands MUST adhere to a standardized exit code contract:

- `0`: Success.
- `1`: Generic/Validation Error.
- `2`: Authentication/Permission Error (e.g. LCP login required).
- `3`: Infrastructure/Data Error (e.g. Backup download failure).
- `4`: Orchestration/Deployment Error. Used for LDM-internal failures at the
  orchestration layer -- e.g. `ldm_core/pipelines/run.py`'s port-conflict-detected
  and project-path-resolution-failed cases -- as opposed to a user-input
  validation problem (which stays under `1`) or an external data/API failure
  (which uses `3`, e.g. the same file's Docker Hub tag-discovery failures).
  This triage was done deliberately, one call site at a time, per
  [#996](https://github.com/peterrichards-lr/liferay-docker-manager/issues/996)
  -- not every failure in `ldm_core/runtime/orchestration.py`/`pipelines/run.py`
  belongs under `4`; most are genuinely `1` (bad project id, missing flag,
  precondition not met) and were deliberately left alone.
- `126`: Command Invocation Error. No genuine candidate for this exists in the
  orchestration/pipeline layer as of the #996 triage -- every "not found"-shaped
  message there (project not found, archetype not found) is a validation error
  (`1`), not a failure to invoke a command. This code is reserved for a true
  invocation failure at that layer if one is ever added; see the `run_command()`
  exception below for where invocation-shaped failures currently do occur.
- `5`: Idempotent No-Op -- the desired state already held, so nothing needed
  to happen (e.g. `ldm run`/`up` in non-interactive mode against a project
  that's already running). Distinct from `1` deliberately: automation
  branching on "did this actually change anything" needs a code that isn't
  the same generic bucket as a real validation failure. Added per
  [#1094](https://github.com/peterrichards-lr/liferay-docker-manager/issues/1094).
  **Only returned in non-interactive mode** (`ldm_core/pipelines/run.py:246`):
  interactively LDM prompts to reconfigure and restart instead, so a caller
  that omits `-y`/`--non-interactive` gets a prompt rather than this code.
  Automation and E2E assertions on this contract must pass `-y`.
- **Low-level subprocess wrapper exception**: `ldm_core/utils.py`'s
  `run_command()` helper -- called from a very large number of sites across
  the codebase -- intentionally uses POSIX-standard shell conventions instead
  of the contract above for its own direct failure exits: `124` for a timed-out
  subprocess (matching GNU `timeout`'s convention) and `127` for
  "command not found" (matching the shell's own convention), and otherwise
  passes through the wrapped subprocess's own `returncode` unchanged on a
  generic failure, since discarding that information would make wrapped-tool
  failures harder to diagnose. This is a deliberate, standard choice for a
  subprocess-wrapping utility, not a violation of the contract above -- the
  0-5/126 contract governs LDM's own top-level command outcomes, not every
  exit path of every subprocess it shells out to.
- **`130` on user interrupt**: `Ctrl+C` exits `130` (`128 + SIGINT`), the POSIX
  convention, standardized across the three places that catch `KeyboardInterrupt`
  --- `ldm_core/utils.py:593` (inside `run_command()`), `ldm_core/ui.py:574`
  (interactive prompts) and `ldm_core/cli.py:3139` (the top-level handler). Like
  `124`/`127` this is a deliberate shell-convention exit, not an LDM-contract
  code, so do not renumber it into the 0-5 range.

### Piped-Input Automation

LDM accepts answers to interactive prompts on standard input, so a prompting
flow can be scripted without needing a dedicated flag for every question:

```bash
echo -e "n\nmy-project\n\n\n" | ldm run
```

- **Shell Precedence Pitfall**: When piping into a chained command, the pipe must bind to LDM itself. `echo "y" | cd /tmp && ldm run` pipes into `cd`, not into `ldm`. Write `cd /tmp && echo "y" | ldm run` instead.

## Liferay Cloud Golden Path

LDM serves as a bridge for Liferay Cloud development. To maintain stability, it enforces a strict boundary:

- **Code (Git)**: Git remains the source of truth for the workspace structure, Client Extensions, and OSGi source. LDM must NEVER modify the user's Git history or structure.
- **Data (LCP)**: LDM automates the retrieval and restoration of Cloud backups (`database.gz` and `volume.tgz`).
- **Orchestration**: LDM must dynamically flatten LCP's nested backup structures into standard LDM snapshots during hydration.

## Liferay Client Extension (CX) Standards

When LDM generates, deploys, or reasons about Client Extensions:

- **YAML Integrity**: Cross-reference generated or modified code against the extension's `client-extension.yaml`. The descriptor and the code must agree.
- **OAuth2 & Context**: Authenticate through `Liferay.authToken` / the platform's OAuth2 flow. Never hardcode credentials.
- **Workspace Awareness**: Respect the workspace layout -- Client Extensions live under `[workspace-root]/client-extensions/`, which is why that path is a host bind-mount in the volume strategy above rather than a Named Volume.
- **Deployment Ordering**: Client Extensions have ordering dependencies (an OAuth2 CX before the Batch CX that authenticates through it, before the frontend custom element that calls it). State the required order rather than deploying blind.

## Custom Containers & Multi-Compose Architecture

- **Custom Containers Integration**: When a user requests to run external services (e.g., WordPress, Node.js, Web Crawler) alongside Liferay, use the LDM `custom_containers` feature rather than altering the native LDM Python orchestration.
- **Multi-Compose Decoupled Networks**: For enterprise multi-compose decoupled architecture setups, always refer to the reference templates in `docker-compose-templates/` to understand the standard `shared-search-net` and `shared-crawl-net` external networking boundaries. Do not invent new bridging architectures if these templates suffice.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-27* | *Last Reviewed: 2026-08-27*
