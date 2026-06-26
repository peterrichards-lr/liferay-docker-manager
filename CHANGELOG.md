# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.7.28] - 2026-05-21

### Fixed

- **Port Mapping Verification**: Fixed a hardcoded port issue in the terminal output where LDM would always report `http://localhost:8080` for local access, even when a custom `--port` (like `8082` in E2E tests) was configured.

## [2.7.27] - 2026-05-21

### Fixed

- **E2E Script Adjustments**: Updated the verification script to skip the behavioral sudo guard check in CI, matching LDM's CI root allowance.
- **Workflow Dependencies**: Added `which` to the Fedora verification job to support script-level path discovery.

## [2.7.26] - 2026-05-21

### Fixed

- **CI Test Stability**: Updated security policy tests to correctly account for the `GITHUB_ACTIONS` environment, fixing a false-positive failure in CI.
- **Workflow Resilience**: Hardened system dependency installation in the verification workflow. Now uses `python3-full` and `hostname` to ensure all platforms have necessary tools for automated testing.

## [2.7.25] - 2026-05-21

### Fixed

- **CI Root Execution**: LDM now automatically allows root execution when `GITHUB_ACTIONS=true` is detected. This prevents security warnings from corrupting output slugs in CI environments.
- **Workflow Dependencies**: Added missing system packages (`python3-venv`, `hostname`) to the new verification workflow to ensure smooth execution on Fedora and Ubuntu runners.

## [2.7.24] - 2026-05-21

### Fixed

- **JVM Argument Deduplication**: Fixed a critical bug in the JVM options parser that caused `-Xms` (initial heap) and `-Xmx` (max heap) to collide, resulting in JVM initialization failures in resource-constrained environments.

## [2.7.23] - 2026-05-21

### Added

- **Lean JVM Profile**: Introduced a resource-optimized JVM profile (2GB heap) for constrained environments.
- **Automatic CI Optimization**: LDM now automatically switches to the Lean profile when it detects a GitHub Actions environment, ensuring reliable Liferay boots on standard 7GB runners.
- **`--lean` Flag**: Added a manual flag to `run` and `import` for users on low-memory local machines.

## [2.7.22] - 2026-05-21

### Fixed

- **Intelligent `ldm env`**: Fixed a discrepancy where hitting "Enter" at the environment prompt would save but not apply shell variables.
- **Unattended Environment Sync**: Enabled `ldm env <project> -y` to automatically synchronize all passthrough shell variables (e.g. AI keys) without requiring explicit arguments, significantly easing CI automation.

## [2.7.21] - 2026-05-21

### Fixed

- **Polished DNS Output**: Cleaned up the pre-flight hostname verification to suppress redundant manual fix suggestions when multiple subdomains are missing. LDM now provides a concise, unified summary before offering the automated `/etc/hosts` fix.

## [2.7.20] - 2026-05-21

### Fixed

- **Polished Upgrade Errors**: Improved the error handling in `ldm upgrade` to provide user-friendly messages when elevated privilege requests fail, replacing raw command strings with actionable advice.

## [2.7.19] - 2026-05-21

### Added

- **Comprehensive Project Rollback**: Enhanced the atomic initialization logic to ensure failed brand-new projects are not only deleted from disk but also removed from the global registry.
- **Improved Workspace Cleanup**: `ldm import` now proactively cleans up the temporary extraction directory and its parent `.ldm_temp` folder on completion or failure.

## [2.7.18] - 2026-05-21

### Added

- **Atomic Project Initialization**: Implemented a "Commit/Rollback" pattern for `ldm run` and `ldm import`. If a brand-new project fails to initialize (e.g., due to DNS errors or build failures), LDM now automatically cleans up the half-baked directory to prevent inconsistent project states.
- Enhanced `ldm import` with the shared **Intelligent Subdomain Fixing** engine, ensuring consistency with the `run` command.

## [2.7.17] - 2026-05-21

### Fixed

- Fixed `ldm fix-hosts` to correctly fallback to treating the target as a direct hostname if no matching project is found.
- Hardened `_apply_hosts_fix` to prevent adding empty configuration blocks to `/etc/hosts`.
- Improved `check_hostname` to verify that domains resolve to local IPs, preventing false-positive resolution reports for remote addresses.

## [2.7.16] - 2026-05-21

### Fixed

- Fixed `ldm scale` command failing in non-interactive environments when the project was already running.

## [2.7.15] - 2026-05-21

### Documentation

