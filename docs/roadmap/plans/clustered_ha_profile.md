# Implementation Plan: Clustered HA Stack Profile

This plan details the configuration and assets needed to implement the `clustered` (High Availability) stack profile in LDM.

---

## 1. Directory Layout

```text
ldm_core/resources/profiles/clustered/
├── profile.json
├── compose-overlay.yml
└── files/
    ├── files/
    │   └── portal-ext.properties.append   # Custom cluster link properties
    └── osgi/
        └── configs/
            ├── com.liferay.portal.cluster.multiple.internal.configuration.ClusterMultipleConfiguration.config
            └── JGroupsChannelFactory.config
```

---

## 2. Docker Compose Overlay (`compose-overlay.yml`)

The overlay defines the secondary Liferay container and shared data volumes:

```yaml
services:
  liferay2:
    image: ${LIFERAY_IMAGE}:${LIFERAY_TAG}
    container_name: ${CONTAINER_PREFIX}-liferay2
    restart: always
    environment:
      - LIFERAY_JVM_OPTS=${LIFERAY_JVM_OPTS}
    volumes:
      - ./files:/opt/liferay/files
      - ./deploy:/opt/liferay/deploy
      - ./license:/opt/liferay/license
      - liferay-shared-data:/opt/liferay/data
    networks:
      - liferay-net
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=liferay-net"
      - "traefik.http.routers.${CONTAINER_PREFIX}-liferay2.rule=Host(`${HOST_NAME}`)"
      - "traefik.http.routers.${CONTAINER_PREFIX}-liferay2.tls=true"
      - "traefik.http.routers.${CONTAINER_PREFIX}-liferay2.entrypoints=websecure"
      - "traefik.http.services.${CONTAINER_PREFIX}-liferay2.loadbalancer.server.port=8080"
      # Enable Sticky Session Cookie
      - "traefik.http.services.${CONTAINER_PREFIX}-liferay2.loadbalancer.sticky.cookie=true"
      - "traefik.http.services.${CONTAINER_PREFIX}-liferay2.loadbalancer.sticky.cookie.name=LDM_STICKY_SESSION"

volumes:
  liferay-shared-data:
    name: ${CONTAINER_PREFIX}-shared-data
```

*(Note: We will also mount `liferay-shared-data` to the primary `liferay` container in the merge logic to ensure document libraries are synchronized).*

---

## 3. OSGi Cluster Link Configuration

To ensure nodes discover each other reliably within Docker networks, configure **`TCPPING`** instead of multicast:

### `com.liferay.portal.cluster.multiple.internal.configuration.ClusterMultipleConfiguration.config`

```properties
enabled=B"true"
```

### `JGroupsChannelFactory.config`

```properties
channel.properties="TCP(bind_addr=match-interface:eth.*;loopback=true):TCPPING(initial_hosts=${CONTAINER_PREFIX}-liferay1[7800],${CONTAINER_PREFIX}-liferay2[7800];port_range=0):MERGE3:FD_SOCK:FD_ALL:VERIFY_SUSPECT:pbcast.NAKACK2(use_mcast_xmit=false):UNICAST3:pbcast.STABLE:FRAG2:pbcast.GMS"
```

---

## 4. Shared Document Library Storage

In `files/portal-ext.properties.append`, configure the shared store directory so both nodes access the same volume:

```properties
dl.store.impl=com.liferay.portal.store.file.system.AdvancedFileSystemStore
```

*(By default, Liferay DXP/Portal's AdvancedFileSystemStore writes documents to `/opt/liferay/data/document_library`, which will be mapped to our shared `liferay-shared-data` volume).*

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-08* | *Last Reviewed: 2026-07-02*
