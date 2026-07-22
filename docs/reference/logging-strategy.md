# LDM Logging Strategy

This document defines the official logging architecture for Liferay Docker Manager. It exists to prevent inconsistent use of output levels across the codebase and to ensure the CLI feels appropriately terse to end users while remaining fully debuggable for developers and CI pipelines.

---

## Logging Tier Architecture

LDM uses **four console output tiers** and one silent file tier:

| Tier | Method | Flag | Icon | Audience |
|:---|:---|:---|:---|:---|
| **Standard** | `UI.success()` `UI.warning()` `UI.error()` `UI.info()` `UI.heading()` | *(always)* | ✅ ⚠️ ❌ ℹ | All users |
| **Info** | `UI.detail()` | `--info` / `-i` | ℹ | Power users, troubleshooting |
| **Verbose** | `UI.debug()` | `--verbose` / `-v` | ⚙️ | Developers, CI debug |
| **Trace** | `UI.trace()` | *(always, file only)* | — | Log analysis (`~/.ldm/last-command.log`) |

```text
ldm run <project>            # Standard output only
ldm run <project> --info     # Standard + Info tier
ldm run <project> --verbose  # Standard + Info + Verbose
```

> **Rule:** Every log call must be placed at the correct tier at the point of writing. Do not escalate to a higher tier for convenience — demote to the correct tier.

---

## Method Reference

### `UI.info(msg)` — Standard tier, always shown

Use for **phase-level milestones** that mark meaningful progress a user needs to track.

**Use when:**

- A named phase is starting or completing
- The user would be confused or alarmed if this line didn't appear
- This represents a top-level action the user explicitly requested

**Examples:**

```python
UI.info("Downloading LDM package: liferay-ai-commerce-accelerator.ldmp...")
UI.info("Triggering orchestrated database restore (postgresql)...")
UI.info(f"Starting {project_id} stack ({tag}, {db_mode}, {host_name})...")
UI.info("Generating SSL certificates for aica.local...")
UI.info("Waiting for Elasticsearch to become ready...")
```

**Do NOT use for:**

- Sub-steps within a phase (use `detail()`)
- Docker container lifecycle ops (use `detail()`)
- Internal state reads/writes (use `debug()`)
- File-level operations unless directly user-visible (use `detail()`)

---

### `UI.detail(msg)` — Info tier, shown with `--info`

Use for **sub-step operations** within a phase that a power user may want to follow.

**Use when:**

- This is a meaningful step inside a larger named phase
- A developer or advanced user would want visibility into this step for troubleshooting
- The step is expected and routine (not alarming)

**Examples:**

```python
UI.detail("Scaffolding Docker environment for restore...")
UI.detail(f"Hydrating volume: {volume_name} from host")
UI.detail(f"Synchronising virtual host to {host_name}")
UI.detail(f"Reclaimed volume ownership across {n} directories")
UI.detail("Checking infrastructure stack (Traefik SSL Proxy)...")
UI.detail(f"Synced license from Common: {match.name}")
UI.detail("Scheduled automatic search reindex for next boot.")
```

**Do NOT use for:**

- Top-level phase milestones (use `info()`)
- Raw Docker command output (use `debug()`)
- API response payloads (use `debug()`)

---

### `UI.debug(msg)` — Verbose tier, shown with `--verbose`

Use for **developer-level internals** that are needed to diagnose failures but produce noise for normal users.

**Use when:**

- The content is a raw command, API payload, or internal state value
- Only a developer or CI engineer debugging a failure would need this
- The output would be meaningless to an end user

**Examples:**

```python
UI.debug(f"Running: {' '.join(cmd)}")
UI.debug(f"docker inspect output: {result}")
UI.debug(f"Detected ports {ports} from {lcp_file.name}")
UI.debug(f"Tag cache hit: {cache_key}")
UI.debug(f"  Reclaiming: {path}")  # per-directory detail within a batch
UI.debug(f"API response: {response.status_code} {response.text[:200]}")
```

---

### `UI.success(msg)` — Standard tier, always shown

Use for **completed milestones** that confirm a meaningful outcome to the user.

**Use when:**

- A phase or major operation has completed successfully
- The user needs positive confirmation that something worked

**Examples:**

```python
UI.success("LDM package checksum verified successfully.")
UI.success("Snapshot integrity verified.")
UI.success("Database restored successfully.")
UI.success("Restore complete.")
UI.success(f"Liferay is ready  ({elapsed})")
```

**Do NOT use for:**

- Sub-step completions within a phase (use `detail()`)
- Trivial internal state confirmations (use `debug()`)

---

### `UI.warning(msg)` — Standard tier, always shown

Use **only** for genuine user-actionable advisories where something unexpected occurred or the user may need to act.

**Use when:**

- Something unexpected happened but execution can continue
- The user may need to take action (e.g. check a setting, restart a service)
- A best-practice violation was detected

**Legitimate `warning()` uses:**

```python
UI.warning(f"Port conflict detected! Using {new_port} instead of {orig_port}.")
UI.warning("Version downgrade detected. A pre-upgrade backup has been created.")
UI.warning(f"SSL certificate for {host} is not trusted by the system keychain.")
UI.warning(f"Search restore timed out. Indices may be incomplete — run: ldm reindex {project_id}")
UI.warning(f"Stale project lock auto-recovered (PID {pid} no longer running).")
UI.warning("Using '.local' TLD — ensure mkcert is installed and trusted.")
```

**Do NOT use for:**

