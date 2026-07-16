# Unified Project Management & Automation Playbook

This consolidated guide serves as a blueprint for AI agents and maintainers to standardize repository management, release pipelines, and automated backlog prioritization.

---

## 1. Branch Protection & Integration Workflow

To maintain production stability, the `master` branch (or primary production branch) is protected against direct pushes. All integration must follow this strict protocol:

- **Branch Isolation**: Create short-lived branches prefixed by scope:
  - `feat/...` for new features.
  - `fix/...` for bug fixes.
  - `docs/...` for documentation updates.
- **Pre-Commit Validation**: Before pushing, local Git pre-commit hooks must run:
  - `gitleaks` to check for hardcoded secrets.
  - Language-specific formatters and linters (e.g., `go fmt`, `go vet`, `golangci-lint` or Python `ruff`, `mypy`).
  - The complete unit test suite.
- **Pull Request & Auto-Merge**: Open PRs via the GitHub CLI and immediately enable auto-merge with a squash fallback to satisfy status checks asynchronously:

  ```bash
  gh pr create --title "feat: <description>" --body "<details>" --head <branch_name> --base master
  gh pr merge <pr_number> --auto --squash --delete-branch
  ```

- **Local Synchronization**: Once merged, return to the base branch and pull changes:

  ```bash
  git checkout master && git pull origin master
  ```

---

## 2. Automated Backlog Management & Reaction Prioritization

Instead of tracking requests in static lists, leverage GitHub Issues enhanced with structured templates and automated reaction-based prioritization.

### Issue Templates

- **Bug Reports**: Must capture environment-specific metadata (e.g., Liferay CE/DXP version, Native Bundle vs. Docker) and require verbose debug logs (`-v` or `LOG_LEVEL=debug`).
- **Feature Requests (FR)**: Must require a defined business or proof-of-concept (POC) impact to measure value. Include user-facing headers explaining how to vote using GitHub's native 👍 reactions.

### Automated Prioritization Engine

Because GitHub Actions do not natively trigger on reaction additions/removals, implement a hybrid scheduler/trigger workaround (`prioritize-issues.yml`) running a Python parser (`prioritize_issues.py`):

```text
+------------------+      +-----------------------+      +------------------------+
| Hybrid Trigger   | ---> | Python Parser Script  | ---> | GitHub CLI (gh)        |
| (Cron / Dispatch)|      | prioritize_issues.py  |      | Adjusts Priority Labels|
+------------------+      +-----------------------+      +------------------------+
```

- **The Engine**: The script uses `gh issue list --json number,reactionGroups` to fetch thumbs-up counts.
- **Label Management**: Dynamically strips old priority tags and applies unified tracking labels:
  - `priority: p1` (10+ upvotes)
  - `priority: p2` (5-9 upvotes)
  - `priority: p3` (<5 upvotes)
- **Permissions**: Ensure the GitHub Actions runner environment has explicit write permissions for issues:

  ```yaml
  permissions:
    issues: write
  ```

---

## 3. Version Tagging & CI/CD Release Pipeline

Automate releases using the repository's dedicated orchestrator script: `python3 scripts/release.py`.

- **Initiate a Pre-release**: Run this command from `master` branch to bump the version, stage the required release-related changes, create a tracking PR, and push the release tag directly on the release branch:

  ```bash
  python3 scripts/release.py --bump beta
  ```

- **Promote to Stable**: Run this command from the active `release/v*` branch to promote the version to a stable release, auto-merge the tracking PR to master, and push the stable release tag on master:

  ```bash
  python3 scripts/release.py --promote
  ```

- **CI/CD Compilation & Distribution**: Pushing tags (pre-release or stable) triggers the GitHub Actions workflows, which execute:
  - **Cross-Compilation**: Compiles platform-specific binaries, injecting version numbers at build time.
  - **Checksum Verification**: Generates SHA256 hashes and pushes a `checksums.txt` file.
  - **GitHub Release**: Creates an official GitHub Release and attaches the compiled assets.
  - **Package Managers**: Automatically pushes updated manifests to community repositories.
  - **Containerization**: Builds and publishes the latest application container image.

---

## 4. Cryptographic Signing & Non-Interactive Workaround

When running scripts via an AI agent, standard input is non-interactive (`[ -t 0 ]` evaluates to false), meaning interactive authentication requests (such as 1Password CLI prompts or macOS Keychain requests) will cause the environment to hang.

### The Workspace Handoff Solution

1. **Agent Phase**: The AI agent compiles the raw binaries into a shared local workspace.
2. **Human Phase**: The user opens an interactive terminal (where standard input is a true TTY) and executes the signing script locally to authorize Touch ID, physical tokens, or passphrases:

   ```bash
   IDENTITY="<ID>" KEY="op://..." PASS="op://..." ./scripts/sign-binaries.sh
   ```

3. **Resume Agent Phase**: The AI agent monitors the workspace, detects the cryptographically signed binaries, and continues the secure deployment process.

---

## 5. Gateway & Remote Server Deployment

- **Production Compilation**: Compile binaries using optimizations that strip local debugging file paths to enhance security and EDR compatibility (e.g., `-trimpath`).
- **Secure Asset Transit**: Copy the production binaries, static templates, configuration assets, and translations to the remote VPS using an SSH identity key.
- **Remote Execution**: Execute a secure SSH block to move assets to protected system directories, configure proper file permissions, and safely restart the host daemon:

   ```bash
   ssh -i ~/.ssh/id_key user@ip "sudo systemctl restart application-daemon"
   ```

- **Automated Diagnostics Validation**: Run a post-deployment health check script testing:
  - DNS A/AAAA record propagation.
  - Network reachability and firewall port openings (e.g., 22, 80, 443).
  - JSON responses from the application's version endpoint (`/api/version`) to guarantee the active deployment matches the newly released target version.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-16* | *Last Reviewed: 2026-07-10*
