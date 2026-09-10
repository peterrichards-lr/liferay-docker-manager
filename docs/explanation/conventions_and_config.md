# LDM Conventions & Configuration Architecture

> [!NOTE]
> This guide details the 3-tier configuration hierarchy, default infrastructure parameters, out-of-the-box port routing, and volume storage conventions enforced by Liferay Docker Manager (LDM).

---

## 🎛️ 1. Configuration Precedence Levels

LDM evaluates settings using a strict precedence hierarchy. Higher levels override lower levels without exception:

```text
┌─────────────────────────────────────────────────────────────┐
│ 1. CLI Flags & Runtime Arguments (Highest Precedence)       │
│    e.g. ldm run --port 9090 --db postgresql                 │
├─────────────────────────────────────────────────────────────┤
│ 2. Project Local Metadata (<project>/meta)                  │
│    written by ldm run/up; scoped to that one project        │
├─────────────────────────────────────────────────────────────┤
│ 3. User Defaults (~/.ldmrc, "defaults" block)               │
│    e.g. ldm defaults port 9090                              │
├─────────────────────────────────────────────────────────────┤
│ 4. Machine Defaults (/etc/ldmrc, "defaults" block)          │
│    e.g. ldm defaults db_type postgresql --global            │
├─────────────────────────────────────────────────────────────┤
│ 5. Convention Defaults (in LDM itself; not a file)          │
└─────────────────────────────────────────────────────────────┘
```

1. **CLI Flags & Arguments (Level 1)**: Command-line parameters passed directly to commands (e.g. `ldm run --port 9090`) take precedence over all stored configuration. They are not merely for one invocation: `ldm run` persists what it resolved into the project's `meta`, so the value survives into later `ldm up` calls that omit the flag.
2. **Project Local Metadata (Level 2)**: Stored as `meta` inside the project root directory (the older `.liferay-docker.meta` and `.ldm.meta` names are still read). Manages project-specific ports, database engine and mode, search modes, and attached client extension mappings.
3. **User Defaults (Level 3)**: The `defaults` block of `~/.ldmrc`. Set with `ldm defaults <key> <value>`; view the whole resolved cascade, and where each value came from, with `ldm defaults`.
4. **Machine Defaults (Level 4)**: The `defaults` block of `/etc/ldmrc`, for shared workstation images and CI agents. Set with `ldm defaults <key> <value> --global`.
5. **Convention Defaults (Level 5)**: `CONVENTION_DEFAULTS` in `ldm_core/defaults.py`. Not a file and not editable — the values that apply when nobody has chosen one.

> [!IMPORTANT]
> **`ldm defaults <key>` and `ldm config set <key>` are not interchangeable** (LDM-#1651).
> `ldm config set` writes the *root* of `~/.ldmrc`, which is the right home for
> credentials and machine-local settings (`ngrok_authtoken`, `share_provider`).
> Cascading defaults are read from the `defaults` block, so a cascading key written to
> the root is invisible to every command that resolves it. LDM now refuses that write
> and names the command that works, rather than reporting a success with no effect.

Both files are plain JSON and can be inspected directly, but prefer the commands —
`ldm defaults` reports which level each effective value came from.

---

## 🏗️ 2. Default Infrastructure & Ports

When running `ldm run` without overriding flags, LDM provisions the following out-of-the-box stack:

| Component | Default Convention | Host Port | Description |
| :--- | :--- | :--- | :--- |
| **Liferay Version** | Latest **LTS** | N/A | Automatically resolves and pulls the latest verified LTS container tag. |
| **Database** | **PostgreSQL** | `5432` | Shared Global Infrastructure container (`liferay-db-global`, published on `5433`), reducing memory consumption across projects. MySQL/MariaDB shares its own global container (`liferay-db-mysql-global`, published on `3307`). |
| **Search Engine** | **Elasticsearch 8.x** | `9200` | Shared Global Search service. Isolated sidecars are supported via `--search-mode sidecar`. |
| **Reverse Proxy** | **Traefik** | `80`, `443` | Automated HTTP/HTTPS routing with `mkcert` zero-config SSL certificates. |
| **Default Hostname** | `localhost` / `<project>.local` | `8080` / `443` | Auto-resolved in `/etc/hosts` to loopback (`127.0.0.1`) or target node IP. |

---

## 💾 3. Hybrid Volume & Mount Strategy

LDM uses a hybrid storage architecture to maximize performance while preventing POSIX lock deadlocks:

* **Named Docker Volumes (POSIX Lock-Sensitive)**:
  Directories subject to heavy OSGi lock contention (`osgi/state`, `data`, `work`) are backed by high-performance named Docker volumes. This definitively prevents file-locking deadlocks on macOS (Colima/VirtioFS) and Windows (WSL2).
* **Bind Mounts (Hot-Reloading & Code Sync)**:
  Directories meant for developer iteration (`deploy/`, `modules/`, `client-extensions/`) are bind-mounted directly from the host workspace, enabling instant file synchronization and auto-deploy scanner triggers.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-09-10* | *Last Reviewed: 2026-09-10*
