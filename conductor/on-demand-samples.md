# Implementation Plan: On-Demand Sample Hydration

## Objective

Enable standalone LDM binaries to download and cache sample assets on-demand from GitHub Releases. This keeps the binary small while ensuring users have access to versioned, tested samples.

## Key Files & Context

- `ldm_core/utils.py`: Add `download_samples(version, destination)` utility.
- `ldm_core/handlers/config.py`: Update `sync_samples(paths)` to handle missing global samples by triggering a download and caching them.
- `.github/workflows/ci.yml`: Update the release job to package `samples/` into a `samples.zip` and upload it as a release asset.

## Implementation Steps

### 1. CI Workflow Update (`.github/workflows/ci.yml`)

- In the `release` job (on `ubuntu-latest`), add a step to zip the `samples/` directory.
- Add `samples.zip` to the `softprops/action-gh-release` files list.

### 2. Downloader Utility (`ldm_core/utils.py`)

- Add `download_samples(version, destination)`:
  - Construct the download URL: `https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v{version}/samples.zip`
  - Use `urllib.request` to download the zip file.
  - Use `zipfile` to extract it to the `destination` (e.g., `~/.ldm/samples/v{version}/`).
  - Handle potential download errors gracefully with `UI.error`.

### 3. Hydration Logic Update (`ldm_core/handlers/config.py`)

- Refine `sync_samples(paths)`:
  1. Check Local: Look for `SCRIPT_DIR / "samples"`.
  2. Check Cache: Look for `~/.ldm/samples/v{VERSION}`.
  3. Prompt & Download: If both are missing:
     - In interactive mode: `UI.ask("Sample assets not found. Download sample pack (~50MB)?", "Y")`.
     - If 'Y', call `download_samples(VERSION, cache_path)`.
  4. Sync: Proceed with the existing sync logic using the identified source path.

### 4. Documentation Update (`docs/README.md`)

- Note that the first use of `ldm run --samples` in the standalone binary will require a one-time download of the sample pack.

## Verification & Testing

1. **Developer Mode**: Verify `ldm run --samples` still works from the git repo using the local `samples/` folder.
2. **Binary Mode (Simulated)**:
   - Temporarily rename the local `samples/` folder.
   - Run `ldm run --samples`.
   - Verify it prompts for download and correctly populates `~/.ldm/samples/v1.4.0/`.
   - Verify the project is hydrated correctly from the cached samples.
