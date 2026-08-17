# Remote Node Architecture: Target Resolution

**Status:** Approved design — Phase 1 (`TargetContext` + `resolve_target_context()`) implemented. See [Migration plan](#6-migration-plan) for remaining phases.

**Scope:** How LDM resolves and uses `--node`/`--target` compute targets across the codebase.

---

## 1. The core problem

Remote-node support was added as a *parameter threaded through existing code paths*, not as its own concept. There was no single place that decided "what target does this operation use, and how do I reach it" — that decision was re-derived independently at every call site, using whichever of three overlapping signals (explicit CLI flag, project metadata, persisted default) that particular call site happened to remember to check, in whatever order its author happened to pick.

The identical bug appeared independently in three different files, because there was no one function doing this resolution:

| File | Symptom | Root cause |
|---|---|---|
| `handlers/base.py` (`_pre_flight_checks`) | Brand-new project's `ldm run --node X` ran the local-only port check anyway | `meta["target"]` is never populated during `run` itself, and `self.target` wasn't checked first |
| `handlers/composer.py` (`write_docker_compose`) | Bind-mount paths stayed local even with a persisted default target set | `get_active_target()` was only called inside `if target_name:` — skipped entirely when nothing explicit was set, so the persisted-default fallback never ran |
| `diagnostics/info.py` (`run_list`/`run_status`/`run_info`) | `ldm list` displayed `Target: local` while the project was actually running on the persisted default `aws-2` | `meta.get("target", "local")` — passing the literal string `"local"` into `get_active_target()` makes it treat that as an *explicit* choice, which short-circuits its own persisted-default check |

Three fixes, three files, one underlying cause. Without a shared abstraction, there will be a fourth place with the same bug the next time someone touches this.

## 2. Full inventory of the problem's actual size

A dedicated audit (covering every `docker`/`docker compose` invocation in `ldm_core/` outside tests) found:

- **~18 call sites** using `get_compose_cmd()` (`ldm_core/utils.py`), which is not context-aware at all — always returns `["docker", "compose"]` regardless of any active target.
- **A second, distinct bug class**: call sites that *do* go through the context-aware `DockerService.*` methods, but simply never pass the `target_name` parameter even though it was sitting in scope (usually from an already-read `meta` dict two lines above).
- `InfraService.target` (`handlers/infra.py:18`) is declared but never assigned anywhere in the codebase — dead plumbing.
- Bind-mount path remapping for remote targets required a hand-rolled, duplicated dict (`mount_paths`) built inline inside `composer.py`'s `write_docker_compose()`, mirroring `setup_paths()`'s structure by hand rather than through any shared abstraction.

High-impact clusters still using the old pattern (full list preserved on issue #1133):

- `runtime/orchestration.py`: `cmd_stop`, `cmd_restart`, `cmd_down`, `cmd_reset` all hardcode local, directly inconsistent with `cmd_start` in the *same class*, which already resolves the target correctly.
- `handlers/infra.py`: `setup_global_database`/`setup_global_search` — every Docker call hardcoded local, even when called from an already target-aware caller. A project correctly targeting a remote node still provisions its shared DB/search locally.
- `dashboard/server.py`: `api_projects`/`api_logs` — reads and *displays* `meta.get("target")` in the same function that queries the wrong (local) daemon for status.
- `runtime/readiness.py`: `cmd_wait`/`_wait_for_ready` — the core post-`run` health-polling loop, every Docker call raw/local throughout.
- A long tail of medium-impact call sites in `runtime/fragments.py`, `runtime/search.py`, `handlers/share.py`, `snapshot/*.py`, `handlers/mcp.py`, `diagnostics/doctor.py`.

Separately, categories that are ambiguous by *design*, not just unfixed — these need a product decision, not a code fix: `diagnostics/prune.py`, `handlers/system.py`'s `cmd_nuke`/`cmd_rescue`, `handlers/database.py`'s shared-DB `cmd_start`/`cmd_stop`. None of these currently have any target concept at all, and it's not obvious they should — "prune the orchestrating host's own Docker Desktop" is arguably correct as a host-local operation regardless of any project's target.

## 3. Additional UX gaps found via live use

- **Silent remote provisioning.** When a persisted default target is set via `ldm target use`, `ldm run` on a plain project (no explicit `--node`) silently provisions against that remote target with no upfront indication — the first sign is `--verbose` output showing `docker --context aws-2 ...` mid-run. A user who forgot they'd set a default, or inherited a machine with one already set, has no warning before real infrastructure gets touched.
- **Redundant double confirmation.** Re-running `ldm run` against an existing, currently-running project shows *two* separate confirmations in sequence: an `interruptible_pause` ("already exists, CTRL+C to cancel") from `ProjectInitializationStage`, followed by a blocking `UI.confirm` ("already running, reconfigure and restart? [Y/n]") from `RuntimeValidationStage`. These fire independently because the two stages don't share what they each already know. The CTRL+C style pause is preferred where one is sufficient, since it doesn't block indefinitely on input.

Neither of these is a "remote node" bug specifically, but both stem from the same underlying issue: state (is this remote? is this already running? has the user already been warned once this command?) isn't tracked as a single resolved thing and passed along — it's recomputed piecemeal at each point that happens to need it.

## 4. Resolution precedence

Three overlapping signals decide "what target should this operation use," ranked most to least specific:

1. **Explicit `--node`/`--target` CLI flag** — a per-invocation override, for a user who wants to target a specific node "just this once."
2. **The project's own persisted `meta["target"]`** — set via `ldm target set`, or pinned automatically the first time an unpinned project resolves a target (see [Pinning](#pinning) below). This lives in the project's own metadata, not `~/.ldmrc`, because a project's source/config always lives locally regardless of where it executes — where a *specific project* runs is a property of that project, not of the machine running LDM.
3. **The persisted global default** (`ldm target use`, i.e. `TargetNode.is_default`) — the "set and forget" fallback for a user who mostly works against one non-local node.
4. **`local`** — the ultimate fallback when nothing else is configured.

This is precisely `get_active_target()`'s own existing logic — the design here isn't a new algorithm, it's making sure every caller reaches it the same way (passing `None` through when nothing is known, never a hardcoded `"local"` string) and stops re-implementing the priority chain by hand.

**Two metadata locations, by design, not a bug.** `~/.ldmrc` is the catalog of known nodes and which one is the global default. A project's own metadata file is where that *specific project's* target assignment lives. These aren't competing sources of truth — they're different questions ("what nodes exist, and which is the default?" vs. "where does *this* project run?").

### Conflict

If an explicit `--node` disagrees with a project that's **already pinned** to a different target, LDM warns and gives the user a CTRL+C window before proceeding with the override for that run only. The project's synced files, named volumes, or other state may only exist on the pinned node, so silently switching would produce confusing "it's not there" failures. The override is **not** written back as a new pin — a one-off `--node` flag shouldn't silently and permanently reassign a project. Use `ldm target set`/`ldm target migrate` for that.

### Pinning

If a project has **no pinned target yet**, whatever it resolves to — whether from an explicit `--node` or by falling through to the global default — is written back into the project's metadata. This stops a project's effective target from silently drifting if the global default changes later: without pinning, a project that only ever inherited the ambient default would appear to move to a different node the moment someone runs `ldm target use` for something unrelated, even though its actual files and volumes never moved. This is exactly the failure mode observed live: a project provisioned on `aws-2` (the default at creation time) was later displayed as a `local` node project once other commands re-derived the target independently instead of reading a pinned value.

### Open question: sync strategy for `deploy`/`monitor`

Commands like `ldm deploy`/`ldm monitor` may need continuous/incremental remote sync (not just the one-time `sync_project_to_target()` used at provisioning) to push files to a remote node before container deployment. This is explicitly **not resolved** by this design — it's deferred to whenever those specific commands are migrated (Phase 5). Existing SSH/rsync infrastructure (`sync_project_to_target()`, `resolve_remote_home()`) may be reusable as-is or may need an incremental mode.

## 5. One resolver function, every command calls it

`resolve_target_context()` (`ldm_core/config.py`) is the single function every command calls to find out what compute target it's running against, implementing the precedence chain, conflict warning, and pinning write described above. It returns a `TargetContext`:

```python
@dataclass
class TargetContext:
    target: TargetNode            # the resolved node (local/remote)
    is_remote: bool
    docker_prefix: list[str]      # ["docker"] or ["docker", "--context", name]
    compose_prefix: list[str]     # docker_prefix + ["compose"]
    conflict_overridden: bool = False
    newly_pinned: bool = False
    local_root: Path | None = None
    remote_root: str | None = None

    def map_path(self, local_path: Path) -> Path | PurePosixPath:
        """Local targets: identity. Remote targets: rewrite onto the
        already-synced remote project root, replacing composer.py's
        hand-rolled mount_paths dict."""
```

**What this fixes structurally, not just symptomatically:**

- The "hardcoded local before checking" bug class becomes structurally impossible — there's one function that resolves the target, and everything else just reads the result. Keeping this to one choke point also makes it far easier to debug and fix if new issues surface, rather than chasing the same class of bug across a dozen independently-written call sites.
- `composer.py`'s hand-rolled `mount_paths` dict is replaced by `context.map_path()`, removing duplicated path-construction logic and making the remote path mapping testable in one place instead of embedded in compose generation.
- `ldm list`/`status`/`info`'s display and the actual Docker command construction are guaranteed to agree, because they'd read the same resolved context instead of two independently-computed values that happened to diverge.
- New code gets this by default (read from context) rather than by remembering to call `get_active_target()` correctly — the failure mode shifts from "silent wrong behavior" to "doesn't compile/obviously missing a parameter."

**Where it's called from:**

- For the `run`/`init` pipeline: resolved once in `ProjectInitializationStage` (where `project_meta` first becomes available) and threaded through `PipelineContext`, the same way `paths`/`project_meta` already are. Every later stage (`ComposerStage`, `RuntimeValidationStage`, `ExecutionStage`) reads it from context instead of recomputing it.
- For ad-hoc handler methods (`orchestration.py`, `diagnostics/info.py`, `handlers/infra.py`, etc.): called once per command entry point, with the resulting context threaded through rather than re-resolved.

## 6. Migration plan

Phased, each phase unit-tested *and* live-verified against a real remote node before merging — Docker command construction is exactly the kind of change that can be unit-test-green and still wrong:

1. **Introduce the abstraction.** `TargetContext` + `resolve_target_context()`, with full unit coverage. No behavior change — exists alongside current code, unused.
2. **Migrate the `run`/`init` pipeline.** Highest-traffic path; replaces `composer.py`'s `mount_paths` hack directly.
3. **Migrate `orchestration.py`** (`cmd_stop`/`restart`/`down`/`reset`) — the highest-confidence cluster from the #1133 audit, already inconsistent with its own sibling `cmd_start`.
4. **Migrate `diagnostics/info.py` and `dashboard/server.py`** — closes the display-vs-reality gap permanently.
5. **Sweep the remaining medium-impact call sites** (`fragments.py`, `search.py`, `share.py`, `snapshot/*.py`, `mcp.py`, `doctor.py`) as a broader pass once the pattern is established and proven.
6. **Revisit the category-(b) items** (`prune`, `nuke`, `rescue`, shared DB) as their own product conversation, once the mechanism exists to make them target-aware *if* that's the decision.

Separable, smaller UX fixes not blocked on the full migration:

- Collapsing `ldm run`'s redundant double confirmation for already-running projects down to one CTRL+C-style pause.
- Surfacing the effective resolved target upfront, before provisioning begins, when it's not `local`.

See also: [Multi-Node Orchestration & Remote Node Setup](../how-to/multi_node_orchestration.md) for the user-facing `ldm target` guide, and [Architecture Overview](architecture.md) for how this fits into LDM's broader layering.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-17* | *Last Reviewed: 2026-08-17*
