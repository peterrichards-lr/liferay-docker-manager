# CLI System Commands

## `system doctor` (legacy: `doctor`)

Verify host environment health, Docker resources (CPUs/Memory), disk space (warns on dangling volumes), and project dependencies. Includes checks for required tools: `mkcert`, `telnet`, `nc`, `lcp`, `lfr-tunnel`, and the Docker Compose V2 plugin.

The `lfr-tunnel` row reports the client's version and the date it was last
installed or self-upgraded, for whichever binary `ldm share` would use --
`LDM_LFR_TUNNEL_BIN`, the `lfr_tunnel_bin` setting, or `PATH`. The date comes
from the file itself, because `lfr-tunnel -version` reports no build date.
The check is offline and does not compare against the gateway's minimum
version; a client below that floor is explained when `ldm share` runs. A
missing client is a warning, not a failure -- sharing is optional, and the
containerised `lfr-tunnel-docker` provider needs no host binary.

For every registered remote target, `doctor` also compares the SSH user in
`~/.ldmrc` against the user that node's Docker context actually dials, and
reports a `DRIFTED` row when they disagree (LDM-#1797). The two are stored
separately and nothing keeps them in step, so the failure shows up as
`Permission denied (publickey)` naming a user `ldm target ls` never mentions.
The repair it names is `ldm target add <name> --user <user>`, which keeps the
node's other settings.

```bash
ldm system doctor          # Health check for current/selected project
ldm system doctor --all    # Batch validate every project in your workspace
ldm system doctor --detailed  # Show detailed troubleshooting hints and automatic fixes
ldm system doctor --fix       # Automatically apply recommended fixes
ldm system doctor --bundle    # Generate a sanitized zip bundle of logs and config
ldm system doctor --slug      # Output a machine-readable environment identifier string
ldm system doctor --fix-hosts # Add missing domains to /etc/hosts (will prompt for sudo)
ldm system doctor --ssl       # Run SSL diagnostic workflow to verify certificate chains and routing
ldm system doctor --ssl --domain aica.local # Check a specific domain

# Legacy flat form (still works):
ldm doctor --fix
```

## `system fix-hosts` (legacy: `fix-hosts`)

Manually append missing project hostnames to your system's `/etc/hosts` file. This command is automatically triggered by `ldm run` if a resolution failure is detected, but can also be called surgically.

```bash
# Fix all hostnames for a project (including extension subdomains)
ldm system fix-hosts my-project

# Add a specific raw hostname
ldm system fix-hosts custom.local

# Run a full fix for all projects via doctor
ldm system doctor --fix-hosts

# Legacy flat form (still works):
ldm fix-hosts my-project
```

## `system seeds`

List available pre-warmed database seeds from GitHub.

```bash
ldm system seeds
```

> [!NOTE]  
> **Seed Retention Policy:** LDM only maintains one pre-warmed seed per quarterly release (the absolute latest patch). When a new patch is released, the previous seed is deleted to conserve storage and reduce confusion.

## `wait` (Readiness Gating)

Blocks execution until a Liferay instance is genuinely ready for work. This is highly recommended for CI/CD pipelines and complex deployment scripts. Unlike basic Docker healthchecks, `ldm wait` performs a **3-Phase Verification**:

1. **Log Readiness**: Scans Docker logs for the Tomcat `"Server startup"` marker.
2. **HTTP Availability**: Polls the instance until it responds with an `HTTP 200` or `302` on its primary port.
3. **CPU Idle State**: Actively monitors the container's CPU usage, blocking until it drops below the threshold (default: 15.0%) for consecutive checks (default: 3). This is configurable via `--cpu-idle-threshold` and `--cpu-idle-checks` options.

```bash
# Wait for the current project to be fully idle (up to 10 minutes)
ldm wait

# Wait for a specific project with a custom timeout
ldm wait my-project --timeout 300
```

## `status` (alias: `ps`)

Lightweight summary of all active global services and running projects.

```bash
ldm status          # Show active global services and running projects
ldm status --all    # Show all managed projects (including stopped ones)
ldm ps

# Machine-readable output for scripts/automation, instead of the color-coded
# tables -- a stable JSON object: {"infrastructure": [...], "projects": [...]}.
# Combines with --detailed (per-container service/status/ports/image) and -a/--all.
ldm status --json
```

## `info`

Displays a user-friendly, formatted view of a project's internal metadata (`.liferay-docker.meta`). This is incredibly useful for diagnosing configuration issues or verifying project state without opening the file manually.

```bash
ldm info [project]
ldm info [project] --credentials  # Print the default admin credentials block
ldm info [project] --credentials --credential-type database  # Print the database credentials
ldm info [project] --credentials --password-only  # Print ONLY the raw password string (useful for CI scripts: `export PASS=$(ldm info --credentials --password-only)`)
```

The output includes an **`ID:`** row -- the project's internal UUID (LDM-#1393).
LDM identifies projects by this, not by their name: the name is yours to choose
and can legitimately be shared by two projects in different directories, so it
cannot be a reliable identity.

You will not see it anywhere else. `ldm list` and every routine command work in
project names, because that is how you think about them. It appears here because
this is the diagnostic view, and it is the value stamped on every container and
volume as `com.liferay.ldm.project.uuid` -- so it is what to match when working
out which Docker resources belong to which project:

```bash
docker ps --filter "label=com.liferay.ldm.project.uuid=<id from ldm info>"
```

Projects created before this existed have no ID, and the row is simply omitted.
One is assigned the next time their metadata is written.

> [!NOTE]
> **Security Posture regarding `ldm info --credentials`:**
> This feature intentionally outputs clear-text credentials to stdout. Because LDM is a local development and CI tool, the caller already has read access to the local `.liferay-docker.meta` file on disk. Providing a native extraction method encourages developers to dynamically source passwords in automated integration scripts, rather than hardcoding sensitive data into version control (which is significantly riskier).

## `browser` (alias: `open`)

Launch the project URL in your system browser. If no project is specified, LDM will present a list of currently running projects to select from.

```bash
ldm browser [project]
ldm open [project]
ldm browser [project] -u /path # Open a specific path (e.g., /web/guest)
ldm browser --list             # List available URLs without opening
ldm browser --remove           # Remove saved custom URLs from history
```

## `system upgrade` (legacy: `upgrade`)

Automatically download and install the latest version of LDM for your architecture. Includes integrity verification. If the automatic process fails, LDM will provide a manual `curl` or `PowerShell` command to complete the installation.

> [!TIP]
> **Post-Upgrade Release Notes**: After a successful upgrade, LDM will automatically display a "What's New" release notes banner summarising the key changes in the new version. This is shown once on the next command run after the upgrade completes. ![Added in v2.15.16](https://img.shields.io/badge/Added%20in-v2.15.16-blue)

```bash
ldm system upgrade                   # Standard upgrade to latest stable
ldm system upgrade --pre-release     # Upgrade to the latest pre-release/beta
ldm system upgrade --repair          # Re-download current version to fix integrity issues
ldm system upgrade --check           # Check for updates without installing

# Legacy flat form (still works):
ldm upgrade --beta
```

## `system completion` (legacy: `completion`)

Configure shell autocompletion for `ldm`. Supports **Bash**, **Zsh**, **Fish**, and **PowerShell**.

```bash
ldm system completion           # Auto-detect your shell
ldm system completion zsh       # Generate setup for Zsh specifically
ldm system completion bash      # Generate setup for Bash
ldm system completion fish      # Generate setup for Fish

# Legacy flat form (still works):
ldm completion
```

**Setup Summary:**

1. Run `ldm system completion` to get the command for your shell.
2. Add the provided command to your shell profile (`.zshrc`, `.bashrc`, or `config.fish`).
3. Restart your terminal.

This enables TAB completion for all commands, namespaces, subcommands, and project names.

## `system man` (legacy: `man`)

Display the comprehensive manual page for LDM. This provides an offline reference for all commands, options, and architecture details.

```bash
ldm system man

# Legacy flat form (still works):
ldm man
```

### Native Integration (`man ldm`)

To support the native system `man ldm` command, add this to your shell profile (`.zshrc` or `.bashrc`):

```bash
export MANPATH="$MANPATH:$HOME/.ldm/man"
```

## `infra renew-ssl` (legacy: `renew-ssl`)

Refresh project-specific SSL certificates immediately.

```bash
ldm infra renew-ssl           # Interactive selector
ldm infra renew-ssl demo      # Renew for 'demo' specifically
ldm infra renew-ssl --all     # Renew certificates for every project

# Legacy flat form (still works):
ldm renew-ssl demo
```

## `infra init-common` (legacy: `init-common`)

Initialize or recreate the baseline global configuration (`common/` folder) from internal resources.

```bash
ldm infra init-common

# Legacy flat form (still works):
ldm init-common
```

## `infra setup` / `infra down` / `infra restart` (legacy: `infra-setup`, `infra-down`, `infra-restart`)

Independently manage global infrastructure services (Traefik proxy, Search sidecar, Bridge).

```bash
ldm infra setup            # Start global services manually
ldm infra setup --search   # Also initialize the Global Search container
ldm infra setup --es7      # Force Global Search to use legacy Elasticsearch 7
ldm infra setup --ssl-port 8443 # Custom SSL port for Traefik proxy (default: 443 or LDM_SSL_PORT)
ldm infra setup --force-recreate # Force recreate proxy containers to re-apply ports
ldm infra down             # Stop and remove global services
ldm infra restart          # Reset all global services in one go
ldm infra restart --search # Restart and also initialize/restart Global Search
ldm infra restart-proxy    # Restarts only the Traefik proxy container

# Legacy flat forms (still work):
ldm infra-setup --search
ldm infra-down
ldm infra-restart
```

> [!TIP]
> **Sidecar Fallback**: If the Global Search (ES8) container is not running, `ldm` will automatically default to Liferay's internal **Sidecar** search. It also cleans up global ES configurations in your project to ensure the Sidecar initializes correctly.

## `infra migrate-search` (legacy: `migrate-search`)

Migrates a project from using the internal Sidecar search to the shared **Global Search container**.

```bash
ldm infra migrate-search [project]

# Legacy flat form (still works):
ldm migrate-search [project]
```

**What it does:**

1. Verifies the project is stopped.
2. Ensures the Global Search container is running (offers to start it).
3. Deletes internal indices (`data/elasticsearch7` or `data/elasticsearch8`).
4. Re-syncs Global ES configurations from `common/`.
5. Offers to restart the project immediately.

## `system prune` (legacy: `prune`)

Identify and reclaim disk space by safely removing orphaned resources. This command scans your Docker environment for containers and global search snapshots that no longer have a matching project folder on your disk, as well as cleaning up temporary files, large asset caches, and (with `--images`) unused Docker images and build cache. If `ldm system doctor` warns you about low disk space, run this with `--all`.

```bash
ldm system prune
ldm system prune --seeds --samples   # Also clear large pre-warmed asset caches
ldm system prune --images            # Also reclaim unused Docker images and build cache
ldm system prune --all               # Run all pruning operations without asking (includes --images)
ldm system prune --clean-hosts       # Remove all LDM-tagged entries from /etc/hosts

# Legacy flat form (still works):
ldm prune --seeds --samples
```

**What it cleans:**

- **Orphaned Containers**: Any container with the `com.liferay.ldm.managed` label whose project folder was manually deleted.
- **Orphaned Search Snapshots**: Leftover Elasticsearch 8.x snapshots in the global vault from deleted projects.
- **Pre-warmed Seeds**: (Optional) Large Database + Search + OSGi state archives used for instant project initialization.
- **Sample Extensions**: (Optional) Cached sample client extensions.
- **Docker Images & Build Cache**: (Optional, `--images`) Unused/dangling Docker images and unused build cache -- usually the single biggest reclaimable consumer on a long-lived dev machine, via `docker image prune -af` and `docker builder prune -af`.
- **Temporary Files**: Residual `.*.tmp` files left behind by interrupted sync or build operations.
- **Legacy LDM Volumes**: (Optional, `--legacy-volumes`) Named volumes created before LDM labelled volumes with their owning project. Docker only labels a volume at creation, so these never acquire labels and are matched by name pattern instead. Because a name is weaker evidence of ownership than a label, every candidate is listed and grouped by project, volumes belonging to a registered project are excluded, and confirmation is always interactive -- `--all` does not cover it.

## `clear-cache`

Clears the local Docker Hub tag cache. LDM caches Liferay tags for 24 hours to improve performance; use this command to force a fresh fetch from the registry.

```bash
ldm clear-cache
```

## `system relocate`

Safely move your LDM global configuration, Docker volumes, and cached assets to an external drive (e.g., an external SSD). This is highly recommended for macOS users to save internal disk space and bypass filesystem locking issues.

```bash
ldm system relocate /Volumes/SanDisk
```

## `config` (get / set / remove)

View or set generic custom environment variables inside a project's metadata. The `config` command now has explicit subcommands for clarity, while the legacy positional form is still supported.

```bash
# New namespaced form:
ldm config get MY_VAR           # Get a project-level variable
ldm config set MY_VAR "value"   # Set a project-level variable
ldm config remove MY_VAR        # Remove a variable

# Legacy positional form (still works):
ldm config MY_VAR "value"       # Detected as 'set'
ldm config MY_VAR --remove      # Detected as 'remove'
```

## `config defaults` (legacy: `defaults`)

View or manage LDM's Cascading Configuration Defaults. This system resolves settings (like the default DB type, search mode, or host name) using a hierarchy: Convention -> Global -> User -> Project.

```bash
# View the resolved configuration tree and their sources
ldm config defaults

# Set a custom default just for your local user (~/.ldmrc)
ldm config defaults db_type mysql

# Remove a local user default to fall back to the convention
ldm config defaults --remove db_type

# Set a system-wide global default (requires permissions, writes to /etc/ldmrc)
sudo ldm config defaults port 9090 --global

# Legacy flat form (still works):
ldm defaults db_type mysql
```

---

## Backward Compatibility Reference

All legacy flat-form commands are automatically translated to their namespaced equivalents by the `preprocess_args` layer. Both forms are valid and permanent:

| Legacy Command | New Canonical Form |
| :--- | :--- |
| `ldm init-from` | `ldm link` |
| `ldm prune` | `ldm system prune` |
| `ldm doctor` | `ldm system doctor` |
| `ldm upgrade` | `ldm system upgrade` |
| `ldm completion` | `ldm system completion` |
| `ldm man` | `ldm system man` |
| `ldm fix-hosts` | `ldm system fix-hosts` |
| `ldm dev-setup` | `ldm system dev-setup` |
| `ldm infra-setup` | `ldm infra setup` |
| `ldm infra-down` | `ldm infra down` |
| `ldm infra-restart` | `ldm infra restart` |
| `ldm init-common` | `ldm infra init-common` |
| `ldm renew-ssl` | `ldm infra renew-ssl` |
| `ldm migrate-search` | `ldm infra migrate-search` |
| `ldm cloud-fetch` | `ldm cloud fetch` |
| `ldm env` | `ldm config env` |
| `ldm feature` | `ldm config feature` |
| `ldm log-level` | `ldm config log-level` |
| `ldm edit` | `ldm config edit` |
| `ldm defaults` | `ldm config defaults` |
| `ldm init-from` | `ldm link` |
| `ldm start` | `ldm db start` |

---

## All CLI Options Reference

The following is a comprehensive index of all registered CLI option flags and their descriptions:

- **`--archetype`**: Apply an Extensible Stack Archetype (e.g. 'keycloak-sso', 'clustered')
- **`--ascii`**: Enable ASCII-safe output translation.
- **`--auto-install-lfr-tunnel`** *(deprecated)*: LDM no longer downloads a
  client. The flag still parses and still means "do not prompt", so it
  continues to work with a `lfr_tunnel_install_cmd` configured in `~/.ldmrc`;
  without one it warns and explains how to install the client.

LDM resolves an existing `lfr-tunnel` client rather than installing one, in
this order:

1. `LDM_LFR_TUNNEL_BIN`
2. `lfr_tunnel_bin` in `~/.ldmrc`
3. anywhere on `PATH`
4. `~/liferay/lfr-tunnel/lfr-tunnel` -- the location endpoint protection is
   configured to allow (`lfr-tunnel.exe` on Windows)
5. `~/.ldm/bin/lfr-tunnel` -- where LDM used to install its own copy. Still
   resolved so existing setups keep working, but never written to again.

Every candidate is resolved to its real path (symlinks included) before it is
probed or executed, so a legacy `~/.ldm/bin/lfr-tunnel` that is itself a
symlink to the whitelisted location is invoked by that resolved, whitelisted
path -- not by the unresolved, non-whitelisted symlink.

If no client is found, LDM explains how to install one and points at the
containerised `lfr-tunnel-docker` provider, which needs no host binary.

- **`--background`**: Run dashboard in background.
- **`--backup-dir`**: Directory path to backup archives.
- **`--benchmark`**: Display performance benchmark on execution.
- **`--build-info`**: Inject build metadata into the source.
- **`--bump`**: Increment the version logically.
- **`--clone-only`**: Force cloning the Git repository instead of downloading the LDM package (.ldmp).
- **`--clear-lock`** ![Added in v2.15.17](https://img.shields.io/badge/Added%20in-v2.15.17-blue): Clear the stale concurrency project lock for the specified project and exit.
- **`--container`**: Show detailed Docker container diagnostic checks.
- **`--docker`**: Show detailed Docker diagnostic checks.
- **`--domain`**: Custom domain prefix (e.g. lfr-demo.online, lfr-demo.se).
- **`--download`**: Force downloading of dependencies.
- **`--dry-run`**: Preview execution without mutations.
- **`--files-only`**: Extract or backup files/folders only.
- **`--force-boot`**: Restart the container immediately to apply the scheduled reindex, without prompting.
- **`--force-downgrade`**: Force a version downgrade (bypassing safety validations).
- **`--grep`**: Grep search pattern for filtering log lines.
- **`--grep-i`**: Case-insensitive grep search.
- **`--grep-v`**: Inverted grep search (select non-matching lines).
- **`--hydrate-from`**: Automatically hydrate data from a Liferay Cloud environment.
- **`--image`**: Custom Docker image to use for the sharing tunnel sidecar.
- **`--index`**: Force indexing check.
- **`--inspector`**: Expose the lfr-tunnel local inspector dashboard on port 4040.
- **`--keep-config`**: Retain global config file ~/.ldmrc.
- **`--keep-last`**: Keep only the specified number of most recent snapshots.
- **`--latest`**: Restore the most recent snapshot.
- **`--leave-running`** ![Added in v2.11.34](https://img.shields.io/badge/Added%20in-v2.11.34-blue): Keep the running project active and abort the import if it is currently running.
- **`--legacy-volumes`**: Also offer LDM volumes created before ownership labelling existed. Always interactive, never covered by `--all`.
- **`--list-backups`**: List backups in project work-folders.
- **`--list-envs`**: List all cloud environments.
- **`--logs`**: Stream container logs.
- **`--name`**: Specify target name.
- **`--no-color`**: Disable ANSI color codes in output.
- **`--no-env-sync`**: Skip syncing environment variables from Liferay Cloud.
- **`--no-home-warn`**: Suppress warning when running LDM from the root of the user's home directory.
- **`--tunnel-managed-cors`**: Skip local CORS patching and defer entirely to the tunnel gateway's dynamic header injection.
- **`--no-move`**: Skip moving existing data (just create symlinks).
- **`--no-restart`**: Do not automatically stop and restart the containers.
- **`--no-run`**: Update the metadata without automatically restarting the stack.
- **`--no-unicode`**: Disable Unicode characters in output and force ASCII safe-replacements.
- **`--older-than`**: Delete snapshots older than the specified number of days.

## `tray`

Launch the cross-platform LDM System Tray GUI application to monitor runtime container health, view active URLs, and launch/stop instances.

```bash
ldm tray                        # Launch System Tray GUI
ldm tray --autostart            # Install launch-on-login autostart
ldm tray --uninstall-autostart  # Remove launch-on-login autostart
```

---

## Global and Subcommand Flags Reference

- **`--all`**: Apply command across all managed projects.
- **`--autostart`** / **`--install-autostart`**: Install System Tray application to launch automatically on user login.
- **`--bundle`**: Generate a sanitized zip bundle of logs and config.
- **`--credentials`**: Print admin or service credentials.
- **`--cpu-idle-checks`**: Number of consecutive checks required to verify Liferay is idle (default: 3). ![Added in v2.15.26](https://img.shields.io/badge/Added%20in-v2.15.26-blue)
- **`--cpu-idle-threshold`**: CPU percentage threshold below which Liferay is considered idle (default: 15.0). ![Added in v2.15.26](https://img.shields.io/badge/Added%20in-v2.15.26-blue)
- **`--detailed`**: Show detailed diagnostic information.
- **`--domain`**: Specify target domain for diagnostics or routing.
- **`--download`**: Download asset or seed.
- **`--dry-run`**: Print actions without executing them.
- **`--fix`**: Automatically apply recommended fixes.
- **`--fix-hosts`**: Add missing domains to `/etc/hosts`.
- **`--force`**: Force operation without prompting.
- **`--format`**: Specify output format (`json`, `table`, `csv`).
- **`--fragment-patch-timeout`** (`wait`): Per-loop seconds to poll the headless-admin-site API for OSGi/Site Initializer readiness before patching fragment overrides (default: 300, or 900 when auto-detected on an external drive). Runs after — and additive to — `wait`'s own `--timeout`; also overridable via `LDM_FRAGMENT_PATCH_TIMEOUT`. ![Added in v2.15.27](https://img.shields.io/badge/Added%20in-v2.15.27-blue)
- **`--global`**: Apply setting globally.
- **`--help`**: Display CLI help menu.
- **`--index`** / **`-i`**: Select project by 1-based index from list. ![Added in v2.15.22](https://img.shields.io/badge/Added%20in-v2.15.22-blue)
- **`--leave-running`**: Leave workspace containers running after operation.
- **`--list`**: List items or URLs.
- **`--no-ssl`**: Bypass local SSL/mkcert generation and use HTTP.
- **`--non-interactive`**: Run in non-interactive mode.
- **`--output`**: Directory path to save generated output.
- **`--overwrite-registry`**: Automatically overwrite existing project registry entries.
- **`--ports`**: Comma-separated ports to expose.
- **`--print`**: Output current version string only.
- **`--project`**: Show detailed Project diagnostic checks.
- **`--project-id`**: Specific project ID to target.
- **`--default`**: Set target compute node as active global default.
- **`--force-recreate`**: Force recreation of Docker containers.
- **`--host`**: Host IP address or SSH connection string for target compute node.
- **`--key`**: Path to SSH key identity file for target compute node.
- **`--promote`**: Promote current pre-release to stable.
- **`--provider`**: Tunnel provider (defaults to lfr-tunnel).
- **`--quiet`**: Quiet mode (suppress info logs).
- **`--reboot`**: Force container reboot instead of runtime reindexing.
- **`--reindex`**: Force full search reindex on startup.
- **`--reset`**: Reset cumulative ROI metrics back to zero.
- **`--restore`**: Restore project backup/snapshot.
- **`--service`**: Specify container service.
- **`--set`**: Directly set version string.
- **`--share-domain`**: Custom domain for sharing.
- **`--share-image`**: Custom Docker image for sharing sidecar.
- **`--share-inspector`**: Expose local inspector dashboard on port 4040.
- **`--share-provider`**: Sharing provider to use.
- **`--status`**: Check status without performing upgrade.
- **`--stop-running`**: Automatically stop running project.
- **`--subdomain`**: Custom subdomain prefix.
- **`--sync-env`**: Sync configuration env vars.
- **`--system`**: Show detailed system diagnostic checks.
- **`--tail`**: Number of lines to show from end of logs.
- **`--timestamps`**: Show timestamps.
- **`--token`**: CSRF/Authentication token for dashboard mutations.
- **`--trigger`**: Event trigger for workflow.
- **`--tui`**: Launch interactive terminal configuration menu.
- **`--uninstall-autostart`**: Remove System Tray launch-on-login autostart.
- **`--up`**: Automatically start project after reseeding.
- **`--url`**: Remote packages download URL.
- **`--user`**: SSH user name for target compute node connection.
- **`--version`**: Target specific version of LDM.
- **`--wait-for-bundles`**: Comma-separated list of expected OSGi bundle symbolic names.
- **`--wait-for-deployables`**: Scan workspace for JARs/YAMLs and block until deployed.
- **`--stream-status`**: Stream Liferay startup milestones to stdout in real-time.
- **`--stream-logs`**: Stream raw Docker container logs to stdout in real-time.
- **`--workflow-name`**: Name of workflow file.
- **`-V`**: Show LDM version.
- **`-q`**: Quiet mode.

## `target` (Multi-Node Compute Node Management)

Manage local and remote compute target nodes for multi-node execution, live workload migration, and remote container orchestration:

```bash
ldm target add win-wsl --host 192.168.1.50 --user developer --key ~/.ssh/id_rsa --default
ldm target ls
ldm target use win-wsl
ldm target status win-wsl
ldm target set win-wsl
ldm target migrate local win-wsl
ldm target rm win-wsl
```

### `target add` adds **or updates** -- omitted flags are kept

Running `target add` against a name that already exists **merges** onto the
stored node: any flag you do not pass keeps its stored value (LDM-#1797).

```bash
ldm target add aws-1 --user ec2-user     # host, key, MAC pin and default kept
```

It used to replace the node wholesale, so the obvious repair for a wrong SSH
user reported *"registered successfully"* and silently erased the node's
`--mac-address`, producing the activation failure described below.

Clearing a value is still possible -- an absent flag and a flag passed with an
empty value are different inputs:

```bash
ldm target add aws-1 --mac-address ""    # removes the pin
ldm target add aws-1 --user ""           # removes the SSH user
```

`--default` is the exception: a `store_true` flag has no empty spelling, so
`target add` can only set it. Use `ldm target use <name>` to move the default.

An update reports what it changed and what it kept:

```text
✅  Target node 'aws-1' (13.49.210.78) updated.
ℹ  Changed SSH user: ldm-automation -> ec2-user
ℹ  Docker context 'aws-1' rebuilt to dial ssh://ec2-user@13.49.210.78.
ℹ  Kept host 13.49.210.78, MAC pin 06:ff:c5:f9:cf:a5 (not passed).
```

An update that moves nothing reports `stored settings unchanged` rather than a
bare success -- and still names the Docker context it rebuilt, since a remote
`target add` always rebuilds one.

### `--mac-address` (licence activation on remote nodes)

Liferay's licence binds to a MAC address. On a remote node the container takes
a bridge-assigned address, validation fails, and the portal serves the **DXP
Activation page** instead of the Sign In form -- with a healthy container and
`License registered` in the log, so nothing looks wrong.

```bash
ldm target add aws-1 --host 10.0.0.9 --user ec2-user \
  --mac-address 06:d0:95:e5:26:a7
```

Use the node's own primary NIC address (the one the licence allows). LDM does
not infer it: a node typically shows `ens5` beside `docker0` and `br-*`, all
plausible and only one licensed, and a wrong value produces the failure above.
Re-run `ldm target add` with the same name to change it -- and see above: the
re-run keeps every other setting you do not pass.

`target add` reads the node's interfaces over SSH and **warns** when the value
you gave matches none of them, naming each one so you can pick the right one:

```text
⚠️  MAC 02:42:de:ad:be:ef matches none of 'aws-1's interfaces
    (docker0 02:42:11:22:33:44, ens5 06:1a:2b:3c:4d:5e).
```

It warns rather than refuses, and records the value as given: Liferay validates
the *container's* MAC against the licence and does not care what the host's
interfaces are, so a licence bound to a MAC that is not a current NIC is
legitimate, if unusual. A node that cannot be reached says nothing at all --
unable to ask is not the same as wrong (LDM-#1780).

The check is one SSH round trip, and `target add` announces it because
registering a node that is currently stopped is legitimate and pays the full
connect timeout there -- measured 6s to 16s against an unroutable host:

```text
ℹ  Checking 'aws-1's interfaces for the pinned MAC (up to 10s if the node is
   unreachable)...
```

No `--mac-address`, no round trip and no line.

LDM re-reads the MAC from the running container and **refuses** when it does
not match, because writing the value into the compose file is a request rather
than a guarantee. The MAC can only be set at container creation, so a changed
value requires a recreate.

See `docs/explanation/remote-node-architecture.md` for the measurement and the
bridge-only safety caveat.

### `target migrate`

Migrate a running project workload live between target compute nodes with zero manual data loss:

```bash
ldm target migrate local win-wsl
ldm target migrate win-wsl aws-1
```

*(Performs pre-flight target probe -> creates live snapshot on source -> stops source stack -> syncs workspace via `rsync` -> reassigns project target metadata -> starts workload on destination node).*

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-09-20* | *Last Reviewed: 2026-09-20*