- Formally documented the **Environment Variable Forwarding** logic in `README.md`, covering `LDM_` prefix stripping, automatic AI passthrough, and service-specific targeting.
- Documented the **Automatic Volume Hardening** behavior for macOS external drives.

## [2.7.14] - 2026-05-21

### Changed

- **Refactored Environment Forwarding**: Consolidated global and service-specific variable logic. LDM now supports:
  - **`LDM_` Strip-Forwarding**: Global variables (e.g. `LDM_VAR=xxx`) are forwarded to all containers with the prefix removed (`VAR=xxx`).
  - **Unified Passthrough**: Liferay Cloud and AI provider keys (`OPENAI_`, `GEMINI_`, etc.) are automatically forwarded as-is.
  - **Custom Passthrough**: Users can now extend the passthrough list by setting `LDM_FORWARD_PREFIXES` (comma-separated) on the host.

## [2.7.13] - 2026-05-21

### Added

- **Flexible `ldm deploy`**: Now accepts optional specific services or file paths (JAR, WAR, ZIP). ZIP files are intelligently handled as either Client Extensions or OSGi Fragments.
- **Dedicated `ldm wait` Command**: Standardized way to block scripts until Liferay is responding to HTTP requests.
- **Automatic Volume Hardening**: Proactively detects macOS `/Volumes/` paths and enables `--internal-state` for reliable OSGi file locking.
- **Automatic AI Environment Forwarding**: Host variables starting with `OPENAI_`, `GEMINI_`, `ANTHROPIC_`, or `MISTRAL_` are now automatically available inside all project containers.
- **Automatic Non-Interactive Hosts Fix**: When running with `-y`, LDM now attempts to fix missing `/etc/hosts` entries automatically using `sudo -n`.
- **Smarter Subdomain Fixing**: `ldm fix-hosts` and pre-flight checks now scan for active client extensions and include their required subdomains in the resolution check.

### Fixed

- **AutoDeployScanner Permissions**: Resolved "Unable to write" errors on Linux by ensuring the `osgi/` directory tree is included in proactive permission reclamation.
- **Zero-Race Atomic Deployment**: All file deployments now use hidden staging files with proactive permission fixups before the final atomic rename.
- **Developer Guardrails**: Pass-through for `-y/--non-interactive` to bypass internal developer mode prompts while maintaining protection for unattended CI environments.

## [2.7.11] - 2026-05-21

### Added

- **Smarter Subdomain Fixing**: Enhanced `ldm fix-hosts` and pre-flight checks to automatically identify and fix missing client extension subdomains. LDM now scans projects for active extensions and ensures every required URL resolves to 127.0.0.1 before starting.
- Updated documentation to clarify that LDM handles the entire project DNS tree (main host + wildcards/subdomains) automatically.

## [2.7.10] - 2026-05-21

### Fixed

- Implemented universal permission fixup (`chmod 666` and `chown 1000:1000`) for all file deployment operations on Unix. This definitively resolves the "Unable to write" errors in Liferay's `AutoDeployScanner` when LDM is running as root (e.g. in CI or with sudo).

## [2.7.9] - 2026-05-21

### Fixed

- Enabled true non-interactive execution for commands requiring elevation (`fix-hosts`, `upgrade`) on Linux and macOS. By passing `-y/--non-interactive`, LDM now uses `sudo -n` to suppress password prompts and fail fast if a password is required.

## [2.7.8] - 2026-05-21

### Documentation

- Formally documented the **Client Extension Routing & Wildcard SSL** logic in `README.md`.
- Reorganized the `README.md` to move the `LDM_COMMON_DIR` section under "Configuration Files" for better logical flow.

## [2.7.7] - 2026-05-21

### Documentation

- Formally documented the **Zero-Race Atomic Deployment Strategy** in `docs/LDM_ARCHITECTURE.md`, detailing the staging and permission fixup pattern.

## [2.7.6] - 2026-05-21

### Fixed

- Hardened atomic deployment logic by ensuring Unix permission fixups occur on hidden staging files *before* they are moved into Liferay's scanner path. This eliminates the race condition where `AutoDeployScanner` could see a file before its ownership was handed off to the `liferay` user.

## [2.7.4] - 2026-05-21

### Fixed

- Fixed permission errors (`Unable to write [file.zip]`) in `AutoDeployScanner` on Linux host environments (e.g. GitHub Actions) by ensuring the `osgi` directory is targeted during proactive volume permission reclamation.

## [2.7.3] - 2026-05-21

