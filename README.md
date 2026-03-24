# Liferay Docker Scripts

A collection of professional automation tools for managing Liferay Portal and DXP instances using Docker. These scripts simplify container orchestration, configuration persistence, and snapshot/restoration workflows.

---

## Compatibility & Intended Use

The scripts in this repository are designed to work as an integrated suite:

- **Runner Script:** `run-liferay-dxp-docker.sh` creates a standardized project layout (e.g., `files/`, `data/`, `osgi/`, `deploy/`).
- **Management Scripts:** `liferay-docker.sh` (Python wrapper) provides a high-level manager for running, snapshoting, and restoring environments. Features a **24-hour Shared Cache** for Docker Hub tags.
- **Snapshot Scripts:** `create-docker-snapshot.sh` and `restore-docker-snapshot.sh` utilize the standardized layout to perform full database and filesystem backups.

---

## Tag Caching & Discovery

To ensure high performance, the scripts cache Docker Hub API responses in `~/.liferay_docker_cache.json`.

- **TTL:** 24 hours.
- **Refresh:** Use the `--refresh` flag to force a new fetch from Docker Hub.
- **Clear:** Run `./liferay-docker.sh clear-cache` to manually wipe the cache file.

---

## Liferay Home Folder Mapping

The scripts create a project root that mirrors **Liferay Home** and bind-mounts these folders into the container for persistence and easy configuration:

| Host Folder                     | Container Path                        | Purpose                                                 |
| :------------------------------ | :------------------------------------ | :------------------------------------------------------ |
| `<root>/data`                   | `/opt/liferay/data`                   | Persistent data (Document Library, Hypersonic DB, etc.) |
| `<root>/deploy`                 | `/mnt/liferay/deploy`                 | Auto-deploy drop-in for JARs, WARs, and Licenses.       |
| `<root>/files`                  | `/mnt/liferay/files`                  | Configuration overlay (e.g., `portal-ext.properties`).  |
| `<root>/osgi/state`             | `/opt/liferay/osgi/state`             | OSGi runtime cache; persisting this speeds up restarts. |
| `<root>/osgi/client-extensions` | `/opt/liferay/osgi/client-extensions` | Location for zip-deployable Client Extensions.          |
| `<root>/.certs`                 | N/A (Host only)                       | Locally generated SSL certificates and Traefik config.  |
| `<root>/backups`                | N/A (Host only)                       | Local snapshots created by the manager.                 |

---

### Automated Local SSL
When a custom `--host-name` is used (and `mkcert` is installed), the scripts automatically:
1.  **Generate Certificates**: Creates locally trusted SSL certificates in a `.certs/` folder.
2.  **Launch Traefik**: Starts (or reuses) a singleton **global Traefik proxy** (`liferay-proxy-global`) to handle HTTPS.
    *   **Shared Network**: All instances are connected via a shared `liferay-net` Docker bridge.
    *   **Port Priority**: The script attempts to bind to host port **443** first.
    *   **Fallback**: If port 443 is blocked (e.g., missing permissions), it offers to use port **8443**.
    *   **Sudo Hint**: Standard HTTPS (443) requires running the script with `sudo` on most systems.
3.  **Secure Routing**: Configures Liferay with the necessary labels for seamless TLS termination.

#### Proxy Visibility
You can inspect the global proxy state by checking its logs:
```bash
docker logs liferay-proxy-global
```
_Tip: This is the first place to look if you encounter a 404 or 502 error during SSL development._

---

## Cross-Platform Networking

The scripts are designed to be fully cross-platform (macOS, Windows, Linux) but have specific networking requirements for virtual hostnames:

### macOS (Intel & Silicon)
To use custom hostnames (e.g., `prospect.demo`), you must alias your loopback interface if you aren't using `127.0.0.1`.
*   **Requirement**: Run `sudo ifconfig lo0 alias <your-ip> up` for each unique IP.
*   **Docker Socket**: The script automatically detects the socket at `~/.docker/run/docker.sock` to handle M1/M2 permission issues.

### Windows
*   **Docker Socket**: Uses Named Pipes (`//./pipe/docker_engine`).
*   **Pathing**: The script automatically converts Path objects to POSIX format (`/`) to prevent backslash escapes from breaking Docker volume strings.

### Linux
*   **Docker Socket**: Defaults to `/var/run/docker.sock`.
*   **Permissions**: Ensure your user is in the `docker` group or run the script with `sudo`.

---

## Automated Health Checks

When starting an instance in background mode (default), the script will:
1.  **Monitor Readiness**: Poll the access URL until Liferay responds with a `200 OK` or `302 Found`.
2.  **Provide Feedback**: Display a progress loop while waiting (typically 2-5 minutes).
3.  **Explicit Readiness**: Notify you with a `READY!` message as soon as the instance is live.

---

## Prerequisites

