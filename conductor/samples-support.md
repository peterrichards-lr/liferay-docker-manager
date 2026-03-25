# Implementation Plan: Samples Support (`--samples` switch)

## Objective

Implement the `--samples` switch for the `ldm run` command to allow users to initialize a new Liferay instance with pre-configured Client Extension examples.

## Key Files & Context

- `ldm_core/cli.py`: Add the `--samples` flag to the `run` subparser.
- `ldm_core/handlers/stack.py`: Modify `cmd_run` to detect the `--samples` flag and trigger the synchronization of sample assets.
- `ldm_core/handlers/config.py`: Add logic to synchronize sample client extensions and deployable artifacts.

## Implementation Steps

### 1. CLI Update

- In `ldm_core/cli.py`, add `run.add_argument("--samples", action="store_true")` to the `run` parser.

### 2. Handler Logic: `cmd_run` Enhancement

- In `ldm_core/handlers/stack.py`, update `cmd_run` to check `getattr(self.args, "samples", False)`.
- If `True`, and the project is being initialized (or re-initialized), call a new method to sync sample assets.

### 3. Asset Synchronization: `sync_samples`

- Add `sync_samples(self, paths)` to `ConfigHandler` or a similar mixin.
- This method will:
  - Locate the global `samples/` directory (relative to `SCRIPT_DIR`).
  - Copy all directories from `samples/client-extensions/` to the project's `client-extensions/` (`paths['ce_dir']`).
  - Copy all files from `samples/deploy/` to the project's `deploy/` (`paths['deploy']`).
  - (Optional) Copy a pre-configured snapshot from `samples/snapshots/` to `paths['backups']`.

### 4. Integration with `sync_stack`

- Ensure that `sync_stack` (which is called by `cmd_run`) processes these newly added sample assets correctly.
- Since `sync_stack` already calls `scan_client_extensions`, it should automatically pick up any sample extensions copied into `paths['ce_dir']`.

## Verification & Testing

1. Run `ldm run my-sample-project --samples`.
2. Verify that the `client-extensions/` folder in `my-sample-project` contains the sample extensions.
3. Verify that Liferay starts up and correctly identifies the sample extensions (check logs or Traefik routes).
4. Verify that running `ldm run` without `--samples` does not copy any sample assets.
