# Implementation Plan: Shared Scenario Packs

## 1. Objective

Create a formal specification for portable "Scenario Packs" that bundle a database snapshot, client extensions, and configuration into a single, distributable archive.

## 2. Key Requirements

- **Bundle Format**: A `.ldmpack` (zip) file containing a manifest and all project assets.
- **Manifest Specification**: A `pack.json` file describing the version, metadata, and dependencies of the pack.
- **Import/Export CLI**: Dedicated commands to create (`ldm pack create`) and extract (`ldm pack install`) these packs.
- **Checksum Validation**: Ensure the integrity of the pack contents using SHA-256.

## 3. Technical Design

### Pack Structure (`.ldmpack`)

```text
/manifest.json
/snapshot/ (database + file store)
/client-extensions/
/configs/ (portal-ext.properties, etc.)
/metadata.json (original project .meta)
```

### CLI Update (`ldm_core/cli.py`)

- Add `ldm pack` namespace.
- Commands:
  - `ldm pack create [project] --output=...`
  - `ldm pack install [file] --name=...`
  - `ldm pack info [file]`

### Handler Logic (`ldm_core/handlers/snapshot.py`)

- Extend the `SnapshotHandler` to support pack creation and installation.
- Implement the packaging logic using Python's `zipfile` module.
- Add logic to verify Liferay version compatibility during installation.

## 4. Implementation Steps

1. **Step 1: Manifest Definition**: Finalize the JSON schema for `pack.json`.
2. **Step 2: Packaging Logic**: Implement `cmd_pack_create` in `SnapshotHandler`.
3. **Step 3: Installation Logic**: Implement `cmd_pack_install` to unpack and initialize a new project from a `.ldmpack`.
4. **Step 4: Verification Logic**: Add SHA-256 generation and check to the packaging process.
5. **Step 5: CLI Integration**: Update `cli.py` to include the `pack` command and subparsers.

## 5. Verification & Testing

1. Create a pack from an existing project: `ldm pack create my-project`.
2. Verify the contents of the generated `.ldmpack` file.
3. Install the pack into a new project: `ldm pack install my-project.ldmpack --name=test-import`.
4. Verify that the new project is identical in state and configuration to the original.
5. Attempt to install a corrupted pack and verify that SHA-256 validation fails.
