# Implementation Plan: Multi-OS E2E Matrix & JVM Optimization

This track focuses on enabling automated cross-platform verification of LDM by implementing a multi-OS GitHub Actions matrix and optimizing Liferay's JVM footprint to fit within CI resource limits.

## 1. Problem Statement

Currently, we rely on manual E2E verification reports for macOS, Windows, and specific Linux distros like Fedora. While we have an automated E2E suite for Ubuntu, the resource limits of GitHub-hosted runners (7GB RAM) and the lack of native Docker on non-Linux runners make full cross-platform automation challenging. Liferay's default JVM settings are too aggressive for these environments.

## 2. Proposed Solution

- **JVM Footprint Reduction**: Implement a `--lean` flag (or automatic CI detection) that reduces Liferay's heap and metaspace allocation to fit within 7GB total system RAM.
- **Comprehensive Cross-Platform Matrix**: Refactor `release-e2e.yml` to cover all verified workstation environments:
  - **Linux**: Ubuntu (Native) and Fedora (via Container Job).
  - **macOS**: Apple Silicon (`macos-14`) and Intel (`macos-13`) via Colima.
  - **Windows**: Docker Desktop / Linux mode.
- **Virtualization Setup**: Automate Colima for macOS and Docker Linux-mode for Windows.

## 3. Implementation Steps

### Phase 1: JVM Optimization (`--lean` mode) [✅ COMPLETED]

- **Status**: Implemented in v2.7.23, bugfixed in v2.7.24.
- Update `ComposerHandler.get_default_jvm_args` to support a "Lean" profile.
- **Lean Profile**: `-Xms1536m -Xmx2048m -XX:MaxMetaspaceSize=512m` (sufficient for basic smoke tests with shared search).
- Automatically trigger Lean mode if `GITHUB_ACTIONS=true` is detected on the host.

### Phase 2: GitHub Actions Matrix Refactor (Linux/Fedora) [✅ COMPLETED]

- **Status**: Dedicated `scheduled-verification.yml` workflow created. Phase 2 (Fedora via explicit docker run) implemented.
- Refactor `.github/workflows/release-e2e.yml` (or create new) to use an expanded `strategy.matrix`:
  - `os`: `[ubuntu-latest, macos-13, macos-14, windows-latest]`
  - `distro`: `[ubuntu, fedora]` (for Linux runners).
- Update reporting logic to include `matrix.os` and `matrix.distro` in artifact names.

### Phase 3: macOS Environment (Apple Silicon & Intel) [⚠️ LOCAL ONLY]

- **Status**: E2E scripts are fully compatible. However, automated GitHub Actions execution was backed out because the macOS runners do not support nested virtualization reliably enough for Colima. This remains a local-only automated verification.
- Implement setup steps for macOS runners to install `colima` and `docker` via Homebrew.
- Initialize Colima with CI-optimized resource limits (`colima start --cpu 2 --memory 6`).
- Verify binary execution (`shiv` for macOS) and Traefik routing.

### Phase 4: Fedora Environment (Container Jobs) [✅ COMPLETED]

- Implement a specific job that runs inside a `fedora:latest` container on an Ubuntu host.
- Mount `/var/run/docker.sock` to enable LDM to orchestrate containers from within Fedora.
- Verify LDM's path and permission handling on the Fedora filesystem.

### Phase 5: Windows Environment (WSL2/Linux Mode) [⚠️ LOCAL ONLY]

- **Status**: E2E PowerShell script is fully implemented. However, automated GitHub Actions execution was backed out because the Windows Server runners (windows-latest) do not natively support Linux Containers (LCOW) without complex Hyper-V / WSL2 nested setups that frequently fail. This remains a local-only automated verification.
- Configure Windows runners to switch Docker to Linux Container mode.
- Ensure the LDM binary (built with PyInstaller) correctly interacts with the Docker daemon.

### Phase 6: Reporting & Documentation

- Update `scripts/sync_compatibility.py` to ingest and parse reports from all matrix permutations.
- Update `docs/TESTING.md` to reflect that all platforms are now verified automatically.

## 4. Definition of Done

- `LDM Release E2E` workflow successfully passes on Ubuntu, Fedora, macOS (Silicon), macOS (Intel), and Windows.
- `ldm run --lean` (or CI auto-detect) allows Liferay to boot within 7GB RAM without OOM kills.
- Compatibility table in `README.md` is updated automatically from multi-OS CI results.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-21* | *Last Reviewed: 2026-07-02*
