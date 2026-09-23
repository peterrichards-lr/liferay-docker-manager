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

## Permissions, Ownership and Timing

Three regressions in the v2.26.0 cycle alone came from this area, and each was
diagnosed from scratch because none of it was written down. The mechanisms
below are cheap to state and expensive to rediscover.

### Reconciliation runs BEFORE boot; containers write AFTER it

**This is the rule that keeps being missed.** LDM reconciles permissions in
`verify_runtime_environment` (`handlers/base.py`) and in `pipelines/run.py`,
both **before** the stack starts. A container then creates files and
directories of its own, with its own uid and umask, which no pre-boot pass can
have touched.

Pre-creating or pre-`chmod`-ing the PARENT does not help. `chmod -R` cannot
reach a directory that does not exist yet.

Two instances in one cycle, found days apart and initially read as unrelated:

| | what the container created afterwards | symptom |
|---|---|---|
| LDM-#1941 | `osgi/marketplace/override` | `ldm snapshot` produced no backup at all on native Linux -- the whole `osgi` tree failed one archive entry |
| LDM-#1944 | `routes/default/<ext>/` | a client extension cannot read the OAuth2 credentials Liferay publishes for it |

**Before adding a fix here, ask when the content appears.** If a container
creates it, a pre-boot pass is the wrong tool and the fix belongs after the
write -- or in what the container is told to do, not what the host did earlier.

### You cannot verify any of this on macOS or Windows

Docker Desktop's virtiofs/gRPC-FUSE bind mounts present files as the host user
**regardless of mode**. Every permission bug in this section is invisible on a
developer Mac and on Windows, and reproduces only where a bind mount performs
no uid translation -- native Linux.

This is not a footnote; it is why these ship:

- LDM-#599 hardened a mode to `750`, broke native Linux, and was only caught a
  release later by LDM-#645.
- LDM-#1941 reached a tag with four of five distros green; only Ubuntu failed,
  and nothing on macOS could have shown it.
- LDM-#1944 was reported by an external consumer, not found here.

**A permission change is not verified by a green local run or by macOS CI.** It
needs native Linux -- the Multi-OS workflow, or a Linux host. Note that
`release-e2e.yml` and `scheduled-verification.yml` trigger on **tag pushes
only**, so a PR being green says nothing about them; dispatch them against the
branch (`gh workflow run <file> --ref <branch>`) rather than discovering it on
the tag.

### The host filesystem must actually honour modes

`reclaim_volume_permissions()` shells out to Alpine to `chown`/`chmod`, and
returns whether the **command ran** -- not whether the mode changed. On a
filesystem that ignores permissions it silently does nothing and reports
success.

