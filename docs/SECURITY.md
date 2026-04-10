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
| **B104** | Hardcoded bind to all interfaces. Required for macOS loopback and Gogo shell access. |
| **B108** | Hardcoded /tmp directory. Used only for transient mount verification tokens. |
| **CVE-2026-4539** | Pygments vulnerability. Ignored as LDM only uses Pygments for local console highlighting, posing no remote risk. |

## 3. Hardened Command & Data Processing

Following ourcommitment to local security, the following hardening measures are implemented:

- **Native Command Piping**: Database restore and snapshot operations now use native OS-level process piping (stdin/stdout) instead of `shell=True`. This eliminates the risk of shell injection while maintaining performance for large database dumps.
- **XXE Protection**: Liferay XML license parsing uses a regex-based extraction layer instead of a standard XML parser. This provides absolute immunity to XML External Entity (XXE) attacks.
- **Identifier Sanitization**: All project IDs, container names, and environment identifiers are strictly sanitized to allow only alphanumeric characters, preventing malicious path or command injection via project metadata.

## 4. Sensitive Data Masking (Log Redaction)

LDM implements a proactive redaction layer to prevent the accidental logging of sensitive information in clear-text.

- **Automatic Redaction**: Common sensitive keys (e.g., `PASSWORD`, `SECRET`, `TOKEN`, `AUTH`) are automatically masked with `[REDACTED]` in all command execution logs and verbose output.
- **Local Focus**: While LDM is a sandbox tool and developers are responsible for their local environment, this measure ensures that even when sharing logs for troubleshooting, sensitive credentials remain protected.

## 5. SSL Trust (mkcert)

LDM uses `mkcert` to provide a "Green Lock" experience. This requires installing a local Certificate Authority (CA) on your machine.

- **Risk**: If your private CA key is compromised, an attacker could spoof websites on your local machine.
- **Mitigation**: LDM stores certificates in `~/liferay-docker-certs`. Ensure this directory is kept private.

---

**LDM is not a production orchestrator.** Never use LDM to host publicly accessible Liferay instances or sensitive production data.
