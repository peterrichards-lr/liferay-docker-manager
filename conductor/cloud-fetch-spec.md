# Feature Specification: `ldm cloud-fetch`

## 1. Objective

Automate the retrieval of Liferay Cloud (LCP) backups, logs, and configuration to streamline local debugging and synchronization.

## 2. Technical Requirements

- **LCP CLI**: The `lcp` command must be present in the system PATH and authenticated (`lcp login`).
- **Project Mapping**: LDM projects must store a `cloud_project_id` in their metadata (defaulting to the folder name or project ID).

## 3. Command Syntax & Options

### Discovery

- `ldm cloud-fetch --list-envs`: Lists all environments associated with the project.
- `ldm cloud-fetch <env> --list-backups`: Lists available database and volume snapshots.

### Synchronization

- `ldm cloud-fetch <env> --download`: Downloads the latest database and volume backups into the project's `snapshots/` directory.
- `ldm cloud-fetch <env> --restore`: Downloads and immediately triggers a local restoration (replaces current local data).
- `ldm cloud-fetch <env> --sync-env`: Fetches remote environment variables and updates `.liferay-docker.meta` (merging with existing `custom_env`).

### Monitoring

- `ldm cloud-fetch <env> <service> --logs`: Streams logs from the remote service (e.g., `liferay`, `webserver`, `database`) to the local terminal.

## 4. Implementation Details

### Handler: `ldm_core/handlers/cloud.py`

- `_run_lcp_cmd(args, capture_json=True)`: Utility to execute `lcp` commands and handle output.
- `cmd_cloud_fetch(env, service=None)`: Main entry point for the command.

### CLI Updates (`ldm_core/cli.py`)

- Add `cloud-fetch` subparser.
- Arguments:
  - `env`: Positional environment ID.
  - `service`: Optional positional service name.
  - `--list-envs`, `--list-backups`: Flags for discovery.
  - `--download`, `--restore`: Flags for data sync.
  - `--sync-env`: Flag for variable sync.
  - `--logs`: Flag for remote logging.

## 5. Security & Safety

- **No Credentials**: LDM will **not** store LCP passwords or tokens. It relies entirely on the host's existing `lcp` authentication session.
- **Backup Verification**: Verify checksums of downloaded archives before attempting restoration.
- **Atomic Sync**: Use temporary files for metadata merging to prevent corruption.
