# LDM Security Posture & Disclosures

Liferay Docker Manager (LDM) is designed strictly for **local development and demonstration (sandbox)** use. To provide a professional, platform-agnostic experience across macOS, Windows, and Linux, certain intentional security tradeoffs have been made.

## 1. Intentional Network Bindings (0.0.0.0)

LDM binds certain infrastructure components to `0.0.0.0` (all interfaces) instead of `127.0.0.1` (localhost) in specific scenarios:

- **macOS Multi-IP Loopback**: On macOS (Silicon and Intel), Traefik is bound to `0.0.0.0` to ensure that custom hostnames (e.g., `mysite.local`) correctly route back to the Docker containers.
- **Gogo Shell Exposure**: To allow telnet access to the Liferay OSGi console, the Gogo shell listener is bound to `0.0.0.0` inside the container network.

**Mitigation**: These bindings are only active while the LDM stack is running. In a standard home/office network, this exposure is limited to the local subnet. Users requiring stricter isolation should use a firewall or VPN.

## 2. Ignored Security Scans (Bandit/Audit Disclosures)

The LDM CI pipeline runs Bandit security scans. We explicitly ignore the following warnings to maintain core functionality:

| Code | Intent & Disclosure |
| :--- | :--- |
| **B103** | Permissive 777 permissions. Used in `migrate_layout` for legacy projects and `cmd_upgrade` to ensure the newly downloaded binary is executable. **Mitigation**: All calls are wrapped in `try...except` and LDM prioritizes standard user ownership. |
| **B104** | Hardcoded bind to all interfaces. Required for macOS loopback, Gogo shell access, and infrastructure setup. |
| **B105** | Hardcoded passwords or tokens. Used for default local development database passwords (e.g., `test`) and transient mount verification tokens. **Mitigation**: These are only used in isolated sandbox environments and are not intended for production secrets. |
| **B108** | Hardcoded /tmp directory. Used only for transient mount verification tokens. |
| **B110 / B112** | `try...except pass/continue` patterns. Used in loops (e.g., tag discovery, project scanning) and cleanup routines where a single failure should not abort the entire operation. |
| **B202** | Tar `extractall` operations. Used for snapshots and samples. **Mitigation**: LDM uses a mandatory `is_within_root` validation before every extraction to prevent Zip Slip / Path Traversal attacks. |
| **B324** | Use of MD5 hashing. Used in `cloud-fetch` for ETag verification and non-cryptographic file integrity checks. |
| **B602 / B603** | Subprocess execution with shell or without absolute paths. Used for complex piping during database snapshots, Windows bridge logic, and rendering the local man page (`cmd_man`). **Mitigation**: Commands are hardcoded or constructed from strictly sanitized internal identifiers. |
| **B604** | Function call with `shell=True`. Used in `is_completion_enabled`, `cmd_completion`, and `cmd_man` to interact with shell builtins and render the local man page. **Mitigation**: All command strings are hardcoded and contain no user-supplied input. |
| **B605** | Start process with a shell. Used in `cmd_log_level` to pipe Gogo shell commands via `nc` for dynamic log adjustment. **Mitigation**: The command string is hardcoded and only sanitized level/category strings are interpolated. |
| **CVE-2026-4539** | Pygments vulnerability. Ignored as LDM only uses Pygments for local console highlighting, posing no remote risk. |

## 3. Hardened Command & Data Processing

Following our commitment to local security, the following hardening measures are implemented:

- **Native Command Piping**: Database restore and snapshot operations now use native OS-level process piping (stdin/stdout) instead of `shell=True`. This eliminates the risk of shell injection while maintaining performance for large database dumps.
- **XXE Protection**: Liferay XML license parsing uses a regex-based extraction layer instead of a standard XML parser. This provides absolute immunity to XML External Entity (XXE) attacks.
- **Identifier Sanitization**: All project IDs, container names, and environment identifiers are strictly sanitized to allow only alphanumeric characters, preventing malicious path or command injection via project metadata.
- **Snapshot Integrity Verification**: LDM generates SHA-256 checksums for all project snapshots. During recovery or import, the tool validates the archive against this checksum to detect accidental corruption or accidental tampering. Note that this provides *integrity* (data validity) but not *authenticity* (proof of origin), as the checksum files are stored alongside the data in the local filesystem.