### Fixed

- Fixed port check conflict where `ldm import` would start a project and `ldm run` would fail.
- Added runtime state-awareness checks to commands (run, import) to prevent unexpected container collisions.
- Enabled non-interactive bypass for internal developer utility prompts.

## [v2.11.59] - 2026-06-26

### Added

-

## [v2.11.58] - 2026-06-26

### Added

-

## [v2.11.57] - 2026-06-26

### Added

-

## [v2.11.56] - 2026-06-26

### Added

-

## [v2.11.55] - 2026-06-25

### Added

-

## [v2.11.54] - 2026-06-25

### Added

-

## [v2.11.53] - 2026-06-25

### Added

-

## [v2.11.52] - 2026-06-24

### Added

- Added support for immediate search reindexing on running containers via OSGi Gogo telnet command.

## [v2.11.51] - 2026-06-24

### Added

-

## [v2.11.50] - 2026-06-24

### Added

-

## [v2.11.49] - 2026-06-24

### Added

-

## [v2.11.48] - 2026-06-24

### Added

-

## [v2.11.47] - 2026-06-24

### Added

-

## [v2.11.46] - 2026-06-24

### Added

-

## [v2.11.45] - 2026-06-24

### Added

-

## [v2.11.44] - 2026-06-24

### Added

-

## [v2.11.43] - 2026-06-24

### Added

-

## [v2.11.42] - 2026-06-24

### Added

-

## [v2.11.41] - 2026-06-23

### Added

-

## [v2.11.40] - 2026-06-23

### Added

-

## [v2.11.39] - 2026-06-23

### Added

-

## [v2.11.38] - 2026-06-23

### Added

-

## [v2.11.37] - 2026-06-23

### Added

- Added automated resolution for project registry collisions, including auto-cleaning stale paths, interactive prompts, and the `--overwrite-registry` CLI flag.

## [v2.11.35] - 2026-06-23

### Added

- Added `--auto-install-lfr-tunnel` command-line argument and `lfr_tunnel_bin` / `lfr_tunnel_install_cmd` settings to configure custom paths or commands for the `lfr-tunnel` binary.
- Added mapping and propagation of global configuration preferred admin user details (e.g. `admin_password`, `admin_first_name`, etc.) directly into target `portal-ext.properties`.

### Fixed

- Fixed `--tag-latest` and `--tag-prefix` to correctly override the project's locally stored metadata tag during run commands.
- Fixed subprocess invocation to use `encoding="utf-8"`, preventing `UnicodeDecodeError` exceptions on Windows environments under non-UTF-8 locale encodings.

## [v2.11.34] - 2026-06-23

### Added

- Added `--leave-running` option for the `import` command to keep the running project active and abort the import cleanly.

### Changed

- Automatically stop running projects in non-interactive/yes (`-y`) mode during `import` commands.

## [v2.11.33] - 2026-06-23

### Added

- Added warning check when running LDM commands directly from the Home directory (CWD is `~`) to prevent folder clutter.
- Documented database container online requirements for `.ldmp` package exports (with AICA CI case study details in `DATA_MANAGEMENT.md`).
- Consolidated `--stop-running` flag support from the pre-release.

## [v2.11.32] - 2026-06-23

### Added

- Skipped due to pre-squash release tag mismatch.

## [v2.11.32-pre.1] - 2026-06-23

### Added

- Added `--stop-running` flag to `ldm import` command to automatically stop a running instance.

## [v2.11.31] - 2026-06-22

### Added

- Support for custom quickstart templates overrides via `~/.ldm_templates.json`.
- Automatic tunnel sharing exposure under `ldm import` when the `--share` flag is passed.
- Robust unit test coverage for package commands and template overrides.

### Fixed

- Typo in workspace quickstart test patching (`AssetsService` -> `AssetService`).
- Mock manager sharing helper verification in unit tests.
- Standalone package export command (`ldm package`) snapshot listing and dynamic test path assertions.

## [v2.11.21] - 2026-06-20

### Added

-

## [v2.11.20] - 2026-06-20

### Added

- Added explicit container naming (e.g. `[project_name]-lfr-tunnel`) for the `lfr-tunnel-docker` sidecar service to improve clean container teardown.
- Persisted `tunnel_container_name` in project metadata and included it in the `ldm status` diagnostics display.

## [v2.11.19] - 2026-06-20

### Added

