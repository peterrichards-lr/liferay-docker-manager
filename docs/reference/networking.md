# Networking & DNS

## Unified Host & SSL Rules (run, link, clone, import)

All project initialization commands follow these security and naming rules:

1. **Interactive Hostname**: If no `--host-name` is provided, LDM will prompt you (defaulting to `localhost`).
2. **SSL Auto-Enable**: If a custom hostname is used (anything other than `localhost`), LDM **automatically enables SSL** and routes traffic via port 443.
3. **Explicit Control**: You can override the auto-SSL behavior using `--ssl` or `--no-ssl`.
4. **Port Mapping**: When SSL is active, the direct port `8080` mapping is removed to ensure all traffic passes through the secure Traefik proxy.

## Client Extension Routing & Wildcard SSL

LDM automates the routing and SSL orchestration for both the main Liferay instance and its related Client Extensions using a **Wildcard Subdomain Strategy**:

- **Predictable Subdomains**: Server-Side Client Extensions (SSCE) with a `Dockerfile` are automatically assigned a unique subdomain based on their ID. For example, if your project host is `my-project.local`, an extension with ID `custom-logic` will be accessible at `https://custom-logic.my-project.local`.
- **Zero-Config HTTPS**: LDM generates a single SSL certificate that covers both the main host and its wildcard (e.g., `my-project.local` and `*.my-project.local`). This secures all extensions automatically.
- **Automated Routing**: Traffic on port 443 is intercepted by the global Traefik proxy and routed to the correct container using SNI (Server Name Indication) and Docker labels.
- **Liferay Integration**: LDM automatically injects `LIFERAY_WEB_SERVER_HOST` and other necessary properties into Liferay to ensure it can communicate seamlessly with its client extension subdomains.

> [!TIP]
> **DNS Resolution**: Standard `/etc/hosts` files do not support wildcards. However, LDM's **`ldm fix-hosts [project]`** and **`ldm doctor --fix-hosts`** commands are intelligent—they scan your project for active client extensions and automatically append entries for both the main hostname and all required subdomains (e.g., `custom-logic.my-project.local`) to your hosts file.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-20* | *Last Reviewed: 2026-07-02*
