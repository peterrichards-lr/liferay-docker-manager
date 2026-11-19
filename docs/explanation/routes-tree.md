# The Routes Tree

`<project>/routes` is how Liferay publishes configuration to everything else
in the project: its own main domain, and the OAuth2 credentials it generates
for each client extension. It is a **shared space**, not a Liferay-private
directory, and **Liferay itself writes it, at runtime**.

This page is the contract a client-extension (or custom-service) developer can
rely on: what is mounted where, which variables point at it, when it fills,
and why each rule exists. The incident numbers are kept on purpose. Each one
is a time the rule was broken, and they are the reason the rules are
believed. None of this was written down before LDM-#1928, which is how the
mechanism came to be broken three separate times.

For **ownership and permissions** on the same tree (the helper container, and
why the mode is `777`), see
[Permissions, Bind Mounts and the Reclaim Helper](permissions-and-reclaim.md).
For where the tree sits in the overall deployment flow, see
[Architecture, section 3](architecture.md#3-client-extension-deployment-lifecycle).

---

## What is mounted where

| Container | Host source | Container target |
|---|---|---|
| Liferay | `routes/` (whole tree) | `/opt/liferay/routes` |
| Client extension | `routes/default` (the virtual-instance tree) | `/etc/liferay/lxc/routes` |
| Custom service | `routes/default/dxp` | `/etc/liferay/lxc/dxp-metadata` |

`default` is the virtual-instance id. Inside it, Liferay writes two kinds of
tree:

- `default/dxp` -- what Liferay publishes about **itself**, chiefly the main
  domain that `lxcConfig.dxpMainDomain()` resolves.
- `default/<projectName>` -- what Liferay publishes about **one extension**,
  including the OAuth2 credentials it generates when it registers that
  extension's application.

A custom service gets the `dxp` half only, and only if you have not already
mounted something at `/etc/liferay/lxc/dxp-metadata` yourself. A custom
service is an arbitrary image, so everything LDM adds to it -- this volume,
the domain variables, `extra_hosts`, `depends_on` -- is additive: anything you
declared wins.

## The variables LDM sets

A client extension reaches both trees as **subdirectories of its one mount**,
through the variables it already reads:

| Variable | Value LDM sets |
|---|---|
| `LIFERAY_ROUTES_DXP` | `/etc/liferay/lxc/routes/dxp` |
| `LIFERAY_ROUTES_CLIENT_EXTENSION` | `/etc/liferay/lxc/routes/<projectName>` |
| `LIFERAY_LXC_DXP_MAIN_DOMAIN` | the project's host name |
| `LIFERAY_LXC_DXP_DOMAINS` | the project's host name |

A real extension declares both routes trees and reads both, typically like
this in its `LCP.json` and `application.json`:

```json
"LIFERAY_ROUTES_CLIENT_EXTENSION": "/etc/liferay/lxc/ext-init-metadata",
"LIFERAY_ROUTES_DXP":              "/etc/liferay/lxc/dxp-metadata"
"config.node.config.trees": ["${LIFERAY_ROUTES_CLIENT_EXTENSION}",
                             "${LIFERAY_ROUTES_DXP}"]
```

**Under LDM, the two `LIFERAY_ROUTES_*` values are overridden.** For every
other variable, your `LCP.json` `env` block wins. These two are the deliberate
exception. The paths in `LCP.json` describe Liferay Cloud's layout, where they
really are the trees. Under LDM nothing is mounted at them, so honouring the
declaration would point your extension at an empty path. That is the exact
failure LDM-#1944 removed. Before LDM-#1928, LDM forwarded the client-extension
variable from `LCP.json` and mounted nothing at it, so every extension was told
to read a path that did not exist.

The two domain variables work the other way round. LDM sets them first, so a
value your extension declares itself wins.

You cannot repoint a routes tree from the host either. A service-targeted
`MY_EXTENSION_LIFERAY_ROUTES_DXP` is blocked just as `LIFERAY_ROUTES_DXP`
would be. See [Configuration](../reference/configuration.md).

### The per-extension directory is named from `projectName`

The directory is `routes/default/<projectName>`, where `projectName` comes from
your extension's `<name>.client-extension-config.json`. It is **not** the `id`
in `LCP.json`. Liferay takes the directory name from the
`ext.lxc.liferay.com/projectName` ConfigMap label and calls
`Files.createDirectories` on it (`RoutesPortalK8sConfigMapModifier`). That
label is fed from `projectName`.

The two names differ for almost every extension, because the id drops the
hyphens the directory keeps (`ecopulseheadlessauth` against
`ecopulse-headless-auth`). All 16 samples in `ldm-cx-samples` differ. Reading
the id-named path does not fail loudly. You get a real, empty, readable
directory while Liferay fills a different one beside it, which is why this was
diagnosed as a permissions problem four times over (LDM-#1944).

When the two differ, LDM prints which name applies where as it builds the
extension's service. The **id** gives the subdomain and the `/etc/hosts`
entry. The **projectName** gives the config tree (LDM-#1984).

## Rules, and why each exists

### Never mount over `/opt/liferay/routes` in an extension container

That path is right for Liferay, which writes its trees there. But an extension
built on `liferay/node-runner` does `COPY . /opt/liferay`, so in that container
`/opt/liferay/routes` holds the application's own route handlers. Mounting over
it shadows the app and the container will not start. On the deployment that
reported LDM-#1911, sixteen `.cjs` handlers sat at that path. The two images do
not share a filesystem convention, and every regression here has come from
assuming they do.

The Liferay container's own mount is just as easy to get wrong. Liferay reads
and writes `/opt/liferay/routes`. When LDM mounted the host tree somewhere else,
Liferay wrote its trees inside the container where nothing could see them
(LDM-#1918).

### The extension mount is anchored at `routes/default`, not the leaf

**A bind mount fixes its source directory once, when the container is
created.** Until LDM-#1944, each tree was mounted separately at its leaf.
Something deletes and recreates `routes/default/<projectName>` between the
mount being made and Liferay publishing into it. The container keeps the
orphaned directory and reads it as empty for the rest of its life, while the
live directory fills beside it.

This was measured on a live deployment. The extension container held inode
`2013443` while the live host directory was `2013444`, in the same capture
where the mount still read as empty. It reproduces on plain Docker with no
Liferay involved: a leaf mount shows an empty directory after the host
directory is deleted and recreated, and a parent mount survives it. In the same
capture, `routes/default/dxp` was mounted into the same container at the same
moment and read correctly. Liferay does not recreate that one, and that was the
only difference.

Mounting one level up means the leaf is looked up **inside** the container on
every read, so it follows whatever the live directory is. This is also why the
Liferay container never had the bug: it has always mounted above the level that
gets replaced.

**It stops at `routes/default`, not `routes`.** Mounting `routes` would expose
every other virtual instance's trees. One level is enough: over four days on
the deployment that reported this, `default/` and `default/dxp/` both persisted
while `default/<projectName>/` was replaced.

> [!WARNING]
> **Every extension in a project can read every other extension's OAuth2
> credentials.** This is a knowing trade, decided on LDM-#1944. LDM is for
> demos, testbeds and experimentation, never production. Narrowing the mount
> back to one extension's directory would bring the empty-directory bug back.
> Isolation needs a mechanism that does not depend on a leaf bind mount
> surviving deletion.

### Everything that mounts a subtree waits for Liferay

Each client extension, and each custom service that has not declared its own
ordering, gets:

```yaml
depends_on:
  liferay:
    condition: service_healthy
```

Without it, a container that starts first reads an empty directory
(LDM-#1928).

`service_healthy` resolves against the healthcheck on the **Liferay** service.
LDM writes that healthcheck in the generated compose file, but its test is the
`liferay/dxp` image's own, unchanged: a `curl` of `/c/portal/layout`. So
"healthy" means "serving pages", not "process started". LDM changes only the
patience (`start_period` 600s, `retries` 10). The image's defaults declared a
busy portal dead after twelve seconds of not responding, and compose then took
the whole stack down (LDM-#2050).

### LDM creates the directories before mounting them

If a bind-mount source is missing, Docker creates it itself, as an empty
directory owned by root. So LDM creates every routes subdirectory the generated
compose will mount before the stack starts. It also creates the directory
behind every `LIFERAY_ROUTES_*` value that points inside the extension mount.
That second part matters because a missing tree is a configuration error to
`config.node`, not an empty tree (LDM-#1944).

This currently happens for local projects only. On a remote node the
directories are left to Docker. See
[Remote Node Architecture](remote-node-architecture.md#open-question-scaffolding-the-routes-tree-on-a-remote-node).

## Deploying an extension is two things, with opposite timing

These two are routinely mixed up, and putting the wait on the wrong one breaks
the chain:

| | What it is | When |
|---|---|---|
| **The zip into Liferay** | the artifact placed in `osgi/client-extensions/` | **immediately**, before Liferay is running or ready |
| **The extension's container** | the compose service built from the extension | **only once Liferay is healthy** |

The zip is a file copied into a bind-mounted directory. `ldm deploy <zip>`
places it without checking whether Liferay is running, and Liferay picks it up
on its next scan. This order matters. The zip is what makes Liferay register
the extension and create its OAuth profile at all. If you delay the zip until
Liferay is ready, the whole chain starts later. If you make it wait for
Liferay, a cold start can deadlock, because the thing being waited for depends
on the thing being withheld.

The `service_healthy` wait therefore belongs on the **container only**, which
is where LDM-#1928 put it. The full chain:

```text
zip deployed (before or during boot)
  -> Liferay scans and registers the extension
  -> servlet triggered
  -> OAuth profile created
  -> credentials written to routes/default/<projectName>
  -> visible inside the running container (the bind mount is live)
```

## When the tree is actually populated

The mounts are only half the mechanism. The contents arrive when Liferay writes
them, not when your container starts, and nothing in LDM can hurry them:

1. Liferay boots and becomes healthy. Reaching "healthy" is itself a servlet
   request, because the healthcheck curls `/c/portal/layout`.
2. `default/dxp` is populated with what Liferay publishes about itself.
3. **An extension must be deployed before it has an OAuth profile at all.**
   Liferay has nothing to publish for an extension it has not seen.
4. The OAuth application, and so the credentials in `default/<projectName>`,
   is **not created until the Liferay servlet is first triggered.** On the
   deployment investigated in LDM-#1911, the files were stamped roughly two
   minutes after the extension deployed.

So `service_healthy` guarantees Liferay is *serving* before your extension
starts. It does **not** guarantee your extension's credentials already exist.
They cannot exist if that trigger has not happened yet.

**The bind mount is live, but that is not enough on its own.** Files that
appear on the host after your container started show up inside it
immediately. Whether your extension notices is up to your code. On the
deployment that reported LDM-#1915, the installed SDK resolved the OAuth
application once, in a constructor (`lxcConfig.oauthApplication(erc)`). If the
credentials were not on disk at container start, it returned `undefined` and
kept that value for the life of the process. `config-node` also caches the miss
on purpose, so a lazy getter alone does not recover it.

The ordering itself is correct. LDM-#1978 suspected the wait was skipped on the
`compose create` + `compose start` path. That was measured and retracted:
`compose start` honours `depends_on` conditions. The race between "Liferay is
healthy" and "this extension's credentials exist" is built in, and no
scheduling on LDM's side removes it. LDM's job ends at publishing the
credentials where your extension can read them. **Re-resolve them on an
authentication failure** instead of caching them for the process lifetime.
[Client Extensions](../how-to/client_extensions.md) explains why, including the
later rewrite of the tree when an extension is redeployed.

**Docker must actually share the host path.** If the project lives outside the
paths Docker Desktop shares, Docker quietly creates a directory inside its VM
instead of binding the host one. The mount looks correct, the container sees an
empty directory, and nothing ever appears in it. This was mistaken for "bind
mounts are not live" while working on LDM-#1928. The first probe had simply
used an unshared path.

## The DXP domain carries no port (LDM-#2289)

Behind Traefik, `default/dxp` must publish the bare host, such as
`my-project.local`, never `my-project.local:8080`. Liferay's routes writer
(`com.liferay.portal.k8s.agent.impl`, active in every DXP, not only on
Kubernetes) gets this wrong by itself. Its first write asks for the **secure**
local port, which is `-1` behind a proxy that terminates TLS, so it writes the
bare host. But its address listener uses the secure flag of each **request**,
so the first plain request adds Tomcat's `:8080`. This is reported upstream as
LDM-#2344.

No property, forwarded header or Tomcat setting stops this. LDM corrects it
inside the JVM with a small OSGi bundle, which `ldm run` places in the
project's `osgi/modules` for the DXP versions it is built for (`2026.q1.14` up
to, but not including, `2026.q4`). Fixing `:8080` from the host instead, by
rewriting the file once Liferay is ready, was tried and discarded. It leaves a
window where the wrong value is visible, and it misses any restart LDM is not
watching.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-11-19* | *Last Reviewed: 2026-11-19*