- Added automatic Liferay portal proxy configuration (`web.server.host`, `web.server.https.port`, and `web.server.protocol` in `portal-ext.properties`) when sharing a project via standard tunnel.

### Fixed

- Added automatic cleanup of previous `portal-ext.properties` proxy/tunnel overrides when neither sharing nor SSL proxy is active.

## [v2.11.18] - 2026-06-20

### Added

- Added `--share-inspector` option to `ldm run` command.
- Added `--inspector` option to `ldm share start` subcommand.

### Changed

- Made the tunnel inspector dashboard opt-in and bound it to `127.0.0.1` inside the container by default (no exposed port `4040` unless opted in).
- Cleaned the local host-side `.env` configuration files of the `LFT_INSPECTOR_BIND` setting.

## [v2.11.17] - 2026-06-19

### Added

- Mapped port `4040` for the `lfr-tunnel` container sidecar to allow host machine access to the web inspector dashboard.
- Added support and automatic `.env` initialization for `LFT_INSPECTOR_BIND` binding address overrides.

## [v2.11.16] - 2026-06-19

### Added

- Added support for resolving and printing public tunnel URLs when using share/expose providers.
- Added support for `.env` overrides (`LFT_SUBDOMAIN`, `LFT_CLIENT_TOKEN`, and `LFT_SERVER_URL`) for the tunnel container.

## [v2.11.15] - 2026-06-19

### Added

- **Custom share image flags**: Added `--share-image` (to `ldm run`) and `--image` (to `ldm share start`) CLI flags to specify custom tunnel Docker image sources.

### Fixed

- **lfr-tunnel Docker image namespace**: Updated default sidecar namespace from `peterrichards` to `peterjrichards` to match the official container repository.

## [v2.11.14] - 2026-06-19

### Added

- **Integrated lfr-tunnel-docker Compose Service**: Added support for running the containerized `lfr-tunnel` client as a service sidecar directly inside the generated `docker-compose.yml` stack.
- **EDR & SentinelOne Bypass**: Encapsulating the client inside the Docker runtime space prevents host-native Go binary blockages.
- **Resource Optimization**: Imposed minimal CPU (`0.10` limits, `0.05` reservations) and memory (`50M` limits, `20M` reservations) constraints on the sidecar service.
- **Host Header & Redirection**: Directly routes external subdomain traffic internally to Tomcat at `http://liferay:8080`, facilitating correct absolute URL redirects.

## [v2.11.9] - 2026-06-15

### Fixed

- Resolved LDM self-upgrade failures due to unauthenticated GitHub API rate limiting by implementing a fallback check via HTML redirect.

## [v2.11.5] - 2026-06-10

### Added

- **lfr-tunnel Integration**: Integrated `lfr-tunnel` host-side Go client for wildcard subdomain routing (`*.lfr-demo.se` and `*.lfr-demo.online`).
- **Unified Tunnel Provider Namespace**: Added `ldm share` subcommands (`start`, `status`, `stop`) to manage sharing tunnels under a single interface.
- **Automated Container Sharing**: Integrated `--share`, `--share-subdomain`, and `--share-provider` flags into `ldm run` to automatically boot the sharing tunnel once Liferay is healthy.
- **Expose Legacy Support**: Mapped the legacy `--expose` flag as a backward-compatible alias for `--share --share-provider ngrok`.

## [v2.11.4] - 2026-06-10

### Added

