# Liferay Cloud PaaS Commands (`ldm cloud`)

Commands for managing deployment and operations on Liferay Cloud PaaS environments.

## Subcommands

### `ldm cloud deploy`

Deploys a workspace project to a Liferay Cloud PaaS environment.

```bash
ldm cloud deploy <project> -e <env> [--apply] [--commit <sha>] [--force]
```

Options:

- `-e, --environment, --env`: Target environment ID (e.g. `dev`, `uat`, `prd`).
- `-d, --direct`: Fast-path direct deployment via `lcp deploy`.
- `-g, --git`: Force Git-driven push deployment.
- `-s, --service`: Target service name (default: `liferay`).
- `--no-wait`: Asynchronous deployment initiation without build log tailing.
- `--apply`: Automatically apply changes.
- `--commit`: Specify Git commit SHA to deploy.
- `--force`: Force deployment without interactive prompt.

### `ldm cloud update-tags`

Updates service image tag references in `LCP.json`.

```bash
ldm cloud update-tags <project> -e <env>
```

### `ldm cloud sql`

Executes a SQL script against a Liferay Cloud database.

```bash
ldm cloud sql <project> -e <env> -f <file.sql> [--force]
```

### `ldm cloud db-reset`

Resets the public schema on a Liferay Cloud database.

```bash
ldm cloud db-reset <project> -e <env> [--override] [--override-production-safety-lock]
```

Options:

- `--override, --override-production-safety-lock`: Required override to reset production database schema.

### `ldm cloud status`

Queries status for a Liferay Cloud project and environment.

```bash
ldm cloud status <project> -e <env>
```

### `ldm cloud logs`

Streams logs for a Liferay Cloud service.

```bash
ldm cloud logs <project> -s <service> [-f]
```

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-19* | *Last Reviewed: 2026-08-19*
