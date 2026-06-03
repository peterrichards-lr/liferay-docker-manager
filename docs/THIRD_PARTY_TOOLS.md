# LDM Third-Party Tool Dependencies

Liferay Docker Manager (LDM) is designed to run as a lightweight command-line utility to orchestrate local development environments. To avoid reinventing the wheel and to keep the codebase secure and standard, LDM delegates specific tasks (such as local SSL certificate generation, container management, and PaaS synchronization) to trusted, industry-standard third-party utilities.

To build trust and provide transparency to developers, this document details every third-party executable LDM interacts with, why it is required, and what features will degrade if it is missing.

---

## Dependency Matrix

| Tool | Status | Key Purpose | Impact if Missing |
| :--- | :--- | :--- | :--- |
| **[Python](https://www.python.org/)** | **Conditional** | Runs macOS/Linux standalone binaries (ZipApp format) and source installs. | LDM cannot run macOS/Linux standalone binaries (not required for Windows). |
| **[Docker Engine](https://www.docker.com/)** | **Mandatory** | Runs the containers for Liferay, DB, Search, and proxy. | LDM cannot start or manage any stack. |
| **[Docker Compose v2](https://docs.docker.com/compose/)** | **Mandatory** | Orchestrates multi-container topologies. | LDM cannot deploy, stop, or structure stacks. |
| **[mkcert](https://github.com/FiloSottile/mkcert)** | **Optional** | Generates trusted local Certificate Authority (CA) and wildcard SSL certificates. | Local virtual hosts will fall back to plain HTTP (`http://`) instead of HTTPS (`https://`). |
| **[openssl](https://www.openssl.org/)** | **Optional** | Performs cryptographic checks and verifies file formatting. | LDM cannot run diagnostic validation checks matching SSL certificates and keys. |
| **[telnet](https://en.wikipedia.org/wiki/Telnet)** | **Optional** | Opens an interactive connection to Liferay's OSGi Gogo Shell console. | The `ldm gogo` command will fail to connect. |
| **[lcp CLI](https://customer.liferay.com/downloads/-/download/liferay-cloud-cli)** | **Optional** | Retrieves DB and volume backups from Liferay Cloud (PaaS) environments. | Cloud golden-path integration (`ldm cloud-fetch` and automated environment imports) is disabled. |
| **[mysql / psql](https://www.mysql.com/)** | **Optional** | Host-side CLI clients to query PostgreSQL/MySQL. | No impact on LDM operations (LDM performs internal tasks like database scrubs via containerized clients using `docker exec`). Recommended only for host-side manual inspection. |
| **nc / ncat (Nmap)** | **Deprecated** | Historically checked for log-level synchronization. | **No impact**. Handled natively via Log4j2 file-based hot-reloading. |

---

## Detailed Tool Analysis

### 1. Docker & Docker Compose (v2)

* **Why it is needed**: LDM is an orchestrator. It does not run Liferay directly on the host machine; instead, it automatically structures a multi-container network containing the Liferay portal, database (PostgreSQL/MySQL), global search engine (Elasticsearch), and an ingress proxy (Traefik).
* **Security & Trust**: Runs entirely locally on your machine. LDM communicates with Docker via the standard unix socket (`/var/run/docker.sock`) or Windows Named Pipe.

### 2. mkcert (Local HTTPS)

* **Why it is needed**: Modern web browsers enforce strict security checks, and developing client extensions or OAuth2 configurations often requires secure `https://` origins. `mkcert` automates the complex task of creating a local CA and registering it in your machine's system trust stores, preventing "Not Secure" browser warning screens.
* **Optionality**: If not installed, LDM runs fine over standard HTTP port `8080` (or your configured port).
* **Trust Boundary**: LDM only calls `mkcert` locally. Certificates are stored in `~/liferay-docker-certs`.

### 3. OpenSSL

* **Why it is needed**: Used by the `ldm doctor` diagnostics layer to inspect generated public keys and certificates, ensuring they are valid and match each other.
* **Optionality**: If missing, LDM operations are unaffected, but `ldm doctor` cannot verify local SSL certificate pair integrity.

### 4. Telnet (OSGi Gogo Shell)

* **Why it is needed**: Liferay DXP exposes an interactive command-line interface for the OSGi container, known as the Gogo Shell. Developers use this shell to diagnose active bundles, check service bindings, and inspect runtime components. LDM maps this port (default `11311`) to localhost and spawns `telnet` to connect the developer directly to the terminal.
* **Optionality**: Without telnet, you cannot connect to the Gogo shell using the `ldm gogo` command.

### 5. Liferay Cloud CLI (`lcp`)

* **Why it is needed**: Facilitates PaaS cloud synchronization. LDM automates the "Golden Path" to let you run a local replica of a Liferay Cloud environment. It calls `lcp` to authenticate, discover backups, and stream SQL dumps and volume files down to the host.
* **Optionality**: Standard local-only projects do not require this. If missing, commands like `ldm cloud-fetch` will prompt you to install the CLI.

### 6. Python (Interpreter)

* **Why it is needed**: The Windows standalone binary is built with PyInstaller and contains its own bundled Python environment. However, to keep sizes lightweight (~1.2MB), the macOS and Linux binaries are packaged in **ZipApp (Shiv)** format, which requires a host-installed Python interpreter (version **3.10 or higher**).
* **Optionality**: Only optional on Windows. On macOS and Linux, you must have Python 3.10+ installed on the host system to run the standalone binaries or when running from source.

---

## Deprecated Dependencies

### Netcat (`nc` or `ncat` from Nmap)

* **History**: Originally, LDM checked for `nc` (on Linux/macOS) or `ncat` (which on Windows is distributed as part of the `Nmap` suite via `winget install Insecure.Nmap`) to send remote commands to Liferay's OSGi framework for dynamic logging configurations.
* **Alternative Implementation (Native Hot-Reload)**: LDM has migrated log-level synchronization to a **native file-based hot-reload mechanism**.
  * LDM writes configurations to a bind-mounted `portal-log4j-ext.xml` template.
  * The Log4j2 configuration is marked with `monitorInterval="5"`, which instructs the Liferay JVM to automatically poll and reload changes on the filesystem every 5 seconds.
* **Current Status**: **Fully Deprecated & Unused**. The dependency check for `nc/ncat` has been retired from the active `ldm doctor` warnings list. Windows developers no longer need to download or install `Insecure.Nmap` to use LDM.
