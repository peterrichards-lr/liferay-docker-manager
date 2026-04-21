# Feature Specification: Hardness & UX Refinements (v1.7.x)

## 1. Objective

Improve the overall stability, reliability, and user productivity of LDM through targeted hardening of core logic and streamlining common workflows.

## 2. Hardness Improvements (Robustness & Integrity)

### 2.1 Pre-Flight Port Conflict Detection

- **Issue**: Starting a stack when host ports (8080, 443, 5432) are already in use leads to confusing Docker Compose errors.
- **Solution**: Implement a pre-check in `StackHandler` that probes required host ports before execution and provides a clear "Port X is occupied by [Process]" error.

### 2.2 Metadata Schema Validation

- **Issue**: Manual editing of `.liferay-docker.meta` can introduce syntax errors or missing keys that crash the orchestrator.
- **Solution**: Define a JSON schema for project metadata and validate the file during `read_meta`. Report specific line/key errors to the user.

### 2.3 Atomic Configuration Writes

- **Issue**: Interrupting the tool (CTRL+C) during a metadata or property write can result in a corrupted/empty file.
- **Solution**: Implement a "Safe Write" pattern across all handlers: write to `.tmp` file, sync to disk, and then rename (atomically) to the target path.

### 2.4 Dependency Version Locking

- **Issue**: Loose dependency ranges in `requirements.txt` can lead to "works on my machine" issues if sub-dependencies change.
- **Solution**: Transition to a locked `requirements.txt` generated from a `requirements.in` using `pip-compile` or similar, ensuring identical environments for all users.

## 3. UX Improvements (Workflow & Productivity)

### 3.1 Fuzzy Project Selection

- **Issue**: Standard numbered lists become cumbersome when managing 20+ projects.
- **Solution**: Integrate a lightweight fuzzy-search filter into `select_project_interactively` allowing users to type characters to narrow down the list.

### 3.2 Interactive Configuration Editor (`ldm edit`)

- **Issue**: Locating and opening hidden metadata files manually is slow.
- **Solution**: Add `ldm edit [project]` which automatically opens the project's `.meta` file or `portal-ext.properties` in the user's preferred editor (honoring `$EDITOR` or falling back to `vi/notepad`).

### 3.3 Multi-Service Log Tailing

- **Issue**: Users often need to see logs from both Liferay and a Client Extension simultaneously.
- **Solution**: Update `cmd_logs` to accept multiple service names (e.g., `ldm logs -f liferay my-microservice`).

### 3.4 Non-Blocking Update Checks

- **Issue**: Checking for updates on every command adds ~500ms of latency to the startup.
- **Solution**: Detach the update check into a background thread/process. If an update is found, notify the user at the *end* of the current command execution rather than at the beginning.

| Feature | Status | Release |
| :--- | :--- | :--- |
| Pre-Flight Port Detection (with IP isolation) | ✅ Completed | v2.3.9 |
| Metadata Schema Validation | ✅ Completed | v2.3.11 |
| Atomic Configuration Writes | ✅ Completed | v2.3.11 |
| Dependency Version Locking | ✅ Completed | v2.3.6 |
| Fuzzy Project Selection | ✅ Completed | v2.3.11 |
| Interactive Config Editor (`ldm edit`) | ✅ Completed | v2.3.6 |
| Multi-Service Log Tailing | ✅ Completed | v2.3.10 |
| Non-Blocking Update Checks | ✅ Completed | v2.3.7 |
