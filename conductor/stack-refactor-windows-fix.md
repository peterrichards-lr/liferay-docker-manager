# LDM Diagnostics and Windows Bug Fixes Plan

## Objective

Address three specific issues reported during testing:

1. **ES Startup Hang**: The Elasticsearch wait loop hangs for 5 minutes if the container crashes.
2. **Windows YAML Escape Error**: Docker Compose config fails on Windows because volume paths contain backslashes that trigger YAML hex sequence escape errors.
3. **Windows Unicode Crash**: Direct `print()` calls in `cmd_status` with the `●` character bypass the `UI` module's Unicode safety fallback, crashing on Windows CMD/PowerShell.

## Key Files & Context

- `ldm_core/handlers/infra.py`: Contains the Elasticsearch wait loop.
- `ldm_core/handlers/runtime.py`: Contains the Liferay wait loop and `cmd_status`.
- `ldm_core/handlers/composer.py`: Generates the `docker-compose.yml` volumes.

## Implementation Steps

### 1. Fix ES Wait Loops (Early Exit)

**File**: `ldm_core/handlers/infra.py`

- In `setup_global_search`, enhance the wait loop to check the container's status using `self.get_container_status(search_name)`.
- If the status is `exited` or `dead`, break out of the loop immediately rather than sleeping for the full timeout. This will trigger the existing auto-repair logic instantly.

### 2. Fix Liferay Wait Loop (Early Exit)

**File**: `ldm_core/handlers/runtime.py`

- In `_wait_for_ready`, enhance the wait loop to check if the Liferay container has exited.
- If it exits prematurely, output a fatal error and return `False` immediately.

### 3. Fix Windows YAML Volume Paths

**File**: `ldm_core/handlers/composer.py`

- In `write_docker_compose`, update the `volumes` list for the Liferay service to use `.as_posix()` on all `Path` objects. This forces forward slashes (`/`) even on Windows, which Docker Compose handles perfectly and avoids all YAML escape sequence errors.

### 4. Fix Windows Unicode Crash

**File**: `ldm_core/handlers/runtime.py`

- In `cmd_status`, change the hardcoded `●` bullet points to use the `UI.raw()` method, which safely handles Unicode encoding errors by falling back to ASCII equivalents, OR change them to standard ASCII `*` directly in the string. (Using `UI.raw` is preferred to maintain the nice UI on supported terminals).

## Verification

- Running `ldm infra-setup --search` when ES is destined to crash (e.g. bad permissions) should fail fast and attempt repair.
- Running `ldm run` on Windows Native should successfully generate and validate the `docker-compose.yml` file.
- Running `ldm status` on Windows Native should complete without a `UnicodeEncodeError`.
