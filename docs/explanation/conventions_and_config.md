# LDM Conventions & Configuration Architecture

> [!NOTE]
> This guide details the 3-tier configuration hierarchy, default infrastructure parameters, out-of-the-box port routing, and volume storage conventions enforced by Liferay Docker Manager (LDM).

---

## 🎛️ 1. The 3 Configuration Precedence Levels

LDM evaluates settings using a strict 3-tier precedence hierarchy. Higher levels override lower levels without exception:

```text
┌─────────────────────────────────────────────────────────────┐
│ 1. CLI Flags & Runtime Arguments (Highest Precedence)       │
│    e.g. ldm run --port 9090 --db postgresql                 │
├─────────────────────────────────────────────────────────────┤
│ 2. Project Local Metadata (.liferay-docker.meta)            │
│    e.g. ldm config set port 8080 (Scoped to current workspace)│
├─────────────────────────────────────────────────────────────┤
│ 3. Global User Configuration (~/.liferay-docker/config.json)│
│    e.g. ldm config set --global default_db postgresql       │
└─────────────────────────────────────────────────────────────┘
```

1. **CLI Flags & Arguments (Level 1)**: Command-line parameters passed directly to commands (e.g. `ldm run --port 9090`) take precedence over all stored configurations for that single invocation.
2. **Project Local Metadata (Level 2)**: Stored in `.liferay-docker.meta` inside the project root directory. Manages workspace-specific ports, database types, search modes, and attached client extension mappings.
3. **Global User Configuration (Level 3)**: Stored in `~/.liferay-docker/config.json`. Defines global developer defaults (e.g. preferred default database, telemetry preferences, ngrok/lfr-tunnel tokens).

---

## 🏗️ 2. Default Infrastructure & Ports

When running `ldm run` without overriding flags, LDM provisions the following out-of-the-box stack:

| Component | Default Convention | Host Port | Description |
| :--- | :--- | :--- | :--- |
| **Liferay Version** | Latest **LTS** | N/A | Automatically resolves and pulls the latest verified LTS container tag. |
| **Database** | **PostgreSQL** | `5432` | Shared Global Infrastructure container (`liferay-db-global`), reducing memory consumption across projects. |
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
*Last Updated: 2026-08-18* | *Last Reviewed: 2026-08-18*
