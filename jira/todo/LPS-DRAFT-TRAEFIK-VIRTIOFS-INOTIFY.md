# Liferay DXP Bug Report: Traefik Inotify Failures on macOS VirtioFS (Colima/Docker Desktop)

[JIRA-KEY] - <https://liferay.atlassian.net/browse/[JIRA-KEY]>

## Component

- **Infrastructure / Liferay Proxy Global**
- **Docker / Traefik / VirtioFS**

## Environment

- **Liferay Product Version**: Liferay DXP 2026.q1.7-lts
- **API Endpoint**: N/A
- **OS**: macOS (Docker Desktop / Colima using VirtioFS or SSHFS)

## Summary

When LDM generates dynamic `mkcert` certificates and Traefik configuration files into a host-mounted volume (e.g. `~/.ldm/infra/certs`), Traefik's dynamic file provider (`--providers.file.watch=true`) fails to detect the file creation/modification events. This causes Traefik to serve its fallback `TRAEFIK DEFAULT CERT` instead of the valid project certificates.

## Description & Technical Analysis

This is a known upstream limitation with macOS hypervisors bridging to Linux VMs via VirtioFS or SSHFS. The translation layer does not reliably forward `inotify` (filesystem event) signals from the macOS host to the Linux guest. As a result, Traefik's internal file watcher remains unaware of newly injected certificates and routing configurations until the container process is restarted.

## Steps to Reproduce

1. Boot the global proxy (`liferay-proxy-global`) on a macOS host using Colima or Docker Desktop.
2. Import a project with `ssl=True` and `host_name=aica.local`.
3. Observe LDM automatically generating `aica.local.pem`, `aica.local-key.pem`, and `traefik-aica.local.yml`.
4. Perform a `curl -v https://aica.local`.
5. Observe `SSL certificate problem: unable to get local issuer certificate` or `Issuer: CN=TRAEFIK DEFAULT CERT`.

```json
{
  "tls": {
    "certificates": [
      {
        "certFile": "/etc/traefik/certs/aica.local.pem",
        "keyFile": "/etc/traefik/certs/aica.local-key.pem"
      }
    ]
  }
}
```

## Expected Results

Traefik should immediately pick up the new certificate and configuration files dropped into the watched directory and serve the valid `mkcert` CA certificate on the next request.

## Workaround

We have implemented an automated orchestration patch in `ldm_core/handlers/infra.py`. Whenever `mkcert` successfully generates new certificates and writes the Traefik `.yml` config, the LDM tool checks if it's running on macOS/Windows. It issues a mandatory 2-second sleep to allow hypervisor sync, followed by a hard `docker restart liferay-proxy-global` to force Traefik to flush its cache and parse the new files upon startup.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-22* | *Last Reviewed: 2026-07-06*