Measured on macOS with disposable disk images (LDM-#1946):

| filesystem | mount options | `chmod 750` | resulting mode |
|---|---|---|---|
| FAT32 | `msdos, noowners` | exit 0, no error | unchanged, `drwx------` |
| exFAT | `exfat, noowners` | exit 0, no error | unchanged, `drwx------` |
| APFS (control) | owners enabled | exit 0 | `drwxr-x---`, as asked |

**The synthesised mode is `0700`, not a permissive one.** The intuitive
assumption -- that a non-POSIX filesystem is permissive and LDM therefore works
by accident -- is exactly backwards: a container running as uid 1000 is locked
out of the entire tree, not merely of credentials.

The same applies to a POSIX volume with macOS's "Ignore ownership on this
volume" enabled, which is off by default on many external drives. **Detect this
by measurement, never by filesystem name** -- set a mode, read it back. A name
check reports "APFS" and misses the ownership-ignored case entirely.

There is no second approach to fall back to: where modes are not enforced,
`chmod 777` and group membership are equally inert. The correct behaviour is to
say so plainly, not to branch.

### Who creates what

`marketplace` is the outlier, and its absence went unnoticed for months because
a comment asserted the opposite (LDM-#1917).

| path key | created by | pre-boot reclaim | snapshot reclaim |
|---|---|---|---|
| `data` | `verify_runtime_environment` | yes | host uid, `755` |
| `state` | `verify_runtime_environment` | yes | host uid, `755` |
| `deploy` | both | yes | uid 1000, `777` |
| `files` | both | yes | uid 1000, `777` |
| `configs` | both | yes | uid 1000, `777` |
| `modules` | both | no | uid 1000, `777` |
| `cx` | both | yes | no |
| `backups` | `verify_runtime_environment` | yes | no |
| `scripts` | `validate_properties` | no | no |
| `logs` | `validate_properties` | yes | uid 1000, `777` |
| `log4j` | `validate_properties` | yes | no |
| `portal_log4j` | `validate_properties` | yes | no |
| `routes` | `validate_properties` | yes | no |
| **`marketplace`** | **nothing** | no | uid 1000, `777` (LDM-#1941) |

"Both" means `verify_runtime_environment` (`handlers/base.py`) and the Missing
Mount Paths check in `validate_properties` (`handlers/config.py`). The two
lists are maintained by hand and have drifted from each other and from
`setup_paths` -- the snapshot list still carries a `client-extensions` entry
matching **no** path key, so it has never once fired (LDM-#1942).

`marketplace`'s only creator is a `mkdir -p` run INSIDE a container, which is
skipped with no docker binary, under `--dry-run`, and on Windows -- while the
mount is declared unconditionally. Docker then creates the source as root.

**Two rules follow.** Do not add a directory to a mount without adding it to a
creator; `paths.get(key)` treats a missing key and a missing directory
identically, so a typo degrades to a silent no-op. And do not trust a comment
here -- three of them were false when checked in a single afternoon.

### Why `routes` is `777`, and why that is now a decision rather than a default

`routes` sits in the pre-boot `777` list. The comment above that list records
the reasoning as **unverified**, and says `routes` "holds Traefik dynamic
config that is only read". That is false: it holds the config trees Liferay
writes, including `oauth2.headless.server.client.secret`.

So the `777` was chosen under a belief about the contents that no longer holds
-- and LDM-#1928 is what made the tree actually carry credentials. Widening it
is defensible, but it is now a deliberate decision about exposing secrets on
the host, not the obvious default it looks like. Decide it on purpose
(LDM-#1944).

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

## Pipeline Rollback

`Pipeline.run` (`ldm_core/pipelines/base.py`) rolls back the stages that
already executed, in reverse order, when a stage fails. A stage fails in two
shapes and both roll back, but they end differently:

- an `Exception` -- rollback runs, `run()` returns `False`, the caller decides.
- a `SystemExit` (which is what **every** `UI.die` raises) or a
  `KeyboardInterrupt` -- rollback runs and the original is **re-raised
  unchanged**, so the exit code, and Ctrl-C's `130`, survive.

**`SystemExit(5)` is the one exception: it is re-raised without rolling back**
(LDM-#1636). Exit `5` is the idempotent no-op (see *Automation Standards*
below) -- the desired state already held, so nothing failed and there is
nothing to undo. `RuntimeValidationStage` refuses this way for an
already-running project, two stages after `ProjectInitializationStage` has
registered it, so treating it as a failure would run a rollback over work that
is legitimately there. It is not recorded in `context.errors` either, because
nothing went wrong. Every other exit code still rolls back.

That second clause exists because it did not (LDM-#1630). `SystemExit` derives
from `BaseException`, so the original `except Exception` never saw it and no
`UI.die` in any stage of any pipeline had ever triggered a rollback. Four rules
follow for anyone adding a stage:

- **Undo what your stage created, in your stage's `rollback`.** Rollback only
  visits stages that executed, so cleanup parked on a later stage never fires
  for an earlier failure. `ldm import` leaked `.ldm_temp/import_<timestamp>/`
  for exactly that reason -- created by `ExtractionStage`, removed by
  `BackupStateStage`, three stages further on. That stage was dissolved in
  LDM-#1635: its `execute` was an empty `pass` and it existed only to host
  other stages' cleanup, so it was the rule's standing counter-example.
- **A stage that raises does not roll itself back.** `executed_stages.append`
  runs only after `execute` returns, so the failing stage is never in the
  rollback walk. Owning the cleanup covers a failure in any *later* stage, not
  a failure part-way through your own `execute` -- guard that with a `try`
  inside `execute` if it matters. Measured, not assumed
  (`test_stage_owns_its_rollback.py`).
- **Delete only what this run created.** Rollback now fires on routine
  validation refusals, so removing a directory the user supplied is data loss,
  not cleanup. Record the fact when you create it -- `root_existed` in
  `pipelines/run.py`, not "has no LDM `meta`", which is also true of any folder
  the user asked LDM to adopt.
- **Mark the commit point.** Once a pipeline's work has actually landed,
  nothing later may undo it. `FinalizationStage` runs `ldm run` *after* the
  import is complete, and a refusal inside that nested pipeline would otherwise
  delete the finished project: `import_committed` (import) and `init_success`
  (run) are those markers.
- **Never `UI.die` inside a `rollback`.** `Pipeline.run` swallows a
  `SystemExit` raised there to protect the original exit code, which means the
  remaining stages then silently do not roll back.

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
  **Only returned in non-interactive mode** (`ldm_core/pipelines/run.py:371`):
  interactively LDM prompts to reconfigure and restart instead, so a caller
  that omits `-y`/`--non-interactive` gets a prompt rather than this code.
  Automation and E2E assertions on this contract must pass `-y`.
  Because it is not a failure, a stage refusing with `5` does **not** trigger
  a pipeline rollback -- see *Pipeline Rollback* above (LDM-#1636).
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

## The Routes Tree (Shared Config Space)

`<project>/routes` is how Liferay publishes configuration to everything else
in the project. It is a **shared space**, not a Liferay-private directory, and
it is written at runtime by Liferay itself.

This mechanism was not recorded anywhere before LDM-#1928, which is exactly how
it came to be broken three separate times. Any change to a routes mount must
be checked against this section.

| Container | Host source | Container target |
|---|---|---|
| Liferay | `routes/` (whole tree) | `/opt/liferay/routes` |
| Client extension | `routes/default/dxp` | `LIFERAY_ROUTES_DXP` (default `/etc/liferay/lxc/dxp-metadata`) |
| Client extension | `routes/default/<ext-id>` | `LIFERAY_ROUTES_CLIENT_EXTENSION` (default `/etc/liferay/lxc/ext-init-metadata`) |
| Custom service | `routes/default/dxp` | `/etc/liferay/lxc/dxp-metadata` |

### Two trees, not one

A real client extension declares **both** and reads **both**:

```json
"LIFERAY_ROUTES_CLIENT_EXTENSION": "/etc/liferay/lxc/ext-init-metadata",
"LIFERAY_ROUTES_DXP":              "/etc/liferay/lxc/dxp-metadata"
"config.node.config.trees": ["${LIFERAY_ROUTES_CLIENT_EXTENSION}",
                             "${LIFERAY_ROUTES_DXP}"]
```

`default/dxp` is what Liferay publishes about **itself** -- the main domain,
which `lxcConfig.dxpMainDomain()` resolves. `default/<ext-id>` is what Liferay
publishes about **that extension**, including the OAuth2 credentials generated
when it registers the extension's application. LDM forwarded the second
variable out of LCP.json and mounted nothing at it, so extensions were pointed
at an empty path.

**Derive the target, do not assume it.** LCP.json reaches the composer as
`ext["env"]` (`ldm_core/workspace/metadata.py:135`), so an extension declaring
a non-standard path is honoured; the constants are only a fallback.

### Deploying an extension is TWO things, with opposite timing

They are routinely conflated, and gating the wrong one breaks the chain:

| | What it is | When |
|---|---|---|
| **The zip into Liferay** | the artifact placed in `osgi/client-extensions/` | **immediately** -- before Liferay is running or ready |
| **The extension's container** | the compose service built from the extension | **only once Liferay is healthy** |

The zip is a file drop into a bind-mounted directory. `cmd_deploy`
(`ldm_core/runtime/orchestration.py:1014`) resolves the project path and
places files; it has **no running-Liferay precondition**, and must not gain
one. Liferay picks the artifact up when it next scans.

That ordering is load-bearing rather than incidental: the zip is what causes
Liferay to register the extension and create its OAuth profile at all. Delay
the zip until Liferay is ready and the whole chain starts later; gate it on
Liferay being ready and a cold start can deadlock, because the thing being
waited for is downstream of the thing being withheld.

`depends_on: {liferay: {condition: service_healthy}}` therefore belongs on the
**container only**, which is where LDM-#1928 put it.

The full chain:

```text
zip deployed (before or during boot)
  -> Liferay scans and registers the extension
  -> servlet triggered
  -> OAuth profile created
  -> credentials written to routes/default/<ext-id>
  -> visible inside the running container (the bind mount is live)
```

The container waiting on `service_healthy` sits at the END of that chain. It
starts once Liferay is serving and picks up credentials as they appear.

### When the tree is actually populated

The mounts are only half the mechanism. The contents arrive on Liferay's
schedule, not at container start, and nothing in LDM can hurry them:

1. Liferay boots and becomes healthy. Its image `HEALTHCHECK` curls
   `/c/portal/layout`, so reaching "healthy" is itself a servlet request.
2. `default/dxp` is populated with what Liferay publishes about itself.
3. **A client extension must be deployed before it has an OAuth profile at
   all.** There is nothing to publish for an extension Liferay has not seen.
4. The OAuth application — and therefore the credentials written into
   `default/<ext-id>` — is **not created until the Liferay servlet is first
   triggered.** On the deployment investigated in LDM-#1911 the files were
   stamped roughly two minutes after the extension deployed.

So `depends_on: service_healthy` guarantees Liferay is *serving* before an
extension starts. It does **not** guarantee that extension's credentials
already exist — they cannot, if the trigger has not happened yet.

**Why the design still works: a bind mount is live.** Files the host gains
after the container started appear inside it immediately. Measured, because
the whole approach rests on it. What is therefore NOT guaranteed is that an
extension which reads its config once at startup will see credentials written
later; whether the `@liferay/client-extension` SDK re-reads is outside this
repository, and is the open half of LDM-#1915.

**Docker must actually share the host path.** If the project lives outside the
paths Docker Desktop shares, Docker silently creates a VM-local directory
instead of binding the host one — the mount looks correct, the container sees
an empty directory, and nothing ever appears in it. This was mistaken for
"bind mounts are not live" while writing LDM-#1928; the first probe simply used
an unshared path.

### The rules

- **Never mount over `/opt/liferay/routes` in an extension container.** That
  path is correct for Liferay, which writes its trees there, but an extension
  built on `liferay/node-runner` does `COPY . /opt/liferay`, so it is the
  application's own route handlers. Mounting over it shadows the app and the
  container will not start (LDM-#1911; 16 `.cjs` handlers sat there on the
  deployment that reported it). The two containers do not share a filesystem
  convention -- every regression here came from assuming they did.
- **Everything that mounts a subtree waits for Liferay.**
  `depends_on: {liferay: {condition: service_healthy}}`. The tree is written
  at boot, so a container that starts first reads an empty directory.
  `service_healthy` resolves against the `liferay/dxp` image's **own**
  `HEALTHCHECK`, which curls `/c/portal/layout` -- it means "serving pages",
  not "process started". `_build_liferay_service` declares no compose-level
  healthcheck, so this depends on the image providing one.
- **Scaffold the host directory before mounting it.** Docker creates a missing
  bind-mount source itself, as an empty root-owned directory. Subtrees are
  created from the generated compose (`_scaffold_routes_tree`) so a mount and
  its directory cannot drift, using local `paths` and never the remote-mapped
  `mount_paths`.
- **A custom service is an arbitrary third-party image.** It gets the same
  shared space, but everything LDM adds is additive: a volume, variable,
  `extra_hosts` or `depends_on` the user declared always wins.

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
*Last Updated: 2026-09-23* | *Last Reviewed: 2026-09-23*
