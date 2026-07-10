# Liferay Docker Manager (LDM) Showcase

Seeing is believing. Check out these short demonstrations of Liferay Docker Manager in action to see how it can accelerate your development workflow.

## 🚀 Fast Provisioning

Watch how quickly LDM provisions a fully functional Liferay environment, complete with SSL, automatic host file mapping, and an instant fast login token generation.

<https://github.com/user-attachments/assets/8266af0f-f7a4-4040-91b1-f41cf2a28b39>

### Transcript: Fast Provisioning

1. The user executes `ldm run ldm-demo --fast-login --ssl` to provision a new environment.
2. LDM interactively prompts for the virtual hostname, defaulting to `ldm.demo`.
3. Missing host entries are detected, and LDM automatically adds `ldm.demo` to the `/etc/hosts` file (requesting `sudo` permissions).
4. The user selects the Liferay release version (e.g., `2026.q1.7-lts`).
5. LDM downloads the pre-warmed database seed for this version.
6. The project is bootstrapped directly from the seed, instantly hydrating the local volumes with pre-configured OSGi state and data.
7. Local SSL certificates are generated for `ldm.demo` using `mkcert`.
8. The Docker Compose stack is started, and LDM tails the logs until the server is healthy.
9. The instance becomes accessible at `https://ldm.demo`, where the user logs in and easily creates a new site using a template.

---

## 📸 Snapshots & Restoration

Accidentally broke your environment? Watch how easy it is to list existing database and volume snapshots, and instantly roll back to a known good state.

<https://github.com/user-attachments/assets/7322c675-2c23-4f61-b0f2-f1d169e8a951>

### Transcript: Snapshots & Restoration

1. The user executes `ldm restore ldm-demo --list` to view all available local snapshots for the project.
2. LDM displays the available snapshots, revealing a "gold-standard-demo" snapshot at index `[1]`.
3. The user initiates the rollback by executing `ldm restore ldm-demo --index 1`.
4. LDM safely tears down the currently running `ldm-demo` stack and cleans all corrupted data, logs, and state.
5. The snapshot's integrity is verified before the restoration begins.
6. Local volumes are hydrated from the snapshot archive, and an orchestrated database restore is triggered.
7. Once the database is successfully restored, LDM prompts the user to restart the project.
8. The infrastructure and Liferay containers spin back up.
9. The environment is fully reverted, allowing the user to browse the restored site exactly as it was when the snapshot was captured.

---

## ☁️ Cloud Hydration

See how seamlessly LDM pulls backups directly from Liferay Cloud environments (like `prd` or `uat`) and restores them locally, giving you an exact replica for debugging.

<https://github.com/user-attachments/assets/3bf7e8e6-6740-4a6c-a350-28e44c9d5bde>

### Transcript: Cloud Hydration

1. The user executes `ldm --cloud-project lctmodernintranetpsql --host-name intranet.demo --hydrate-from prd --no-env-sync`.
2. LDM scans the cloud workspace and automatically fetches the latest database and volume backups from the production (`prd`) environment.
3. The backups are downloaded and organized into local snapshots, with PostgreSQL automatically detected as the database type.
4. LDM triggers a local restore, tearing down any existing stack and scaffolding the Docker environment.
5. Cloud data volumes are unpacked and internal Docker volumes are hydrated.
6. The cloud database dump is decompressed and scrubbed of any cloud-specific meta-commands.
7. An orchestrated database restore wipes the existing local schema and securely imports the production data.
8. Virtual host entries are synchronized to `intranet.demo`.
9. The environment starts up, and the user can browse their locally running, exact replica of the production Intranet site at `https://intranet.demo`.

---

## ⚡ OSGi State Persistence

Minimize boot times on local development environments by persisting the OSGi bundle resolution state across container lifetimes.

See the [OSGi State Persistence Performance Showcase](OSGI_STATE_PERSISTENCE.md) for timing comparisons, speed improvement metrics, and architectural details.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-10* | *Last Reviewed: 2026-07-02*
