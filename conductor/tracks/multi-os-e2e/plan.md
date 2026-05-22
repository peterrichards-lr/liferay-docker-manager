# Implementation Plan: Multi-OS E2E Matrix & JVM Optimization

This track focuses on enabling automated cross-platform verification of LDM by implementing a multi-OS GitHub Actions matrix and optimizing Liferay's JVM footprint to fit within CI resource limits.

## 1. Problem Statement

Currently, we rely on manual E2E verification reports for macOS and Windows. While we have an automated E2E suite for Linux, the resource limits of GitHub-hosted runners (7GB RAM) and the lack of native Docker on non-Linux runners make cross-platform automation challenging. Liferay's default JVM settings are too aggressive for these environments.

## 2. Proposed Solution

- **JVM Footprint Reduction**: Implement a `--lean` flag (or automatic CI detection) that reduces Liferay's heap and metaspace allocation to fit within 7GB total system RAM.
- **Cross-Platform Matrix**: Refactor `release-e2e.yml` to run the verification suite on `ubuntu-latest`, `macos-latest`, and `windows-latest`.
- **Virtualization Setup**: Use Colima for macOS and configure Docker Linux mode for Windows runners.

## 3. Implementation Steps

### Phase 1: JVM Optimization (`--lean` mode)

- Update `ComposerHandler.get_default_jvm_args` to support a "Lean" profile.
- **Lean Profile**: `-Xms1536m -Xmx2g -XX:MaxMetaspaceSize=512m` (sufficient for basic smoke tests with shared search).
- Automatically trigger Lean mode if `GITHUB_ACTIONS=true` is detected on the host.

### Phase 2: GitHub Actions Matrix Refactor

- Refactor `.github/workflows/release-e2e.yml` to use `strategy.matrix`.
- Update reporting logic to include the `matrix.os` in the artifact names to prevent collisions.

### Phase 3: macOS Environment (Colima)

- Add a setup step for macOS runners to install `colima` and `docker` via Homebrew.
- Initialize Colima with specific resource limits (`colima start --cpu 2 --memory 6`).
- Verify binary execution and Traefik routing in the virtualized environment.

### Phase 4: Windows Environment (WSL2/Linux Mode)

- Configure Windows runners to switch Docker to Linux Container mode.
- Ensure the LDM binary (built with PyInstaller) correctly interacts with the Docker daemon in the Windows CI environment.

### Phase 5: Reporting & Documentation

- Update `scripts/sync_compatibility.py` to automatically ingest and parse reports from all three platforms.
- Update `docs/TESTING.md` to reflect that all platforms are now verified automatically.

## 4. Definition of Done

- `LDM Release E2E` workflow successfully passes on Linux, macOS, and Windows.
- `ldm run --lean` (or CI auto-detect) allows Liferay to boot within 7GB RAM without OOM kills.
- Compatibility table in `README.md` is updated automatically from multi-OS CI results.
