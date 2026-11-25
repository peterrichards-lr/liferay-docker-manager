# Machine-Readable Output

Several `ldm` commands print a stable JSON object with `--json`, so scripts,
CI jobs and coding agents can read a result instead of scraping coloured text.
This page is the contract for those objects.

- **stdout carries only the JSON.** Progress, notices and prompts go to stderr.
- **Fields are added, never renamed or removed,** within a `schema_version`. A
  consumer should ignore fields it does not know.
- **Exit codes are unchanged by `--json`.** See [Exit Codes](exit_codes.md).

`ldm status --json` and `ldm list --json` are described with their commands in
[Core Commands](cli/core.md).

## `ldm info --json`

The project's addresses, locally and publicly. ![Added in v2.27.0](https://img.shields.io/badge/Added%20in-v2.27.0-blue)

```bash
ldm info my-project --json
```

```json
{
  "schema_version": 1,
  "project": "my-project",
  "path": "/Users/me/ldm/my-project",
  "id": "5f0c2d1e-...",
  "target": "local",
  "status": "running",
  "running": true,
  "host_name": "localhost",
  "port": 8080,
  "ssl": false,
  "url": "http://localhost:8080",
  "container": "my-project",
  "share": {
    "enabled": true,
    "provider": "lfr-tunnel",
    "subdomain": "peters",
    "domain": "lfr-demo.se",
    "public_urls": ["https://peters.lfr-demo.se"]
  },
  "extensions": [
    {
      "id": "my-service",
      "container": "my-project-my-service",
      "local_url": "http://my-service.localhost:58081",
      "public_url": "https://peters-my-service.lfr-demo.se"
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `url` | The address the project is reached at, derived exactly as the formatted `ldm info` derives it. |
| `status` | The Liferay container's Docker state (`running`, `exited`, ...), or `unknown` when Docker reports none. `running` is the same fact as a boolean. |
| `id` | The project's UUID, or `null` for a project created before UUIDs existed. |
| `share.public_urls` | The addresses a running tunnel reports. Empty when no tunnel answers, even if the project is configured to share. `share.domain` is `null` when the project is not shared. |
| `extensions[].public_url` | `null` unless the project is shared. |

Credentials are deliberately not included. `ldm info --credentials` is the
explicit way to ask for them, and `--json` is ignored when it is given.

## `ldm system doctor --json`

Every health check, with its result and the fix hints. ![Added in v2.27.0](https://img.shields.io/badge/Added%20in-v2.27.0-blue)

```bash
ldm doctor --json
ldm doctor --json --all
```

```json
{
  "schema_version": 1,
  "status": "warn",
  "summary": {"pass": 24, "warn": 1, "fail": 0, "total": 25},
  "subsystems": {"system": "pass", "docker": "warn", "project": "pass"},
  "checks": [
    {
      "name": "Disk Space",
      "subsystem": "docker",
      "status": "warn",
      "detail": "12GB reclaimable docker resources"
    }
  ],
  "hints": [
    {"text": "You have over 12GB of unused Docker resources. Run 'ldm prune' ...", "doc": null}
  ],
  "projects": ["/Users/me/ldm/my-project"]
}
```

| Field | Meaning |
|---|---|
| `status` | `fail` if any check failed, else `warn` if any warned, else `pass`. |
| `checks[].status` | `pass`, `warn` or `fail`. `checks` lists every check, including the ones that passed, so a missing check is genuinely missing. |
| `checks[].subsystem` | `system`, `docker` or `project`: the same grouping as the formatted summary. |
| `hints` | Fix hints for the run, in the order they were raised. Doctor records them per run, not per check, so they are not attached to a check. `doc` is a URL or `null`. |

Each check's `detail` and each hint's `text` pass through the same redaction as LDM's trace log, so a `KEY=value` secret such as `MYSQL_PASSWORD=...` appears as `MYSQL_PASSWORD=[REDACTED]`. The output is safe to paste into a ticket.

The exit code is the same as without `--json`: `0` when nothing failed
(warnings included), `1` otherwise.

`--json` cannot be combined with `--fix`, `--fix-hosts`, `--bundle`, `--slug`
or `--ssl`, which either print their own output or change the system; doctor
refuses the combination with exit code `1`. Run the check with `--json`, then
the remedy separately.

## `ldm share status --json`

The public share URL, and whether a tunnel is answering. ![Added in v2.27.0](https://img.shields.io/badge/Added%20in-v2.27.0-blue)

```bash
ldm share status my-project --json
```

```json
{
  "schema_version": 1,
  "project": "my-project",
  "provider": "lfr-tunnel",
  "active": true,
  "source": "live-api",
  "subdomain": "peters",
  "status": "healthy",
  "connection_state": "connected",
  "public_url": "https://peters.lfr-demo.se",
  "public_urls": ["https://peters.lfr-demo.se"],
  "inspector_url": "http://127.0.0.1:4040"
}
```

| Field | Meaning |
|---|---|
| `active` | A tunnel client answered. Its `status` and `connection_state` say whether it is connected. |
| `source` | Which client answered: `live-api` (the running client's local API), `docker-sidecar` (the `lfr-tunnel-docker` container) or `native-client` (the installed `lfr-tunnel`), or `null` when none did. |
| `status` | The client's own status, e.g. `healthy` or `disconnected`; `inactive` when no client answered. |
| `public_url` | The first of `public_urls`, or `null`. |
| `provider` | The project's recorded share provider, or `null` when it has none recorded. |

`share status --json` only reads. It never installs a tunnel client, prompts,
or starts anything, so it is safe to run from automation. An `ngrok` share is
reported with `active: false`, because ngrok has no status interface LDM reads.

## The privileged-step payload

When LDM needs root and cannot ask for a password -- under `-y` it only ever
tries `sudo -n` -- it exits with code `6` and names the command a person must
run. With `--json`, or with `LDM_JSON=1` in the environment for commands that
have no `--json` of their own (such as `ldm run`), that is also printed to
stdout as one JSON object per line:

```json
{"privileged_step": {"reason": "/etc/hosts needs entries for: acme.demo", "command": ["sudo", "sh", "-c", "printf '%s\\n' '# Added by Liferay Docker Manager (LDM)' '127.0.0.1 acme.demo # [LDM]' >> /etc/hosts"], "resume": "ldm run acme"}}
```

| Field | Meaning |
|---|---|
| `reason` | What needs root, in a sentence. |
| `command` | The exact command, as an argument list. Join it with shell quoting (`shlex.join` in Python) and it runs as-is; the human message on stderr prints it already joined. |
| `resume` | The `ldm` command to run once the step is done, or `null` when nothing else is needed. |

LDM never reads, accepts or stores a password on this path. Which commands can
return it, and when, is listed in [Exit Codes](exit_codes.md#code-6-a-privileged-step-is-required).

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-11-25* | *Last Reviewed: 2026-11-25*
