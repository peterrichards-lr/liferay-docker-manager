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
| LDM-#1944 | `routes/default/<projectName>/` | Liferay creates and populates it after boot, so nothing LDM does before the stack starts can touch it |

**Before adding a fix here, ask when the content appears.** If a container
creates it, a pre-boot pass is the wrong tool and the fix belongs after the
write -- or in what the container is told to do, not what the host did earlier.

#### How Liferay names the per-extension routes directory

Liferay resolves the directory from the extension's **`projectName`**, not from
its `LCP.json` id. LDM must mount the same name, or it binds a directory
Liferay never writes to.

The CX build emits `<name>.client-extension-config.json`, which carries **both**
identifiers:

```text
projectId   = ecopulseheadlessauth      <- equals the LCP.json id
projectName = ecopulse-headless-auth    <- equals the extension directory
```

`BaseConfigurationFactory` (`oauth2-provider-service`) reads the property and
publishes it as a ConfigMap label:

```java
String projectName = GetterUtil.getString(
    properties.get("ext.lxc.liferay.com.projectName"),
    (String)properties.get("projectName"));
...
labels.put("ext.lxc.liferay.com/projectName", _projectName);
```

`RoutesPortalK8sConfigMapModifier` then resolves the path straight from that
label:

```java
String projectName = labels.get("ext.lxc.liferay.com/projectName");
Path projectPath = virtualInstanceIdPath.resolve(projectName);
Files.createDirectories(projectPath);
```

So the tree Liferay publishes is `routes/default/<projectName>`.

**The two identifiers differ for essentially every extension.** The id drops
the hyphens the directory keeps -- measured across `ldm-cx-samples`, 16 of 16.
Treating them as interchangeable works only for an extension whose name happens
to contain no hyphens.

LDM already parses `*client-extension-config.json` (`workspace/metadata.py`
reads `oauth_erc` from it), so `projectName` is available wherever the mount
path is decided.

**A wrong name does not surface as a permission error.** Both directories
exist and both are readable: `_scaffold_routes_tree` creates whatever the
generated compose declares, so an extension bound to the wrong name reads a
real, empty, readable directory while Liferay populates a different one beside
it. When diagnosing an empty config tree, establish which of the two it is --
"cannot read the directory" and "read a directory with nothing in it" are
indistinguishable in a log that only reports the tree as empty.

#### The container's umask is usually the right lever, not a reclaim

Where a container writes files the host or another container must read, the
lever is usually the writing process's umask, set before it starts -- not a
reclaim afterwards. A mode that is wrong from creation cannot be reconciled by
a pre-boot pass, and racing it after the write is worse.

**Liferay runs under Tomcat, and `catalina.sh` defaults its umask to `0027`:**

```sh
# Set UMASK unless it has been overridden
if [ -z "$UMASK" ]; then
    UMASK="0027"
fi
umask $UMASK
```

`0027` is `750` on directories and `640` on files. Every config tree Liferay
publishes under `routes/` was therefore readable only by uid 1000, and a
client extension runs as whatever uid its own image declares. LDM now sets
`UMASK=0022` on the Liferay service (`handlers/composer.py`), which the
`[ -z "$UMASK" ]` guard is what makes possible.

Three things worth carrying forward:

- **Nothing in Liferay sets a mode.** The k8s agent calls
  `Files.createDirectories` with an empty `FileAttribute[]` and the class
  contains no permissions API at all. The umask is the only input, so there is
  no "deliberate restriction" to work around: the mode is whatever the writing
  process's umask produced, and nothing in Liferay chooses it.
- **Group-readable is not readable.** `0027` yields `640`, which HAS group
  read. It is still the bug, because the extension is not in Liferay's group.
  Any assertion here must test **other**-read; a check for group-read passes
  against the defect.
- **Measure through the real startup.** A bare `sh -c 'mkdir -p'` in the same
  image reports `755`/`644`, because it bypasses `catalina.sh` entirely. That
  probe suggests the bug does not exist. Read the running process's umask from
  `/proc/<pid>/status` inside the container instead -- which also works on a
  macOS or Windows host, since it reports Linux process state rather than
  anything about the host filesystem.
