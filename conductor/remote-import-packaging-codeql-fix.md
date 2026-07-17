# Plan: Remote Import & Packaging - CodeQL Security Fixes

This plan outlines the specific security hardening and logic improvements required to resolve CodeQL / Code scanning check failures on the `roadmap/remote-import-packaging` branch.

## Objective

Secure the newly added remote workspace import and packaging capabilities against:

1. **Path Traversal / Arbitrary File Write**: Ensure user-supplied URLs and remote API payloads do not allow files to be downloaded/written outside their intended temporary directory.
2. **Command Option Injection**: Prevent flag injection in `git clone` by introducing command shielding.
3. **Complex URL Parsing Failures**: Fix a logical bug in parsing nested GitHub paths.

## Key Files & Context

- `ldm_core/handlers/workspace.py`: Contains URL parsing and workspace import/cloning methods.
- `ldm_core/tests/test_workspace.py`: Unit tests for remote import and parsing logic.

## Implementation Steps

### 1. Refactor `_parse_github_repo`

Make URL parsing robust against both standard repository landing URLs and deep links (e.g. branch, commits, tree navigation):

- Separate SSH URLs (`git@github.com:...`) and HTTP/HTTPS URLs (`github.com/...`).
- Slice paths cleanly after `github.com/` and pick the first two segments.
- Reliably strip `.git` extension and trailing slashes.

### 2. Sanitize and Validate Archive Downloads in `cmd_import`

For remote `.zip`/`.tgz`/`.tar`/`.ldmp` archive imports:

- Extract the base filename using `Path(archive_name).name`.
- Construct `local_path = (temp_dir / archive_name).resolve()`.
- Validate that `is_within_root(local_path, temp_dir)` is true. If not, raise a SystemExit error with a clear Security Violation message.
- Similarly, generate `sha_path` using `Path(local_path.name + ".sha256").name` and verify containment with `is_within_root`.

### 3. Sanitize and Validate API-Fetched Asset Downloads in `cmd_import`

For assets downloaded via the GitHub Releases API payload:

- Extract base names of `.ldmp` and `.ldmp.sha256` files using `Path(...).name`.
- Construct `ldmp_path` and `sha_path` inside `temp_pkg_dir`.
- Check containment for both using `is_within_root(..., temp_pkg_dir)`.

### 4. Shield Subprocess Invocations from Option Injection

Shield `git clone` from being passed command flags via the remote repository URL:

- Use `git clone -- <source_path> <temp_git_dir>` to treat the source path strictly as a positional argument.

### 5. Expand Unit Tests in `test_workspace.py`

Verify parsing of nested paths and option shielding:

- Add a test case for deep GitHub URLs (e.g., `https://github.com/owner/repo/tree/master/subpath`).
- Update mocks in `test_cmd_import_git_url_success` to expect the `--` option shield in `subprocess.run` arguments.

## Verification & Testing

1. Run Python unit tests inside the virtual environment to verify functional correctness:

   ```bash
   .venv/bin/pytest ldm_core/tests/test_workspace.py
   ```

2. Run the project's quality pipeline to ensure linter compliance, security scans (Bandit), and full test coverage:

   ```bash
   ./lint.sh --check
   ```

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-17* | *Last Reviewed: 2026-07-02*
