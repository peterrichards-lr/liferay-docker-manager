# Liferay DXP Bug Report: Pydantic-Core Virtual Environment Corruption on ARM64 macOS

[JIRA-KEY] - <https://liferay.atlassian.net/browse/[JIRA-KEY]>

## Component

- **Python Virtual Environment**
- **LDM Dependencies**

## Environment

- **Liferay Product Version**: Liferay Docker Manager (LDM) v2.12.x
- **OS**: macOS ARM64 (Apple Silicon)

## Summary

The local Python virtual environment (`.venv`) occasionally experiences binary corruption with the `pydantic-core` dependency. When executing AI commands (`ldm ai query`), the system throws a `ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'` despite the package being installed in the environment.

## Description & Technical Analysis

This appears to be an issue related to the dynamic linking of pre-compiled wheels for `pydantic-core` on Apple Silicon. Under certain conditions (such as Python version upgrades via Homebrew, or aggressive garbage collection of Homebrew cache), the binary extensions `_pydantic_core.cpython-*.so` become unlinked or invalid for the active Python interpreter.

## Steps to Reproduce

1. Install LDM on an ARM64 Mac using a Homebrew-managed Python.
2. Upgrade the underlying Homebrew Python version (e.g., `brew upgrade python`).
3. Run `ldm ai query`.
4. Observe the `ModuleNotFoundError`.

## Expected Results

The `ldm ai` command should execute smoothly without crashing on missing binary core modules.

## Workaround

The current workaround is to force-reinstall `pydantic` and `pydantic-core` inside the virtual environment:

```bash
source .venv/bin/activate
pip install --force-reinstall pydantic pydantic-core
```

A long-term fix might require tweaking the `.venv` creation script to automatically detect host Python binary drift and trigger a targeted reinstall.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-09* | *Last Reviewed: 2026-07-06*