- **A MODE bug is visible on macOS; an OWNERSHIP bug is not.** The section
  below says you cannot verify any of this off native Linux. That is true of
  uid-translation failures and it is what LDM-#599 and LDM-#1941 were. It is
  NOT true of modes. Measured on APFS: a file written by a container at umask
  `0027` reads back `640` on the host, and `644` at `0022` -- Docker Desktop
  rewrites ownership, not the mode bits. So a mode can be asserted anywhere.
  What macOS cannot show is the **consequence**: each container is handed
  ownership of whatever it mounts, so a second container at an unrelated uid
  reads that `640` file happily and a refusal never reproduces. A uid-access
  failure therefore needs native Linux even though the mode that causes it is
  visible everywhere.

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

`migrate_layout` (`handlers/base.py`) is the pre-boot scaffold: it creates
every essential directory plus `routes/default/dxp`, in pure Python, with no
docker, platform or dry-run early exit. The name is historical -- it does not
migrate anything -- and it was silently unwired for months by `64c75e9f`, which
is why `marketplace` had no creator at all (LDM-#1917). It runs in
`EnvironmentSetupStage`, **before** `ComposerStage`, because the point is to
deny Docker the chance to create a bind-mount source itself, which it does as
root (the LDM-#1134 hazard).

**There is no single "pre-boot reclaim".** LDM-#2143: this table used to show
one such column, and the code had FOUR reclaim loops carrying two different
lists (five since LDM-#2266). A reader took "yes" as unconditional, found `routes` unreclaimed on the
path their command actually took, and spent a day concluding something exotic
was happening on their node. One column per site, so disagreement is visible
rather than averaged away.

**The reclaim matrix below is generated** (LDM-#2151). `scripts/sync_reclaim_table.py`
reads the reclaim loops from source by AST and rewrites the block between the
markers here and in `docs/explanation/permissions-and-reclaim.md`; the
`sync-reclaim-table` pre-commit hook runs it with `--check`, so a list changed
without regenerating fails the commit. Edit the source, then run the script --
never the block. The parser is `scripts/reclaim_sites.py`, shared with
`ldm_core/tests/test_routes_is_reclaimed.py` so there is one reading of the
lists, not two. A reclaim loop it cannot read is a hard error, not a missing
row.

`pipelines/run.py` holds two loops -- the remote-target branch and the local
one -- with identical lists, shown as one column. The generator refuses if
they ever diverge.

<!-- RECLAIM_TABLE_START -->
<!-- Generated by scripts/sync_reclaim_table.py from the reclaim loops in source. Do not edit by hand. -->

| path key | pre-boot (`handlers/base.py`) | run pipeline (`pipelines/run.py`) | repair (`ldm rescue`) | node, before sync (`config.py`) |
|---|---|---|---|---|
| `data` | yes | no | no | no |
| `state` | yes | no | no | no |
| `logs` | yes | yes | yes | yes |
| `deploy` | yes | yes | yes | yes |
| `log4j` | yes | no | no | no |
| `portal_log4j` | yes | no | no | no |
| `files` | yes | yes | yes | yes |
| `configs` | yes | no | no | no |
| `backups` | yes | no | no | no |
| `cx` | yes | no | no | no |
| `routes` | yes | yes | yes | yes |
| `osgi` | no | yes | yes | yes |

<!-- RECLAIM_TABLE_END -->

`osgi` is the parent of `configs`, `state`, `modules` and `marketplace`, and the
helper is recursive -- so the `run.py`, `rescue` and node lists name the parent
where the pre-boot list names the children. A `no` for `configs` or `state` in
those columns is therefore covered via `osgi`, not skipped; `modules` and
`marketplace` are in no list by name and are reclaimed only that way.

**Gates, which the old table did not record.** The `run.py` loops are not
unconditional: the remote branch runs whenever the target is remote, and the
local branch only `if platform.system() == "linux" or --fix-permissions`. On
macOS without that flag, nothing in the run pipeline reclaims anything. The
pre-boot loop also skips `data` and `state` when they are Docker named volumes
(LDM-382).

**The node column runs before every remote sync** (LDM-#2266):
`sync_project_to_target` in `config.py`, existing trees only, chowned to the
node's SSH uid -- never this host's, which is a stranger on the node and makes
rsync's own chgrp fail exactly as before. Teardown
(`remove_project_from_target`) reclaims the whole project directory before its
`rm -rf`; it is not a loop and is not in the matrix. Both exist because the
`run.py` remote reclaim runs AFTER the sync, so it repaired the next run rather
than the current one.

Which code creates each tree, and what the snapshot reclaim does to it, derive
from different code again -- the two directory-creation lists in
`handlers/base.py` and `snapshot/archive.py` -- and are **hand-written**, out of
scope for the generator (LDM-#2151):

| path key | created by | snapshot reclaim |
|---|---|---|
| `data` | `verify_runtime_environment` | host uid, `755` |
| `state` | `verify_runtime_environment` | host uid, `755` |
| `deploy` | both | uid 1000, `777` |
| `files` | both | uid 1000, `777` |
| `configs` | both | uid 1000, `777` |
| `modules` | both | uid 1000, `777` |
| `osgi` | -- | no |
| `cx` | both | no |
| `backups` | `verify_runtime_environment` | no |
| `scripts` | `validate_properties` | no |
| `logs` | `validate_properties` | uid 1000, `777` |
| `log4j` | `validate_properties` | no |
| `portal_log4j` | `validate_properties` | no |
| `routes` | `validate_properties` | no |
| `marketplace` | `migrate_layout` | uid 1000, `777` (LDM-#1941) |

The user-facing explanation of all this -- what the helper container is, why the
mode is `777`, and the `routes` special case -- is
`docs/explanation/permissions-and-reclaim.md`. Prefer extending that; this table
is the implementation detail behind it.

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

The user-facing statement of this contract is `docs/reference/exit_codes.md`,
with an EXIT STATUS section in `ldm_core/resources/ldm.1` (LDM-#2428). Change
a code here and update both in the same pull request: users' scripts branch on
the page, not on this file. The triage notes below stay here, for agents.

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
- `6`: Privileged Step Required (LDM-#2517, v2.27.0) -- LDM needs root and,
  under `-y`, only tried `sudo -n`, which cannot prompt. It never hangs and
  never takes a password: it prints the exact command (an argv, shell-quoted
  once with `shlex.join`) and, with `--json` or `LDM_JSON=1`, a
  `{"privileged_step": {"reason", "command", "resume"}}` line on stdout. All of
  it lives in `ldm_core/privileged.py`. Use `privileged.require()` when the step
  blocks the command and `privileged.defer()` for cleanup or optional extras --
  `defer` lets the command finish (`ldm down --clean-hosts` removes hosts entries
  BEFORE `compose down`; exiting there would leave the containers up) and
  `cli._dispatch_command` turns its success into `6`. Not `2`: automation reads
  `2` as "supply a credential and retry", and this needs a person instead. A new
  sudo call site MUST either run interactively or go through this module.
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
  0-6/126 contract governs LDM's own top-level command outcomes, not every
  exit path of every subprocess it shells out to.
- **`130` on user interrupt**: `Ctrl+C` exits `130` (`128 + SIGINT`), the POSIX
  convention, standardized across the three places that catch `KeyboardInterrupt`
  --- `ldm_core/utils.py:593` (inside `run_command()`), `ldm_core/ui.py:574`
  (interactive prompts) and `ldm_core/cli.py:3139` (the top-level handler). Like
  `124`/`127` this is a deliberate shell-convention exit, not an LDM-contract
  code, so do not renumber it into the 0-6 range.

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

**The contract lives in [`docs/explanation/routes-tree.md`](../../../docs/explanation/routes-tree.md)**
(LDM-#2152): the mount table, the variables LDM sets, the
`depends_on: service_healthy` requirement and what it resolves against, the
`projectName` naming rule, deploy timing, when the tree is populated, and the
no-port DXP domain rule -- each with the incident that produced it. Read it
before touching any routes mount. Do not restate the table here or anywhere
else; point at that page. The code it describes is `handlers/composer.py`:
`LXC_ROUTES_MOUNT`, `_extension_routes_mounts`, `_extension_routes_env`,
`_apply_shared_project_context`, `_scaffold_routes_tree`, and the Liferay
service's `/opt/liferay/routes` volume. If the doc and the code disagree, the
code wins and the doc is the bug.

This mechanism was not recorded anywhere before LDM-#1928, which is exactly how
it came to be broken three separate times.

### What an agent must do that a developer need not

- **Check any proposed mount against the doc's table before adding it.** A
  routes mount that is not in the table is a design change, not a fix. Update
  the doc in the same change, or do not make it.
- **Never mount over `/opt/liferay/routes` in an extension container**
  (LDM-#1911). It is the right path for Liferay and the application's own code
  for a `liferay/node-runner` extension.
- **Do not re-narrow the extension mount to a per-extension leaf** to restore
  credential isolation. It reintroduces the stale-inode bug (LDM-#1944). Do not
  widen it to `routes` either:
  `test_refactor_regressions.py::test_it_is_the_instance_subtree_not_the_whole_routes_directory`
  pins `routes/default` deliberately.
- **Never name a routes directory from the LCP.json id.** Use `projectName`
  (`ext["project_name"]`, parsed by `parse_cx_project_name` in
  `ldm_core/workspace/metadata.py`).
- **Keep the `LIFERAY_ROUTES_*` override ordering.** `_extension_routes_env` is
  appended AFTER the extension's own `ext["env"]` so LDM wins on those two;
  every other variable keeps "the extension's declaration wins". Moving it
  before re-breaks LDM-#1944.
- **Never gate the zip on Liferay being ready.** `cmd_deploy`
  (`ldm_core/runtime/orchestration.py`) has no running-Liferay precondition and
  must not gain one; `service_healthy` belongs on the container only
  (LDM-#1928).
- **Scaffold from the generated compose, never from a second hand-kept list.**
  `_scaffold_routes_tree` derives directories from the volume sources and from
  `LIFERAY_ROUTES_*` values inside `LXC_ROUTES_MOUNT`, using local `paths` and
  never the remote-mapped `mount_paths`.
- **A custom service is an arbitrary third-party image.** Everything LDM adds
  is additive: a volume, variable, `extra_hosts` or `depends_on` the user
  declared always wins.
- **Do not "fix" `:8080` in the DXP domain from the host side** (LDM-#2289). A
  post-readiness rewrite was written once and discarded; the correction is the
  `dxp-routes-domain` bundle (see *Portal Workarounds*).
- **Do not conclude from a green E2E that an extension will pick its
  credentials up.** The race between `service_healthy` and the credentials
  existing is inherent (LDM-#1915, LDM-#1978); re-reading is the consumer's
  job.

## Portal Workarounds (internal OSGi bundles)

Some Liferay defects can only be corrected inside the portal JVM. LDM ships the
bundle for each one in `ldm_core/resources/portal_workarounds/`, and
`sync_workarounds` (`ldm_core/runtime/portal_workarounds.py`, called from
`ComposerStage`) places it in the project's `osgi/modules` on every
`ldm run`. Nothing outside LDM needs to know they exist.

- **The source lives in `liferay-custom-osgi-modules`.** LDM vendors a
  released jar, renamed without version or line, so only the content changes
  when it is refreshed.
- **Each workaround holds one or more variants, each a jar with a tag
  window** equal to its `Import-Package` range. The project gets the variant
  whose window holds its tag; every other copy sharing the workaround's
  prefix is removed, so changing line swaps the jar rather than adding one,
  and outside every window there is none. Each window must be verified live
  at both ends; the bundle loading proves nothing about the defect it targets.
- **A new line that the current jar cannot resolve on gets its own variant.**
  Liferay can bump a package between quarters without fixing the defect. Add a
  build from `liferay-custom-osgi-modules` for that line as a second variant
  with its own jar name; windows must not overlap, which a test enforces.
- **Retire per line, then everywhere.** When Liferay ships the fix, end the
  last variant's window at that release: newer lines drop the bundle, older
  ones keep it. Once no supported line needs it, move the prefix from
  `WORKAROUNDS` to `RETIRED_PREFIXES` and every project drops it on its next
  run, including copies carried in by snapshots and `.ldmp` packages made
  before LDM-#2359.
- **Snapshots and packages never carry the bundle (LDM-#2359).** The archive
  adds the `osgi` tree with a tar filter that drops every `osgi/modules` jar
  `is_ldm_bundle` matches (any `WORKAROUNDS` or `RETIRED_PREFIXES` prefix), and
  the manifest's `osgi_modules` / `includes_osgi_modules` ignore it too.
  `ldm package` tars the snapshot directory, so the exclusion at capture
  covers it; the importing `ldm run` installs the variant for its own tag. A
  package built with `--snapshot`/`--use-latest` from an older snapshot can still
  hold a copy, which that run then replaces or removes.

| Bundle | LDM issue | Upstream | Variant | Tags |
|---|---|---|---|---|
| `dxp-routes-domain` | LDM-#2289 | LDM-#2344 | `com.liferay.custom.dxp.routes.domain.jar` | `2026.q1.14` up to `2026.q4` |

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
*Last Updated: 2026-11-25* | *Last Reviewed: 2026-11-25*
