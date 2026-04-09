# Liferay Docker Scripts - Project Context

## Project Overview

This repository contains automation tools for managing Liferay DXP instances using Docker. It provides a Python-based manager (`ldm`) with shell and batch wrappers, designed for **high-velocity evaluation and demonstration**.

## Core Mandates & Logic "Source of Truth"

- **Logic Authority**: `ldm_core` package is the primary source of business logic.
- **Platform Parity**: Full support for macOS (Silicon/Intel), Windows (Native/WSL2), and Linux (Fedora/Ubuntu).
- **Documentation Stewardship**: All documentation (README, TESTING, etc.) MUST be updated within the same commit as code changes to ensure the project state remains 100% accurate.
- **Idempotency**: All commands must handle existing resources gracefully.
- **Atomic Metadata**: Updates to `.liferay-docker.meta` must be atomic (using temp files and `os.replace`) to prevent corruption.

## Engineering Standards

### 1. The "Modern-then-Legacy" Pattern

- **Docker Compose**: Prefer `docker compose` (v2 plugin). On legacy Intel Macs (Monterey and older), automatically fall back to `docker-compose` (v1 standalone).
- **Fail-Fast Discovery**: LDM MUST verify that the discovered Compose command is fully functional (via `version` inspection) before returning it. If no working Compose is found, handlers must abort immediately with a helpful installation hint instead of attempting to run malformed commands.
- **Docker Socket**: Discover active endpoints via `docker context inspect`. Prefer `/run/docker.sock` (modern Linux/WSL) over legacy `/var/run/docker.sock`.

### 2. Permission & Mount Hardening

- **Strict Mounts**: Tolerates restricted `chown/chmod` on macOS/sshfs and WSL mounts as long as the directory is verified functional via a token check (`.ldm_mount_check`).
- **macOS Socket Bridge**: Uses an `alpine/socat` sidecar to bridge the home-dir socket (`~/.docker/run/docker.sock`) to a TCP endpoint for Traefik.
- **Ghost Mount Prevention**: Proactively creates directory structures (`data/`, `deploy/`, `osgi/state/`) before bind-mounting to prevent Docker from creating them as `root`-owned folders.

### 3. Client Extension (CX) Lifecycle

Every artifact imported from a workspace MUST follow this atomic 3-step sequence:

1. **Copy**: To project root `client-extensions/` (The Build Source of Truth).
2. **Expand**: Unzip into a subfolder to provide a valid Docker build context.
3. **Move**: The original ZIP to `osgi/client-extensions/` for Liferay's auto-deployer.

### 4. Implementation Details

- **LCP.json Defaults**: Default to **1 CPU** and **512MB Memory** if resource limits are absent.
- **Common Asset Sync**: Uses a `.liferay-docker.deployed` tracking file to ensure global assets (configs, LPKGs) are only deployed once.
- **Robust Healthchecks**: Use multi-tool probes (`curl`, then `wget`, then `nc`) for extension containers.
- **Zsh Environment**: Scripts must follow `#!/bin/zsh` to support advanced array expansions like `${(z)status}`.
- **Traefik v3 Labels**: Always use escaped backticks (e.g. `Host(\`${HOST_NAME}\`)`) to prevent shell interpretation.

## Troubleshooting Patterns

- **WSL2 SSL Bridge**: Copy `rootCA.pem` from WSL's `mkcert -CAROOT` to Windows and run `mkcert -install` in PowerShell to gain host-side trust.
- **Windows File Locking**: Use a sidecar `.bat` script during `ldm upgrade` to wait for process exit before replacing the binary.
- **Docker API Version**: Set `DOCKER_API_VERSION=1.44` for sidecars to ensure compatibility with modern (v29+) Docker engines.
- **Python hash() randomization**: Always use `hashlib.md5()` for configuration signatures to prevent unnecessary container restarts.

## Current State (April 9, 2026)

- **Version 1.6.9 Automation & Stability Release**: COMPLETED. Enforced mandatory Docker Compose v2 (Plugin), implemented Fail-Fast discovery, and finalized pipeline-ready exit codes and non-interactive support.
- **Version 1.6.8 Nuclear Isolation Release**: COMPLETED. Definitively resolved legacy Docker Compose URI scheme crashes on Intel Macs by forcing an empty `DOCKER_CONTEXT` and raw Unix sockets.
- **Version 1.6.7 Total Isolation Release**: COMPLETED. Refined legacy shield logic to isolate standalone binaries from modern Docker contexts.

## Hardening History (Version Log)

- **v1.6.6**: IMPLEMENTED Fail-Fast discovery, non-interactive support for all commands, and dictionary-based environment generation to ensure uniqueness.
- **v1.6.5**: FIXED Docker Compose misidentification on Intel Macs by implementing an architecture-based exception for `x86_64`.
- **v1.6.4**: ADDED `ldm upgrade --repair` mode to allow one-click fixing of `TAMPERED / MISMATCH` integrity errors.
- **v1.6.2**: ADDED proactive permission checks to `upgrade` to guide users on using `sudo` or Admin rights before download.
- **v1.6.0**: IMPLEMENTED secure self-upgrade mechanism with SHA-256 verification and atomic swaps.
- **v1.5.9**: ADDED `ldm renew-ssl` for project-specific certificate refreshing.
- **v1.5.8**: IMPLEMENTED "Modern-then-Legacy" Compose discovery and 15-minute startup timeouts.
- **v1.5.7**: FIXED `TypeError` in global search setup and hardened provider detection for Native Linux.
- **v1.5.6**: FIXED "UNC path" warnings when launching host browser from WSL terminal.
- **v1.5.5**: FIXED semantic version comparison logic using numeric tuples instead of strings.
- **v1.5.0**: SIGNIFICANT hardening for non-proprietary environments (Colima, WSL2). Implemented `PollingObserver` for macOS file descriptor limits.

## Roadmap: v2.0.0 (The Ecosystem Phase)

- **Guided Scaffolding**: Scenario-based project templates.
- **Visual Dashboard**: Read-only web UI (`http://localhost:19000`) for stack health monitoring.
- **High-Velocity Boot**: Pre-seeded database snapshots to reduce first-run wait to <2 minutes.
- **AI-Assisted Orchestration**: Gemini-powered `ldm ai` command for real-time CLI troubleshooting and configuration generation.
