# Development & Building

## 🛠️ Development & Building

If you want to contribute to LDM or test your changes locally, follow these steps.

### 1. Run from Source (Live Development)

The easiest way to develop is to install LDM in "editable" mode. This allows your changes to the `ldm_core` package to take effect immediately.

```bash
# Clone the repo
git clone https://github.com/peterrichards-lr/liferay-docker-manager.git
cd liferay-docker-manager

# Install in editable mode
pip install -e .

# Run the entry point
python3 liferay_docker.py --help
```

### 2. Building Standalone Binaries

You can build a single-file executable to test how the tool behaves as a binary.

#### **Option A: Shiv (Official CI Method)**

Used for macOS and Linux. Fast and lightweight, but requires `python3` to be present on the host.

```bash
# Build only
./scripts/package-shiv.sh

# Build and install to /usr/local/bin/ldm (requires sudo)
./scripts/package-shiv.sh --install
```

#### **Option B: PyInstaller (True Standalone)**

Bundles the Python interpreter inside the file. Works even on machines without Python installed.

```bash
# Build only
./scripts/package-pyinstaller.sh

# Build and install to /usr/local/bin/ldm (requires sudo)
./scripts/package-pyinstaller.sh --install
```

The resulting binary will be found in the `dist/` folder (for PyInstaller) or the root (for Shiv).

### 3. Pre-commit & Pre-push Setup

To keep local commits fast (< 2 seconds), code formatting, style checks, and secrets scanning run on `git commit`. Heavy quality gates (type checking, security audits, and testing) are deferred to `git push`.

Verify you have both hook types installed in your clone:

```bash
pre-commit install --hook-type pre-commit --hook-type pre-push
```

### 4. Documentation Version Badges

When documenting CLI commands, flags, or configuration options, always specify the LDM version in which the feature was introduced using a visual badge (Shields.io) or bold inline label:

* **Visual SVG Badge (Recommended)**:
  `![Added in v2.11.34](https://img.shields.io/badge/Added%20in-v2.11.34-blue)`
* **Text Fallback**:
  `**[Added in v2.11.34]**`

Place the badge immediately below the heading or next to the flag definition (e.g., in a table or list).

---

## 🛠️ Codebase Conventions & Command Execution

### Version Badges for New Features

To clarify required minimum LDM versions for users, always append a Shields.io version badge next to any mentions of newly introduced flags, features, or commands in the markdown documentation.

**Format**:

```markdown
![Added in v2.X.X](https://img.shields.io/badge/Added%20in-v2.X.X-blue)
```

**Example Usage**:

* `--my-new-flag` ![Added in v2.15.0](https://img.shields.io/badge/Added%20in-v2.15.0-blue) : Does a new thing.

### Execution Safety

When executing external binaries or shell commands (e.g. docker, git, mkcert, lcp):

* **Avoid Direct `subprocess.run` / `subprocess.Popen`**: Running subprocesses directly bypasses central handlers and can introduce security vulnerabilities (Bandit B602/B607) or fail during dry-run executions.
* **Use Centralized wrappers**:
  * If executing inside a Service/Handler subclassing `BaseHandler`, use `self.run_command(cmd, ...)` or `self.manager.run_command(...)`.
  * If executing inside helper modules, import and call `run_command` from `ldm_core.utils`.
  * This automatically enables credential redaction, dry-run mocking, environment variable injection, and platform-specific binary resolution.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-15* | *Last Reviewed: 2026-07-10*
