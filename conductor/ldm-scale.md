# Implementation Plan: Multi-Node Simulation (`ldm scale`)

## Objective

Implement the `ldm scale` command to allow users to scale Liferay and other services within a project. This supports multi-node simulation for load-balancing evaluation and clustered behavior testing.

## Key Files & Context

- `ldm_core/cli.py`: Define the new `scale` command and arguments.
- `ldm_core/manager.py`: Map the command to the handler.
- `ldm_core/handlers/stack.py`: Implement the scaling logic, including `docker-compose.yml` regeneration and scale-aware configuration.
- `ldm_core/handlers/diagnostics.py`: Update `cmd_list` to accurately report the status of scaled services.

## Implementation Steps

### 1. CLI Updates

- Add `scale` command to `subparsers`.
- Arguments:
  - `project`: Optional project identifier.
  - `service_scale`: One or more `service=count` pairs (e.g., `liferay=2`).

### 2. Metadata & Configuration

- Store scale factors in `.liferay-docker.meta` using the prefix `scale_` (e.g., `scale_liferay=2`).
- Update `BaseHandler.read_meta` and `BaseHandler.write_meta` if needed (they seem generic enough already).

### 3. Scale-Aware Compose Generation (`StackHandler.generate_compose`)

- Extract `scale_map` from the project metadata.
- For each service:
  - If `scale > 1`:
    - Omit `container_name` to allow Docker to generate unique names (e.g., `project-liferay-1`, `project-liferay-2`).
    - Disable host-mounting for `osgi/state` and `logs` to prevent file-locking conflicts between nodes.
    - (Optional) Set `deploy.replicas` in the compose file.
  - If `scale == 1`:
    - Keep `container_name` for backward compatibility and predictable naming.
- Ensure Traefik labels remain service-level so they apply to all instances.

### 4. Clustered Logic (Optional but recommended)

- If `scale_liferay > 1`, automatically inject clustering environment variables:
  - `LIFERAY_CLUSTER__LINK__ENABLED=true`
  - `LIFERAY_CLUSTER__LINK__AUTODETECT__ADDRESS=db:5432` (or similar reliable internal endpoint)
  - `LIFERAY_LUCENE__REPLICATE__WRITE=true` (if search is not external)

### 5. Implementation of `cmd_scale`

- Parse input arguments.
- Update metadata.
- Call `cmd_run` to regenerate `docker-compose.yml` and apply changes using `docker compose up -d --scale service=N`.

### 6. Diagnostics Update (`DiagnosticsHandler.cmd_list`)

- Instead of checking a single container status by name, use `docker compose ps` or check multiple containers if the service is scaled.

## Verification & Testing

1. Run `ldm scale myproject liferay=2`.
2. Verify that two Liferay containers are running (`docker ps`).
3. Verify that Traefik load-balances between them (check Traefik dashboard or logs).
4. Run `ldm scale myproject liferay=1` to scale back down.
5. Run `ldm list` and verify the status reflects the scaled service.
