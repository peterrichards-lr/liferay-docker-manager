# Implementation Plan: External Database Integration

This plan outlines the design and implementation steps for connecting local LDM instances to external database engines.

---

## 1. CLI Integration

Update `ldm_core/cli.py` to accept `external` as a valid choice under the `--db` parameter:

```python
parser.add_argument(
    "--db",
    choices=["postgresql", "mysql", "hypersonic", "external"],
    default="postgresql",
    help="Database engine to use"
)
```

---

## 2. Interactive DB Prompt Wizard

If `--db external` is selected, LDM launches an interactive prompt wizard inside `ldm_core/handlers/composer.py` or the initialization command:

### Prompts

1. **Database Type**: Select from `PostgreSQL`, `MySQL`, `Oracle`, `SQL Server`.
2. **JDBC Host**: (e.g. `192.168.1.50` or `db.internal.network`).
3. **JDBC Port**: Defaults based on type (e.g., `5432`, `3306`, `1521`, `1433`).
4. **Database Name**: (e.g., `lportal` or custom).
5. **Database Username**.
6. **Database Password**.

---

## 3. Database Property Generation

LDM maps the inputs to standard Liferay JDBC properties and appends them to the generated `portal-ext.properties`:

### PostgreSQL Example

```properties
jdbc.default.driverClassName=org.postgresql.Driver
jdbc.default.url=jdbc:postgresql://${DB_HOST}:${DB_PORT}/${DB_NAME}
jdbc.default.username=${DB_USER}
jdbc.default.password=${DB_PASSWORD}
```

### MySQL Example

```properties
jdbc.default.driverClassName=com.mysql.cj.jdbc.Driver
jdbc.default.url=jdbc:mysql://${DB_HOST}:${DB_PORT}/${DB_NAME}?useUnicode=true&characterEncoding=UTF-8&useFastDateParsing=false
jdbc.default.username=${DB_USER}
jdbc.default.password=${DB_PASSWORD}
```

---

## 4. Compose Orchestration

During Docker Compose generation in `ComposerService`:

- If `--db external` is set, LDM **excludes the database container service block** (e.g., `db` container is omitted completely from the generated `docker-compose.yml`).
- Liferay is configured to start up independently and connect directly to the external host network interface.
- (Ensure that the `liferay` service network is configured to bridge correctly or that `--network host` compatibility is supported if running against localhost databases on some OS platforms).

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-20* | *Last Reviewed: 2026-07-02*
