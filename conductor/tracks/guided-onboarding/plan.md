# Track Implementation Plan: Extensible Stack Archetypes (Guided Onboarding)

This track implements **Extensible Stack Archetypes** and **External Database** support, moving away from simple application scaffolding to robust topology blueprints.

## 1. Objective

Implement a fully declarative system for injecting complex architectural topologies (Archetypes) into a new LDM project via `ldm init --archetype <name>`, and decouple external database connections via `--db external`.

## 2. Key Requirements

### Declarative Archetypes (`--archetype`)

- Archetypes are self-contained directories under `ldm_core/resources/archetypes/`.
- **`archetype.json`**: Contains metadata, required variables, and interactive prompts.
- **`compose-overlay.yml`**: A snippet that is programmatically merged into the base `docker-compose.yml`.
- **Assets**: Any files in the archetype directory (e.g., `osgi/`, `client-extensions/`) are copied to the project root.

### External Database (`--db external`)

- When `--db external` is used, LDM prompts for the JDBC connection string, username, and password.
- Injects these properties directly into `portal-ext.properties`.
- Generates a `docker-compose.yml` that **omits** the local database container.

## 3. Archetype Definitions

### A. Keycloak SSO (`keycloak-sso`)

- **Docker Compose**: Adds a `keycloak` container.
- **Assets**: Mounts a pre-configured `realm-export.json` into Keycloak.
- **Liferay Integration**: Injects OSGi OpenID Connect config files into Liferay's `osgi/configs` folder.

### B. Clustered / HA (`clustered`)

- **Docker Compose**: Spawns multiple Liferay containers (`liferay1`, `liferay2`).
- **Networking**: Automates JGroups unicast configurations using `TCPPING` (multicast bypass).
- **Storage**: Maps shared Named Docker Volumes for the Document Library.
- **Routing**: Configures sticky session routing on the Traefik load balancer.

## 4. Implementation Steps & Status Checklist

### Phase 1: Core Architecture & External DB

- [ ] Refactor `ldm init` to support `--archetype` and `--db external`.
- [ ] Implement the interactive JDBC prompt logic for `--db external` in `WorkspaceHandler`.
- [ ] Update `ComposerHandler` to support dropping the DB container and injecting `portal-ext.properties`.

### Phase 2: The Archetype Engine

- [ ] Create `ldm_core/resources/archetypes/` directory structure.
- [ ] Implement `ArchetypeManager` to parse `archetype.json`.
- [ ] Implement `yaml` merging logic to combine `compose-overlay.yml` into the generated stack.

### Phase 3: Archetype Implementation (Keycloak)

- [ ] Scaffold `keycloak-sso` archetype directory.
- [ ] Create `compose-overlay.yml` for Keycloak service.
- [ ] Provide `realm-export.json` and Liferay OpenID Connect OSGi configs.

### Phase 4: Archetype Implementation (Clustered)

- [ ] Scaffold `clustered` archetype directory.
- [ ] Create `compose-overlay.yml` for secondary Liferay nodes and Traefik sticky sessions.
- [ ] Provide JGroups `TCPPING` OSGi configurations.

## 5. Verification & Testing (Definition of Done)

- [ ] `ldm init test-ext --db external` successfully prompts for JDBC and generates a stack without a local DB.
- [ ] `ldm init test-sso --archetype keycloak-sso` successfully boots Liferay + Keycloak with SSO enabled.
- [ ] `ldm init test-cluster --archetype clustered` successfully boots multiple nodes with shared storage and Traefik load balancing.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-22* | *Last Reviewed: 2026-07-02*