- **Directory Deletion Safety Validator**: Integrated JIT validation in `safe_rmtree` to prevent accidental deletion of git repository roots, system directories, CWD, user home directories, and LDM source files.
- **Ngrok Tunneling Integration**: Embedded Ngrok tunneling directly into the local LDM stack to facilitate seamless remote testing of client extensions (#27).
- **Secrets Prevention Scanner**: Configured pre-commit secrets detection with Yelp's `detect-secrets` hook and baseline configurations (#36).
- **OSGi State Persistence**: Added support for persisting OSGi state across container lifecycles (#28).

### Fixed

- **CWD FileNotFoundError Bug**: Resolved a CLI crash when running commands (like `ldm list`) inside a directory that has been deleted.
- **CSP Compliance**: Removed inline styles and `<style>` tags from the developer dashboard to prevent Content Security Policy violations (#37).

## [v2.11.4-pre.1] - 2026-06-04

### Added

-

## [v2.11.3] - 2026-06-04

### Added

- **Video Showcase**: Added a new video showcase to the documentation (`docs/showcase/`) featuring HTML5 video demonstrations of Fast Provisioning, Cloud Hydration, and Snapshots & Restoration with full text transcripts for SEO and accessibility (Fixes #21).

### Fixed

- **Port Allocation Conflict**: Resolved a bug in the proxy infrastructure where fallback logic could mistakenly assign the same available port (e.g., 1024) to multiple services (like HTTP and HTTPS) if the host's ports were already occupied and the first fallback port had not yet been bound (Fixes #21).

## [v2.11.2] - 2026-06-03

### Fixed

- **Windows PowerShell Input Prompt Hang**: Refactored `UI.ask` to use native `input(prompt)` on Windows (`sys.platform == "win32"`) with a clean ASCII-only fallback prompt, preventing console host queue blocking and allowing user inputs and Ctrl+C abort sequences to process correctly in PowerShell and cmd.exe.

## [v2.11.1] - 2026-06-03

### Fixed

- **Windows Console Unicode Output**: Added `isinstance(str)` guard to `UI._print` encoding pre-check to prevent `TypeError` when stdout is mocked, and ensures emoji symbols (e.g. `❌`) fall back to ASCII equivalents (`[X]`) on consoles that cannot encode UTF-8.
- **`system fix-hosts` CLI Registration**: Registered the missing `fix-hosts` subcommand under `system_subparsers` in the CLI parser. Previously, `ldm system fix-hosts --help` caused argparse to exit with code 2, failing the WSL2 E2E Sudo Guard verification.
- **SIGPIPE Broken Pipe Traceback**: Restored the default OS-level `SIGPIPE` signal handler (`SIG_DFL`) on Unix/macOS at startup. Python's override was causing `BrokenPipeError: [Errno 32] Broken pipe` tracebacks when `ldm` output was piped to tools like `grep -q` that exit early.
- **Pre-commit Hook Python Resolution**: Added `scripts/run_python.sh` portable resolver so pre-commit hooks use `.venv/bin/python3` when available (local dev) and fall back to system `python3` (CI), preventing hook failures caused by Homebrew Python taking priority on macOS.

## [v2.11.0] - 2026-06-03

### Added

- Proactive remote Liferay tag validation against the releases.json endpoint.
- Automated cleanup of remote pre-release tags upon pull request merge.

## [v2.11.0-pre.2] - 2026-06-03

### Added

- Documented branching and tagging rules in CONTRIBUTING.md.

## [v2.11.0-pre.1] - 2026-06-02

### Added

- **CLI Namespacing**: Restructured flat commands into logical namespaces (`ldm infra`, `ldm cloud`, `ldm config`, `ldm system`) for improved discoverability. All legacy flat commands remain fully supported as transparent aliases (e.g. `ldm prune` → `ldm system prune`).
- **`--open` switch on `ldm run`**: Automatically launches the project URL in your system browser after startup completes.
- **`--scale` switch on `ldm run`**: Boot a scaled multi-replica stack in a single command (e.g. `ldm run demo --scale liferay=2`), bypassing the separate `ldm scale` step.
- **`ldm logs --instance N` / `-i N`**: Target a specific replica of a scaled service directly (e.g. `ldm logs demo liferay --instance 2`). Routes to `docker logs` for exact container targeting.
- **Container naming pattern in metadata**: `ldm scale` now persists `container_name_pattern_{service}` to project metadata, enabling O(1) replica name resolution without a `docker ps` lookup.
- **Updated man page and CLI reference**: Full documentation of all namespaced commands, new switches, backward-compatibility table, and `--instance` usage guide.

## [v2.10.27] - 2026-06-02

### Changed

- Moved fine-grained inner-loop file sync, monitoring, archiving, and database wiping logs to `UI.detail` (visible only via `--verbose`/`-v`) to streamline CLI Developer Experience (DX).

## [v2.10.26] - 2026-06-02

### Added

- Added a dedicated third-party tool dependencies guide ([THIRD_PARTY_TOOLS.md](./docs/THIRD_PARTY_TOOLS.md)) detailing mandatory/optional status, purposes, and impacts of missing dependencies.

### Changed

- Deprecated legacy `nc`/`ncat` (netcat/nmap) diagnostic checks and retired related warnings and installation instructions, as log-level sync is now handled natively via Log4j2 file hot-reloading.

## [v2.10.25] - 2026-06-02

### Added

- **Liferay Cloud Golden Path Hydration**: Automated database extraction, flattening, SQL scrubbing, and volume synchronization.
- **PostgreSQL Restoration Hardening**: Complete wipe mechanism including Large Objects and UNIX socket retries, and high-performance streaming database import.
- **Self-Tuning JVM & Search Indexing**: Automated code cache and heap scaling during reindexing, and real-time indexing progress spinner.
- **Smart Store Detection**: Automatically detects and switches between FileSystemStore and AdvancedFileSystemStore configurations.
- **Smart Volume Naming**: Bypasses Docker Compose prefixing lag and naming mismatches via explicit Named Volume properties.
- **CI/CD Hardening**: Configured shellcheck using system binary to ensure stable GitHub Action runs.
- **Local Dev Hardening**: Added `/modern-intranet/` to gitignore to prevent accidental credential and database leaks.

## [v2.10.24] - 2026-06-01

### Added

-

## [v2.10.23] - 2026-06-01

### Added

-

## [v2.10.22] - 2026-06-01

### Added

-

## [v2.10.21] - 2026-06-01

### Added

-

## [v2.10.20] - 2026-06-01

### Added

-

## [v2.10.19] - 2026-06-01

### Added

-

## [v2.10.18] - 2026-06-01

### Added

-

## [v2.10.17] - 2026-06-01

### Added

-

## [v2.10.16] - 2026-06-01

### Added

-

## [v2.10.15] - 2026-06-01

### Added

-

## [v2.10.14] - 2026-06-01

### Added

-

## [v2.10.13] - 2026-06-01

### Added

-

## [v2.10.12] - 2026-06-01

### Added

-

## [v2.10.11] - 2026-06-01

### Added

-

## [v2.10.10] - 2026-06-01

### Added

-

## [v2.10.9] - 2026-06-01

### Added

-

## [v2.10.8] - 2026-06-01

### Added

-

## [v2.10.7] - 2026-06-01

### Added

-

## [v2.10.6] - 2026-06-01

### Added

-

## [v2.10.5] - 2026-06-01

### Added

-

## [v2.10.4] - 2026-06-01

### Added

-

## [v2.10.3] - 2026-06-01

### Added

-

## [v2.10.2] - 2026-06-01

### Added

-

## [v2.10.1] - 2026-06-01

### Added

-

## [v2.10.0] - 2026-05-31

### Added

-

## [v2.9.9] - 2026-05-31

### Added

-

## [v2.9.8] - 2026-05-31

### Added

-

## [v2.9.7] - 2026-05-31

### Added

-

## [v2.9.6] - 2026-05-31

### Added

-

## [v2.9.5] - 2026-05-31

### Added

-

## [v2.9.4] - 2026-05-31

### Added

-

## [v2.9.3] - 2026-05-31

### Added

-

## [v2.9.2] - 2026-05-31

### Added

-

## [v2.9.1] - 2026-05-31

### Added

-

## [v2.9.0] - 2026-05-31

### Added

-

## [v2.8.39] - 2026-05-29

### Added

-

## [v2.8.38] - 2026-05-28

### Added

-

## [v2.8.37] - 2026-05-28

### Added

-

## [v2.8.36] - 2026-05-27

### Added

-

## [v2.8.35] - 2026-05-27

### Added

-

## [v2.8.34] - 2026-05-27

### Added

-

## [v2.8.33] - 2026-05-27

### Added

-

## [v2.8.32] - 2026-05-27

### Added

-

## [v2.8.31] - 2026-05-27

### Added

-

## [v2.8.30] - 2026-05-27

### Added

-

## [v2.8.29] - 2026-05-27

### Added

-

## [v2.8.28] - 2026-05-27

### Added

-

## [v2.8.27] - 2026-05-27

### Added

-

## [v2.8.26] - 2026-05-27

### Added

-

## [v2.8.25] - 2026-05-27

### Added

-

## [v2.8.24] - 2026-05-27

### Added

-

## [v2.8.23] - 2026-05-27

### Added

-

## [v2.8.22] - 2026-05-27

### Added

-

## [v2.8.21] - 2026-05-27

### Added

-

## [v2.8.20] - 2026-05-27

### Added

-

## [v2.8.19] - 2026-05-27

### Added

-

## [v2.8.18] - 2026-05-27

### Added

-

## [v2.7.2-beta.40] - 2026-05-18

### Fixed

- **Enhanced Readiness Detection**: Updated `ldm run` and E2E scripts to monitor Liferay logs for the Tomcat "Server startup" marker. This provides a faster and more reliable signal that Liferay is ready for access, especially in CI environments where the Docker healthcheck status may be significantly delayed.