## 4. Sensitive Data Masking (Log Redaction)

LDM implements a proactive redaction layer to prevent the accidental logging of sensitive information in clear-text.

- **Automatic Redaction**: Common sensitive keys (e.g., `PASSWORD`, `SECRET`, `TOKEN`, `AUTH`) are automatically masked with `[REDACTED]` in all command execution logs and verbose output.
- **Local Focus**: While LDM is a sandbox tool and developers are responsible for their local environment, this measure ensures that even when sharing logs for troubleshooting, sensitive credentials remain protected.

## 5. SSL Trust (mkcert)

LDM uses `mkcert` to provide a "Green Lock" experience. This requires installing a local Certificate Authority (CA) on your machine.

- **Risk**: If your private CA key is compromised, an attacker could spoof websites on your local machine.
- **Mitigation**: LDM stores certificates in `~/liferay-docker-certs`. Ensure this directory is kept private.

## 6. Sudo & Internalized Elevation

To protect the integrity of the application cache (`~/.shiv`) and ensure consistent file ownership, **LDM prohibits being run with the `sudo` prefix.**

The tool utilizes a "Just-in-Time" elevation strategy. It runs as your standard user and only invokes `sudo` internally for specific system-level operations:

- **`ldm fix-hosts`**: Requests elevation to append entries to `/etc/hosts`.
- **`ldm upgrade`**: Requests elevation to replace the binary in system paths like `/usr/local/bin`. Uses a `cp` + `rm` pattern to handle cross-device links during replacement. On Windows, it seamlessly requests administrative privileges via UAC prompts when replacing the binary. Supports optional `--beta` flag for opting into pre-releases.

This ensures that all project data and temporary files remain owned by your local user, preventing permission-related lockouts.

## 7. Secrets Prevention & Commit Gates

LDM integrates Yelp's `detect-secrets` scanner into its pre-commit hook suite to block credentials, API keys, and passwords from ever being committed.

- **Baseline Exceptions**: Legacy false positives or mock credentials required for unit testing are documented in `.secrets.baseline` (containing hashed values of the ignored strings) so they do not block development.
- **Manual Scanning**: To scan all tracked files in the workspace at any time:

  ```bash
  .venv/bin/pre-commit run detect-secrets --all-files
  ```

- **Updating Exceptions**: If a new safe mock key is flagged as a false positive, append it to the baseline:

  ```bash
  detect-secrets scan --baseline .secrets.baseline
  ```

### 💡 Reusing Secrets Prevention in Other Projects

To implement equivalent commit-blocking gates in your other repositories:

#### Option A: Yelp's `detect-secrets` (Python/Baseline approach)

Excellent for projects that already use Python or where you want a baseline file to manage exceptions. Add this to your `.pre-commit-config.yaml`:

```yaml
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
```

Generate the baseline file initially via `detect-secrets scan > .secrets.baseline` and track it in git.

#### Option B: Gitleaks (Compiled Go Binary / Fingerprint approach)

A fast, single-binary alternative that requires no Python runtime. Add this to your `.pre-commit-config.yaml`:

```yaml
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.2
    hooks:
      - id: gitleaks
```

To ignore mock keys or false positives, create a `.gitleaksignore` file in the root of the project to catalog exceptions by fingerprint or commit hash:

```ini
# .gitleaksignore
# Add mock tokens or hashes here to prevent them from blocking commits.
# Format: [fingerprint_hash] OR [commit_hash]
f7e5b56e5e4e6e94fe5de5424e66fef84be863f385
```

---

**LDM is not a production orchestrator.** Never use LDM to host publicly accessible Liferay instances or sensitive production data.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-08* | *Last Reviewed: 2026-07-02*