- Expected, routine operations (use `info()` or `detail()`)
- Volume permission reclaims — these are expected Docker behaviour (use `detail()`)
- Container teardown during a reset — this is expected (use `detail()`)

> The warning icon carries emotional weight. Every `warning()` line tells the user "something may be wrong". Use it sparingly so genuine warnings are not ignored.

---

### `UI.error(msg)` — Standard tier, stderr

Use when an operation **failed but execution continues** in a degraded state.

```python
UI.error("Client extension deployment failed.", details=str(e), tip="Check: ldm logs")
UI.error("Elasticsearch failed to become ready in time.")
```

---

### `UI.die(msg, exit_code=1)` — Standard tier, stderr, halts execution

Use when a failure is **unrecoverable and execution must stop**.

```python
UI.die("Database restore failed after all retries. Original data preserved.", exit_code=3)
UI.die("Elasticsearch failed after 2 restart attempts.", exit_code=3)
```

---

### `UI.heading(msg)` — Standard tier, section headers

Use for **top-level command banners**, not for phase progress. Phase progress uses `UI.phase()`.

```python
UI.heading("Starting Quickstart: AICA")  # top of a command
```

---

### `UI.phase(current, total, label)` — Standard tier, phase progress

Use for **multi-step command progress orientation**. Only applicable to commands with 3+ distinct phases.

```python
UI.phase(1, 5, "Downloading")
UI.phase(2, 5, "Restoring")
UI.phase(3, 5, "Configuring")
```

---

## Decision Tree

When writing a new log call, apply this decision tree:

```text
Is this a failure?
+-- Yes, unrecoverable          --> UI.die()
+-- Yes, execution can continue --> UI.error()
+-- No --> continue

Is this a genuine user-actionable advisory?
+-- Yes --> UI.warning()
+-- No --> continue

Is this a completed milestone or positive confirmation?
+-- Yes --> UI.success()
+-- No --> continue

Is this a top-level phase the user explicitly triggered?
+-- Yes --> UI.info()
+-- No --> continue

Is this a sub-step within a named phase?
+-- Yes --> UI.detail()
+-- No --> continue

Is this an internal state value, raw command, or API payload?
+-- Yes --> UI.debug()
```

---

## Anti-Patterns

### Do not use `warning()` for expected operations

```python
# Wrong - routine Docker operation
UI.warning(f"Reclaiming volume permissions for {path}...")

# Right - batch summary at detail level
UI.detail(f"Reclaimed volume ownership across {n} directories")
```

### Do not use `info()` for sub-steps

```python
# Wrong - sub-step of restore phase
UI.info("Scaffolding Docker environment for restore...")

# Right
UI.detail("Scaffolding Docker environment for restore...")
```

### Do not use `info()` for internal state dumps

```python
# Wrong
UI.info(f"  {key}={value}")  # property dump

# Right
UI.debug(f"  {key}={value}")
```

### Do not emit O(n) lines where n is unbounded

```python
# Wrong - 1 line per directory
for path in paths:
    UI.warning(f"Reclaiming volume permissions for {path}...")

# Right - 1 summary line, per-item detail only in verbose
UI.detail(f"Reclaimed volume ownership across {len(paths)} directories")
for path in paths:
    UI.debug(f"  {path}")
```

### Do not call `trace()` manually

`UI.trace()` is called automatically by `UI._print()`. All output goes to `~/.ldm/last-command.log` regardless of level.

---

## Phase Headers

Multi-step commands must use phase headers to give users orientation:

```python
# handlers/workspace.py - quickstart
UI.phase(1, 5, "Downloading")
# ... download work ...

UI.phase(2, 5, "Restoring")
# ... restore work ...
```

Commands that do NOT need phase headers (single-purpose):
`ldm logs`, `ldm status`, `ldm config`, `ldm snapshots`, `ldm stop`, `ldm nuke`

---

## Completion Banners

Any command that involves a Liferay boot wait (`_wait_for_ready`) must emit a completion banner:

```text
Liferay is ready  (3m 42s)

  https://aica.local
  /Users/peterrichards/ldm/aica

  Next:  ldm logs aica        View live logs
         ldm snapshot aica    Take a backup
```

In `--non-interactive` mode, emit a single structured line:

```text
Liferay ready: https://aica.local  (3m 42s)
```

---

## Reviewing Log Calls

When reviewing PRs, flag any log call that:

1. Uses `UI.warning()` for an expected/routine operation
2. Uses `UI.info()` for a sub-step that is not a top-level phase milestone
3. Emits O(n) lines where n is unbounded (batching required)
4. Uses `UI.success()` for a trivial internal confirmation
5. Duplicates a message that was already emitted in the same command run
6. Appears inside a tight loop without a batching guard

---

## Related Issues

- [#754 — Surface `--info` flag as documented CLI option](https://github.com/peterrichards-lr/liferay-docker-manager/issues/754)
- [#755 — Reclassify ~200 UI.info() calls to detail()/debug()](https://github.com/peterrichards-lr/liferay-docker-manager/issues/755)
- [#756 — Restore UI.warning() semantic integrity](https://github.com/peterrichards-lr/liferay-docker-manager/issues/756)
- [#757 — Add phase headers to multi-step commands](https://github.com/peterrichards-lr/liferay-docker-manager/issues/757)
- [#758 — Add completion banner to quickstart and run](https://github.com/peterrichards-lr/liferay-docker-manager/issues/758)

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-22* | *Last Reviewed: 2026-07-22*
