# Global Common Overrides

This directory is used for files that should be synchronized to **every** LDM project (e.g., global configuration files, OSGi configs, or LPKG modules).

## Baseline Assets

The standard development baseline (portal-ext.properties, env-blacklist, etc.) is now bundled internally within the LDM binary.

To recreate the baseline files in this directory, run:

```bash
ldm init-common
```

## Usage

- Place any `*.config`, `*.cfg`, `*.xml`, or `*.lpkg` files here to have them automatically deployed to every project stack during startup.
- Files are tracked via `.liferay-docker.deployed` within each project to ensure they are only synchronized once.
