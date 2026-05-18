# LDM Project Mandates

This file establishes the foundational mandates for Liferay Docker Manager development.

## 1. Hybrid Volume Strategy (macOS / ExFAT)

To resolve critical filesystem locking deadlocks (e.g., `Unable to create lock manager` or `access_denied_exception`), LDM MUST use a split-volume approach:

- **Named Docker Volumes**: MUST be used for directories requiring POSIX file locking.
  - `/opt/liferay/data`
  - `/opt/liferay/osgi/state`
- **Host Bind-Mounts**: SHOULD be used for directories facilitating developer hot-reloads.
  - `/mnt/liferay/deploy`
  - `/mnt/liferay/files`
  - `/mnt/liferay/scripts`
  - `/opt/liferay/osgi/modules`
  - `/opt/liferay/osgi/client-extensions`
  - `/opt/liferay/osgi/log4j`

## 2. Infrastructure Enforcement

- **Database**: Standardize on PostgreSQL with mandatory healthchecks.
- **Search**: Use shared Global Search (ES8) by default; support Sidecar fallback isolation.
- **Logging**: Force `LIFERAY_LOG4J2_CONFIGURATION_FILE` injection to guarantee hot-reload capability.

## 3. Reference Documentation

- [Architecture Guide](./docs/LDM_ARCHITECTURE.md)
- [Troubleshooting](./docs/TROUBLESHOOTING.md)
- [Agent Rules of Engagement](./.gemini/gemini.md)
