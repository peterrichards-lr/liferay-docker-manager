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

## 4. Site Initializers Are Deployed After the First Boot

A **site-initializer** client extension is the one CX type LDM does *not* hand
to Liferay during `ldm import`. It is staged under
`<project>/.ldm/deferred-client-extensions/` and copied into
`osgi/client-extensions/` once the portal reports healthy.

### Why

Measured on a live DXP `2026.q3.0`. When a site initializer is already present
in `osgi/client-extensions/` as the portal starts against an empty database,
`SiteInitializerClientExtension`'s bundle tracker opens *before* the built-in
`welcome` and `cms` site initializers have created the Guest site's layouts,
and `ServiceContextFactory` needs one:

```text
ERROR bundle com.liferay.site.initializer.extender:1.0.147
  [SiteInitializerClientExtension(4567)] : The activate method has thrown an exception
java.lang.RuntimeException: java.lang.NullPointerException:
  Cannot invoke "com.liferay.portal.kernel.model.Layout.getGroupId()" because "layout" is null
    at com.liferay.portal.util.PortalImpl.getCanonicalURL(PortalImpl.java:1556)
    at com.liferay.portal.kernel.service.ServiceContextFactory._getInstance(ServiceContextFactory.java:176)
    at ...SiteResourceImpl.putSiteSiteInitializer(SiteResourceImpl.java:201)
    at ...SiteInitializerClientExtension.addingBundle(SiteInitializerClientExtension.java:94)
```

**The failure is silent.** The bundle still logs `STARTED`, no site is created,
no fragments are imported, no page exists, and the only sign is one stack trace
among several thousand startup lines. The same artifact dropped into the
already-running portal initialises in ~100 ms.

It does not reproduce on a *later* boot, because
`SiteResourceImpl.putSiteByExternalReferenceCode` early-returns once a group
with that external reference code exists — so a project hydrated from a
database snapshot never sees it. Only a **fresh** project does, which is
exactly what `ldm import` creates.

### How LDM detects one

By the header the extender itself tracks — `Liferay-Client-Extension-Site-Initializer`
in the built zip's `WEB-INF/liferay-plugin-package.properties`. Not the file
name, and not the `type: siteInitializer` line in `client-extension.yaml`:
that is a source-side descriptor which the Gradle build consumes and does not
place in the artifact.

### What you will see

```text
  + Held back Site Initializer until first boot completes: my-site-initializer.zip
```

...during `ldm import`, and then, once the portal is healthy:

```text
Deploying site initializer(s) held back from the first boot: my-site-initializer.zip
```

### Scope of the deferral

| Path | Behaviour |
|---|---|
| `ldm import` hydration | Deferred — the portal has not booted yet |
| `ldm deploy` | **Not** deferred — the portal is already up, which is the moment that works |
| `ldm dev` file monitor | **Not** deferred — same reason |
| Every other CX type | **Not** deferred — they are fine where they are |

If you boot with `--no-wait`, readiness is never reached and the artifact stays
staged until a later `ldm run` or `ldm wait` completes. Nothing is lost; the
zip is still in `.ldm/deferred-client-extensions/` and travels with a `.ldmp`
package built from the project.

To deploy a staged initializer by hand into a portal that is already running:

```bash
cp .ldm/deferred-client-extensions/*.zip osgi/client-extensions/
```

---

## 🔐 5. Testing Authorisation

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
*Last Updated: 2026-09-18* | *Last Reviewed: 2026-09-18*
