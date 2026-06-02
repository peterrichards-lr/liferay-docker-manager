# Implementation Plan: OSGi Performance & State Persistence

## 1. Objective

Reduce subsequent Liferay startup times by optionally persisting the OSGi bundle state and pre-computing search indexes.

## 2. Key Requirements

- **OSGi State Persistence**: Allow the `osgi/state` directory to survive container restarts (currently wiped or handled by Docker volumes).
- **Persistent Search Volumes**: Optionally use persistent volumes for global search to avoid full re-indexing.
- **State Invalidation**: Implement smart invalidation of the OSGi state if the underlying Liferay image or Client Extensions change.

## 3. Technical Design

### State Persistence Logic (`ldm_core/handlers/stack.py`)

- Introduce a `--persist-osgi` flag for `ldm run`.
- When enabled, map a local project subfolder (e.g., `.ldm/osgi-state/`) to `/opt/liferay/osgi/state` in the container.
- Implement a "safety check" that compares the current Liferay tag with the tag stored in the persisted state. If they mismatch, wipe the state.

### CLI Update (`ldm_core/cli.py`)

- Add `--persist-osgi` to `run` and `import` subparsers.
- Add `--no-persist-osgi` to force a clean OSGi state.

### Search Index Persistence

- Update the default `infra-compose.yml` to support persistent volumes for Elasticsearch.
- Map search indexes to a project-specific volume to ensure data durability across `ldm down` / `ldm run` cycles.

## 4. Implementation Steps

1. **Step 1: Local State Mapping**: Update `StackHandler` to include a persistent mount for `osgi/state` when requested.
2. **Step 2: Metadata Synchronization**: Store the Liferay version and CE hash in the state folder to track invalidation.
3. **Step 3: Invalidation Logic**: Add a pre-flight check in `sync_stack` to verify the integrity of the persisted OSGi state.
4. **Step 4: Search Volume Updates**: Modify the `docker-compose` template to use named volumes for Elasticsearch data.

## 5. Verification & Testing

1. Start a project with `--persist-osgi`.
2. Wait for full Liferay initialization (check logs for "Started in X ms").
3. Restart the project and verify that the "bundle resolution" phase is skipped or significantly faster.
4. Upgrade the project tag and verify that the OSGi state is correctly invalidated and recreated.
5. Verify that search data persists across project restarts.
