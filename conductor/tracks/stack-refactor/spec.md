# Specification: StackHandler Modularization

## 1. Architectural Boundaries

The existing `StackHandler` (mixin) will be decomposed into three primary specialized handlers.

### A. `StackComposer` (`ldm_core/handlers/composer.py`)

**Responsibility**: Pure logic for generating the `docker-compose.yml` and translating metadata into infrastructure.

- **Key Methods**:
  - `write_docker_compose(paths, meta, liferay_env)`
  - `get_default_jvm_args()`
  - `_is_ssl_active(host_name, meta)`
  - `_calculate_mysql_flags(tag)`

### B. `StackRuntime` (`ldm_core/handlers/runtime.py`)

**Responsibility**: Logic for container lifecycle management and health monitoring.

- **Key Methods**:
  - `sync_stack(...)` (Main entry point)
  - `_wait_for_ready(...)`
  - `_ensure_network()`
  - `setup_infrastructure(...)`
  - `cmd_logs(...)`, `cmd_stop(...)`, etc.

### C. `AssetManager` (`ldm_core/handlers/assets.py`)

**Responsibility**: The "Offline First" engine for seeds and samples.

- **Key Methods**:
  - `_fetch_seed(...)`
  - `_ensure_seeded(...)`
  - `download_samples(...)`

## 2. Shared Registry Context

All handlers will share access to `_pre_flight_checks` and `find_dxp_roots` via the `BaseHandler` or a new `RegistryHandler` if needed.

## 3. Redline Preservation

The refactoring MUST NOT change the following behavior:

1. **DB in Properties**: JDBC settings remain in `portal-ext.properties`.
2. **Search in Env**: Search settings remain in Environment Variables.
3. **Cache Priority**: Offline-First discovery logic must be preserved exactly.
