# Liferay Docker Scripts - Project Context

## Project Overview

This repository contains automation tools for managing Liferay DXP instances using Docker. It provides a Python-based manager (`ldm`) with shell and batch wrappers, designed for **high-velocity evaluation and demonstration**.

## Project Intent

- **Sandbox Environment**: Designed for quickly standing up environments to evaluate Liferay, test new features, and build demonstrations.
- **Multi-Instance Isolation**: Run multiple instances side-by-side without session cross-talk via unique session cookies and SNI-based Traefik routing.
- **State Persistence**: Robust snapshot/restore for "gold-standard" demo states, including files, database, and Elasticsearch 8.x indices.
- **Not for Production**: LDM is strictly for sandbox and demonstration use.

## Core Mandates

- **Logic Source of Truth**: `ldm_core` package is the primary source of business logic.
- **Platform Parity**: Full support for macOS (Silicon/Intel), Windows (Native/WSL2), and Linux (Fedora/Ubuntu).
- **Idempotency**: All commands must handle existing resources gracefully.
- **Fail-Fast Design**: Proactive environment checking (Docker reachability, volume health, CPU/RAM thresholds) before execution.
- **Atomic Metadata**: Updates to `.liferay-docker.meta` must be atomic to prevent corruption.
- **SSL Lifecycle**: Automated HTTPS via global Traefik proxy and `mkcert`.
- **Documentation Stewardship**: All documentation (README, TESTING, etc.) MUST be reviewed and updated within the same commit as code changes.

## Engineering Standards

- **Liferay Versioning**: Adhere to 7.4+ tag formats (`YYYY.qX.N`).
- **File System Structure**:
  - `deploy/`, `data/`, `files/`: Primary bind mounts.
  - `osgi/state/`: **MUST be a Bind Mount** to allow LDM to clear cache to resolve issues.
  - `client-extensions/`: The absolute source of truth for Docker builds.
- **Modern-then-Legacy Pattern**:
  - **Docker Compose**: Always try `docker compose` (v2 plugin) first. If missing or broken (common on legacy Intel Macs), fall back to `docker-compose` (v1 standalone).
  - **Docker Socket**: Dynamically discover active socket via `docker context inspect`. Prefer `/run/docker.sock` (modern Linux/WSL) over legacy `/var/run/docker.sock`.
- **Permission Management**:
  - **Strict Mounts**: Tolerates restricted `chown/chmod` on macOS/sshfs and WSL mounts as long as the directory is verified functional via a token check.
  - **User Groups**: Native Linux/WSL require adding the user to the `docker` group to avoid `sudo` requirements.
- **UI Consistency**:
  - **Single Icon Rule**: Status strings must not contain emojis; the `UI` helper prepends them.
  - **Standard Spacing**: Use two-space padding after icons for professional visual breathing room.
  - **Unicode Safety**: Handle `UnicodeEncodeError` by falling back to ASCII for older Windows consoles (CP1252).

## Gotchas & Troubleshooting Patterns

### 1. macOS "Ghost Mounts"

- **Issue**: Colima/OrbStack sometimes create empty directories instead of mounting host files.
- **Fix**: LDM writes a token to the host and verifies it from an Alpine container before project startup.

### 2. WSL2 SSL Trust Bridge

- **Issue**: Windows browsers don't trust CA Roots generated inside WSL.
- **Fix**: Copy `rootCA.pem` from WSL's `mkcert -CAROOT` to Windows and run `mkcert -install` in PowerShell.

### 3. Traefik v3 SNI

- **Issue**: SNI matching fails if multiple wildcard certs are present.
- **Fix**: Explicitly define `tls.domains` in Traefik labels to provide clear hints for wildcard routing.

### 4. Windows File Locking

- **Issue**: Windows prevents replacing running executables during `ldm upgrade`.
- **Fix**: Use a sidecar `.bat` script that waits for LDM to exit, performs the swap, and then restarts the tool.

### 5. Docker Desktop "Split-Brain"

- **Issue**: Running both Docker Desktop and Native WSL Docker causes port 443 conflicts and IP mismatches.
- **Recommendation**: Standardize on **WSL Integration** (one engine) to ensure path and network consistency.

## Current State (April 9, 2026)

- **Version 1.6.5 Milestone**: COMPLETED.
  - **Universal Socket**: Dynamic discovery of active engine endpoints.
  - **Self-Upgrade**: `ldm upgrade` with SHA-256 integrity verification and `--repair` mode.
  - **Modern Compose**: Prefers v2 plugin with v1 fallback.
  - **Hardened UI**: Unicode-safe terminal output with standardized icon spacing.
  - **Verified Lab**: Full pass on M1 Pro, Intel Mac (Monterey), Windows 11 (WSL2), and Fedora 43.

## Roadmap: v2.0.0 (The Ecosystem Phase)

- **Guided Scaffolding**: Scenario-based project templates.
- **Visual Dashboard**: Read-only web UI for stack health monitoring.
- **High-Velocity Boot**: Pre-seeded database snapshots to reduce first-run wait to <2 minutes.
- **AI-Assisted Orchestration**: Gemini-powered `ldm ai` for real-time CLI troubleshooting.
- **Command Namespacing**: Grouping maintenance under `ldm system` and `ldm infra`.
