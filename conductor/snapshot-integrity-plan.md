# Implementation Plan: Snapshot Integrity Verification

## 1. Objective

Ensure the validity and integrity of LDM snapshots and "Scenario Packs" through mandatory SHA-256 checksumming.

## 2. Key Requirements

- **SHA-256 Checksum Generation**: Automatically generate a checksum for each snapshot or pack.
- **Verification on Import**: Automatically verify the checksum before allowing a snapshot or pack to be imported or restored.
- **Tamper Evidence**: Provide clear warnings if a snapshot has been modified or corrupted.

## 3. Technical Design

### Checksum Logic (`ldm_core/utils.py`)

- Implement a utility function to calculate the SHA-256 hash of a file or directory (recursively for snapshots).
- Store the hash in a `.sha256` file alongside the snapshot or within the pack manifest.

### Handler Logic (`ldm_core/handlers/snapshot.py`)

- Update `cmd_snapshot` to generate a checksum file after creation.
- Update `cmd_restore` and `cmd_import` to search for and verify the checksum before proceeding.

### CLI Update (`ldm_core/cli.py`)

- Add a `--verify` flag to `snapshot`, `restore`, and `import` commands.
- Support a `--no-verify` flag to allow intentional modifications (use with caution).

## 4. Implementation Steps

1. **Step 1: Checksum Utility**: Implement the recursive SHA-256 hashing logic.
2. **Step 2: Snapshot Integration**: Update `SnapshotHandler` to generate checksums for every new snapshot.
3. **Step 3: Verification Logic**: Add the checksum check to the `restore` and `import` paths.
4. **Step 4: Pack Integration**: Ensure "Scenario Packs" include SHA-256 hashes in their manifest.
5. **Step 5: User Warnings**: Implement UI indicators (Success/Warning) based on verification results.

## 5. Verification & Testing

1. Create a snapshot: `ldm snapshot project-a`.
2. Verify that a `.sha256` file is created.
3. Manually modify a file within the snapshot and attempt a restore: `ldm restore project-a`.
4. Verify that the tool identifies the corruption and warns the user.
5. Restore a clean snapshot and verify that no warning is shown.