- **Docker**: Docker Desktop or Docker Engine installed and running.
- **Python**: 3.10+ required for the management script.
- **mkcert**: (Optional) Required for automated local SSL termination.
- **Database Clients**: (Optional) If using PostgreSQL or MySQL, ensure the respective client (`psql` or `mysql`) is installed and available in your system PATH for snapshot/restore operations.

---

## Quick Start Examples

### 1. Run a Standard DXP Instance

Launch a Liferay DXP instance on the default port (8080) with Hypersonic:

```bash
./liferay-docker.sh run --tag 2025.q4.11
```

_Note: The container starts in the background. The script will provide direct `docker logs -f <name>`, access URL, and `docker rm -f <name>` commands upon successful startup._

### 2. Run a Liferay Portal Instance

Use the `--portal` flag to switch to the open-source Liferay Portal image:

```bash
./liferay-docker.sh run --portal
```

### 3. Running Multiple Isolated Instances

Use unique virtual hostnames and loopback IPs to run instances side-by-side on the **same port**:

```bash
# Instance A (on 127.0.0.1:8080)
./liferay-docker.sh run --host-name portal-a.local -p 8080

# Instance B (on 127.0.0.74:8080)
./liferay-docker.sh run --host-name portal-b.local -p 8080
```

_Note: Ensure your `hosts` file maps these domains to the respective loopback IPs. See [hosts.example](hosts.example)._

### 4. Create and Restore Snapshots

```bash
# Create a standard snapshot
./liferay-docker.sh snapshot --name "Pre-Upgrade Backup"

# View available snapshots
./liferay-docker.sh snapshots

# Restore the latest snapshot
./liferay-docker.sh restore
```

_Note: Snapshot and restore commands use **Smart Root detection**. If run outside a Liferay project, they will automatically scan subdirectories and offer an interactive selection of managed folders._

---

## Command Reference

### `run` command options

| Option               | Description                                                             | Default          |
| :------------------- | :---------------------------------------------------------------------- | :--------------- |
| `-t, --tag <tag>`    | Docker image tag (e.g., `2024.q1.5`).                                   | Latest available |
| `--portal`           | Use Liferay Portal (`liferay/portal`) instead of DXP.                   | DXP              |
| `-r, --root <path>`  | Project root directory.                                                 | `./<tag>`        |
| `--host-name <host>` | Virtual hostname (e.g., `liferay.local`). Enables session isolation.    | `localhost`      |
| `-p, --port <port>`  | Local HTTP port mapping.                                                | `8080`           |
| `--es-port <port>`   | Elasticsearch sidecar HTTP port.                                        | `9200`           |
| `--db <type>`        | Database type: `postgresql`, `mysql`, or `hypersonic`.                  | `hypersonic`     |
| `--disable-zip64`    | Disable JVM Zip64 extra field validation.                               | Enabled          |
| `--select`           | Browse and select from existing managed folders.                        | N/A              |
| `--refresh`          | Force refresh of the Docker Hub tag cache.                              | N/A              |
| `--ssl`              | Enable SSL with Traefik and mkcert (auto-enabled for custom hostnames). | N/A              |
| `--no-ssl`           | Explicitly disable SSL support.                                         | N/A              |
| `-f, --follow`       | Start container and automatically follow logs.                          | Background only  |
| `--remove-after`     | Automatically remove the container after it stops.                      | Off              |

---

## Advanced Features

### Virtual Host & Session Isolation

When using `--host-name`, the scripts automatically:

1. **Rename Cookies:** Sets a unique `SESSION_COOKIE_NAME` (e.g., `LFR_SESSION_ID_portal_a_local`) to prevent cross-instance logouts.
2. **Domain Scoping:** Configures Liferay to scope cookies to the specific virtual domain.
3. **Security Whitelisting:** Adds the hostname and resolved IP to `virtual.hosts.valid.hosts` to prevent 403 Forbidden errors.

### Proactive Collision Detection

The scripts perform "Fast-Fail" checks before starting a container to prevent environment corruption:

- **DNS/Binding Verification:** Ensures custom hostnames resolve to a bindable `127.x.x.x` loopback address.
- **Port Conflict Detection:** Detects if the requested HTTP or Elasticsearch port is already occupied on the **same loopback IP** by another running Liferay container.
- **Database Collision Detection:** Parses and normalizes JDBC URLs from `portal-ext.properties` to warn you if two instances are attempting to use the **exact same database schema** on the same server.

---

## SSL Setup (Green Lock)

To enable locally trusted HTTPS for your development instances:

1. **Install mkcert**: `brew install mkcert` (macOS) or `choco install mkcert` (Windows).
2. **Install Root CA**: Run `mkcert -install` to trust the local certificate authority.
3. **Use a Custom Hostname**: Provide a `--host-name` (e.g., `prospect.demo`) when running the script.

## Issues & Contributions

Notice a bug or have a feature request? We welcome contributions! Please report any issues on our GitHub tracker:
[https://github.com/peterrichards-lr/liferay-docker-scripts](https://github.com/peterrichards-lr/liferay-docker-scripts)

## License

MIT © Peter Richards
