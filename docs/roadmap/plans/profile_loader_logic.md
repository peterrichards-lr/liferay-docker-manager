# Implementation Plan: Extensible Stack Profile Loader

This plan outlines the design and implementation steps for LDM's declarative Stack Profile engine.

---

## 1. Directory Structure

Profiles are stored as directories in `ldm_core/resources/profiles/`:

```text
ldm_core/resources/profiles/
├── <profile-name>/
│   ├── profile.json               # Profile metadata, variables, prompts
│   ├── compose-overlay.yml        # Docker compose key additions/overwrites
│   └── files/                     # Directory layout copied directly to project
```

---

## 2. Schema Specification: `profile.json`

Every profile directory must contain a `profile.json` file defining metadata and custom environment requirements:

```json
{
  "name": "My Custom Profile",
  "description": "Short description shown in CLI menu helper.",
  "variables": [
    {
      "name": "MY_CUSTOM_VAR",
      "description": "Enter the value for MY_CUSTOM_VAR",
      "default": "default-value"
    }
  ],
  "dependencies": {
    "infra": ["traefik"]
  }
}
```

---

## 3. Profile Loader Service (`ProfileService`)

Create a new service `ldm_core/services/profile.py` that handles discovery and application:

### Operations

1. **Discovery**: Scans `ldm_core/resources/profiles/*` to return a list of available profiles.
2. **Menu Selection**: If `ldm init` is run without `--profile` but the user chooses to use a profile, display a fuzzy menu selection list.
3. **Execution/Application**:
   - Reads `profile.json`.
   - Prompts for and collects any defined `variables` (or reads them from parameters), then writes them to `.liferay-docker.meta`.
   - Copies all assets inside the `files/` folder to the target project directory.
   - Parses the project's standard `docker-compose.yml` and merges the contents of `compose-overlay.yml` using key-value recursive merging (for `services`, `volumes`, `networks`).

---

## 4. Integration into LDM Lifecycle

- Update `ldm_core/handlers/composer.py` to check for active profiles in the project metadata and invoke `ProfileService.apply()`.
- Update `ldm_core/cli.py` to register the `--profile` (`-p`) option under `ldm init`.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-22* | *Last Reviewed: 2026-07-02*
