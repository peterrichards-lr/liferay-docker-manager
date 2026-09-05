# Client Extensions & Workspace Integration Guide

> [!NOTE]
> This guide details how Liferay Docker Manager (LDM) discovers, links, routes, and hot-reloads Liferay Client Extensions (CX) and Liferay Workspace projects.

---

## 🔌 1. Linking a Client Extension Workspace (`ldm link`)

To attach a local Liferay Workspace or standalone Client Extension directory to a running Liferay container, run:

```bash
ldm link /path/to/my-client-extension
```

When `ldm link` is executed, LDM automatically performs the following actions:

1. **Workspace & CX Discovery**: Scans the directory for `client-extension.yaml`, `LCP.json`, and `bnd.bnd` configurations.
2. **Subdomain Route Generation**: Parses `client-extension.yaml` and `LCP.json` to generate virtual subdomains (e.g. `http://my-cx.my-project.local:8080`).
3. **Container Link Storage**: Persists the link mapping in `.liferay-docker.meta` so the extension automatically re-attaches across container restarts.
4. **File Watcher Initialization**: Launches a background file watcher to synchronize built assets (`build/libs/*.zip` or `dist/`) into Liferay's auto-deploy directory.

---

## ⚡ 2. Server-Side Client Extensions (SSCE)

Server-Side Client Extensions (such as Spring Boot OAuth2 resource servers or Node.js microservices) require dedicated container sidecars.

LDM manages SSCE containers out of the box:

- **`LCP.json` Parsing**: Reads port specifications, environment variables, and memory limits from `LCP.json`.
- **Dynamic Docker Compose Sidecars**: Generates dynamic Compose fragments to spin up the SSCE microservice alongside Liferay on the same internal container network.
- **Automated OAuth2 ERC Wiring**: Injects Liferay OAuth2 External Reference Codes (ERC) so the microservice and Liferay authenticate seamlessly.

---

## 🔄 3. Live Hot-Reloading Workflow

LDM uses an atomic file synchronization pipeline to eliminate deployment race conditions:

1. **Staged Copy**: Built archives are written to a temporary staging buffer outside the active deploy scanner path.
2. **Permission Fixing**: Applies POSIX permissions (`chmod 666`, `chown 1000:1000`) so the internal Liferay process (`liferay` user) can read the deployment archive immediately.
3. **Atomic Move**: Atomically moves the archive into the `deploy/` directory, preventing Liferay's `AutoDeployScanner` from reading incomplete zip headers.

To monitor live deployment logs and hot-reloading events:

```bash
ldm logs -f
```

## 🔐 4. Testing Authorisation

**Your LDM project is more permissive than a customer's instance.** An
authorisation check that passes here can fail in production, and the local test
cannot tell you.

LDM never sets `omniadmin.users`, so projects run with Liferay's default:

> If the `omniadmin.users` property is not set or is empty, users with the
> **Administrator** role in the **default instance** are also considered Omni
> Admins.

So the Administrator account satisfies `isOmniadmin()`, and any endpoint gated
on it succeeds. The same code deployed somewhere that sets `omniadmin.users`
explicitly, or where the caller belongs to a **non-default** instance, returns
403.

That is a test which cannot fail — the assertion is fine, the environment
guarantees it passes.

### Reproducing the stricter posture

Set this in `files/portal-ext.properties` and restart:

```properties
omniadmin.users=some-other-screen-name
```

Then re-run the check. If it still passes, the gate is genuinely satisfied
rather than satisfied by the default.

Worth doing whenever you are testing:

- an OSGi module or client extension that gates on `isOmniadmin()`
- anything called by a **service account** rather than an interactive user, since
  a service account is far less likely to hold Administrator in a real
  deployment
- a permission cascade, where the branch you think is being exercised may not be

LDM keeps the permissive default deliberately: it matches stock Liferay, and
this is a local development and demo sandbox (see
[Security Posture](../reference/security.md)). The point is not that the default
is wrong, but that it is invisible — and it makes authorisation tests
optimistic.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-09-05* | *Last Reviewed: 2026-09-05*
