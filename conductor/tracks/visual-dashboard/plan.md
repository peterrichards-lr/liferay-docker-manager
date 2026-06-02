# Implementation Plan: Visual Health Dashboard

## 1. Objective

Provide a lightweight, read-only web interface accessible via `http://localhost:19000` to visualize the health and configuration of all managed Liferay stacks.

## 2. Key Requirements

- **Local Monitoring UI**: Display a grid of all LDM projects with status (Up/Down/Starting).
- **Container Health**: Real-time SVG indicators for container CPU, Memory, and Health status.
- **Route Mapping**: List all active Traefik routes and their destination containers.
- **Log Streamer**: Simple, read-only log view for each container.
- **Zero Configuration**: Automatically discovers all projects without manual setup.

## 3. Technical Design

### Dashboard Backend (`ldm_core/dashboard/server.py`)

- Use a lightweight Python web framework (e.g., `FastAPI` or `Flask`).
- Implement a background poller that queries `docker stats` and `docker inspect` for all projects.
- Serve a single-page application (SPA) from the LDM installation directory.

### CLI Update (`ldm_core/cli.py`)

- Add `ldm dashboard` command.
- Arguments:
  - `--port`: Override default port 19000.
  - `--host`: Bind to a specific address (default: 127.0.0.1).
  - `--background`: Run the dashboard in a detached process.

### UI Frontend (`ldm_core/resources/dashboard/`)

- Vanilla JS or a minimal framework like `Alpine.js`.
- Use `Tailwind CSS` for styling (pre-compiled).
- Use `WebSockets` (if supported by the framework) for real-time status updates.

## 4. Implementation Steps

1. **Step 1: Backend Server**: Implement the basic web server in `ldm_core/dashboard/server.py`.
2. **Step 2: API Discovery**: Add endpoints to list all LDM projects and their status.
3. **Step 3: UI Design**: Create the main health grid using SVG icons.
4. **Step 4: Route Visualization**: Integrate with Traefik's internal API (if available) or parse Traefik labels from Docker.
5. **Step 5: Background Execution**: Add support for running the dashboard as a background daemon.

## 5. Verification & Testing

1. Run `ldm dashboard`.
2. Access `http://localhost:19000` in a browser.
3. Start an LDM project and verify its status updates from "Down" to "Starting" and then "Up."
4. Stop a project and verify its status update.
5. Check route information for correctness.
