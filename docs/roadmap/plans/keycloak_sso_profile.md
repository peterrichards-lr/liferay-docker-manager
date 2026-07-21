# Implementation Plan: Keycloak SSO Stack Profile

This plan details the configuration and assets needed to implement the `keycloak-sso` declarative stack profile.

---

## 1. Directory Layout

```text
ldm_core/resources/profiles/keycloak-sso/
├── profile.json
├── compose-overlay.yml
└── files/
    ├── keycloak/
    │   └── realm-export.json
    └── osgi/
        └── configs/
            └── com.liferay.portal.security.sso.openid.connect.internal.configuration.OpenIdConnectConfiguration.config
```

---

## 2. Docker Compose Overlay (`compose-overlay.yml`)

The overlay file registers Keycloak as a service on the private project network:

```yaml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:24.0.2
    container_name: ${CONTAINER_PREFIX}-keycloak
    restart: always
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD:-admin}
    command:
      - "start-dev"
      - "--import-realm"
    volumes:
      - ./keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json:ro
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=liferay-net"
      - "traefik.http.routers.${CONTAINER_PREFIX}-keycloak.rule=Host(`keycloak.${HOST_NAME}`)"
      - "traefik.http.routers.${CONTAINER_PREFIX}-keycloak.tls=true"
      - "traefik.http.routers.${CONTAINER_PREFIX}-keycloak.entrypoints=websecure"
      - "traefik.http.services.${CONTAINER_PREFIX}-keycloak.loadbalancer.server.port=8080"
    networks:
      - liferay-net
```

---

## 3. Keycloak Realm Template (`realm-export.json`)

The realm template will be pre-configured to:

- Create a realm named `liferay-sandbox`.
- Register an OIDC Client named `liferay-portal` with client secret `liferay-secret` and allowed redirect URIs matching `https://*.${HOST_NAME}/*` and `http://localhost:8080/*`.
- Create a mock user `demo-user` with password `password` and email `demo-user@liferay.com` for rapid evaluations.

---

## 4. Liferay OpenID Connect OSGi Config

Write the OIDC config file:

```properties
connectionTimeout=I"30000"
clientId="liferay-portal"
clientSecret="liferay-secret" # pragma: allowlist secret
discoveryEndpoint="https://keycloak.${HOST_NAME}/realms/liferay-sandbox/.well-known/openid-configuration"
enabled=B"true"
jwkUri="https://keycloak.${HOST_NAME}/realms/liferay-sandbox/protocol/openid-connect/certs"
loginButtonLabel="Sign In with Keycloak"
providerName="Keycloak SSO"
```

*(Note: Variables like `${HOST_NAME}` inside config templates will be programmatically replaced during profile application by LDM).*

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-21* | *Last Reviewed: 2026-07-02*
