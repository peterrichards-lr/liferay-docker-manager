# Networking & DNS

## Unified Host & SSL Rules (run, link, clone, import)

LDM recognises three kinds of host name, and the kind decides whether you ever
need `sudo`, `mkcert` or a hosts-file entry:

| Mode | Example | URL | sudo | mkcert |
| :--- | :--- | :--- | :--- | :--- |
| **Basic** (default) | `localhost` | `http://localhost:8080` | No | No |
| **Named local** | `--host-name myproj.localhost` | `http://myproj.localhost` | No | No |
| **Local HTTPS** (advanced) | `--host-name myproj.localhost --ssl` | `https://myproj.localhost` | No | Yes, once |
| **Custom domain** (advanced) | `--host-name acme.demo` | `https://acme.demo` | Yes (hosts entry, opt-in) | Yes |

All project initialization commands (run, link, clone, import) follow these
rules:

1. **Host name**: Without `--host-name`, a project uses `localhost` and is
   served at `http://localhost:8080`, exactly as before. Passing `--ssl`
   without `--host-name` asks for one interactively; the prompt suggests
   `<project>.localhost` and still defaults to the current host (`localhost`
   under `-y`).
2. **Normalisation** (LDM-#2513): `--host-name`, the prompt answer and a
   `.ldmp` manifest's `host_name` are normalised. `localhost` and anything
   ending in `.localhost` are used as given; a bare name with no dot
   (`myproj`) becomes `myproj.localhost`, and must be a valid DNS label;
   anything else with a dot (`acme.demo`) is a custom domain, used as given. A
   host name already stored in a project is never rewritten -- `ldm doctor`
   suggests the `.localhost` form for a stored bare name instead.
3. **SSL auto-enable** (LDM-#2514): SSL is enabled by default for any host
   name **other than `localhost` and `*.localhost`**. Custom domains therefore
   get `https://` through the global Traefik proxy on port 443; `*.localhost`
   names stay HTTP unless you pass `--ssl` (or a `.ldmp` manifest declares
   `ssl: "true"`). `--ssl` and `--no-ssl` always override the default.
4. **Routing and ports**: An SSL project is served only through the proxy, and
   the direct port `8080` mapping is removed. A non-SSL `*.localhost` project
   is served through the proxy's plain-HTTP router (port 80) at
   `http://<project>.localhost`, and *also* keeps its direct port -- see
   below. A basic `localhost` project and a non-SSL custom domain never use
   the proxy.

### Named local hosts (`*.localhost`)

`.localhost` is reserved for loopback by RFC 6761, so `myproj.localhost`
resolves to `127.0.0.1` with no hosts-file entry -- LDM never offers to edit
the hosts file for it, never asks for `sudo`, and `ldm fix-hosts` /
`ldm doctor --fix-hosts` skip it. Each project gets its own host name and so
its own cookie scope: two `.localhost` projects running at once keep separate
login sessions, which two projects on `localhost` with different ports do not.

Client extensions follow the same rule: an extension with ID `custom-logic` in
`myproj.localhost` is served at `http://custom-logic.myproj.localhost`, which
is also under `.localhost` and needs no entry either.

How it is served (LDM-#2514):

- The project's Liferay container gets a Traefik router on the proxy's `web`
  entrypoint (port 80), and `ldm run` starts the global proxy for it. No
  certificate is involved, so `mkcert` is not needed.
- The direct port (`8080`, or the one LDM allocated) is **still published**.
  If port 80 is already in use when the proxy starts, the proxy binds another
  port and the project falls back to `http://myproj.localhost:<port>`; LDM says
  so at start-up, and the readiness banner, `ldm info` and `ldm list` all
  report that URL. LDM records the proxy's port at each `ldm run`, and reports
  the port-free URL only when it has observed the proxy on port 80.
- Binding port 80 needs no `sudo` on macOS (Docker Desktop, OrbStack, Colima)
  or with rootful Docker on Linux. **Rootless Docker** cannot bind ports below
  1024 by default; lower the limit once with
  `sudo sysctl net.ipv4.ip_unprivileged_port_start=80` (persist it in
  `/etc/sysctl.d/`) or accept the `:<port>` fallback.
- Switching a project to the `hosts` SSL mode (`ldm config ssl-mode hosts`)
  when it has no custom host name now gives it `<project>.localhost` (it was
  `<project>.local`), so the switch needs no hosts-file edit.

**Resolution findings.** Verified on macOS (Darwin 27): Python's resolver
(`socket.getaddrinfo`, and therefore `requests`) and `curl` resolve
`foo.localhost` and `a.b.localhost` to `::1` and `127.0.0.1` with no hosts
entry. Not yet verified, and still open on LDM-#2513: Safari, Java
`HttpClient`, Linux without `nss-myhostname` (glibc does not special-case
`.localhost` on its own -- LDM itself treats the names as loopback without
asking the resolver), the Windows-side browser under WSL2, and server-side
calls from inside containers, where `*.localhost` names the container's own
loopback. Client-extension containers carry an `extra_hosts` entry mapping the
project host name to the Docker host, as they do for every project; whether
that is sufficient for Liferay-to-extension calls on a `.localhost` project
has not been verified by booting one.

### Custom domains

A custom domain (anything that is neither `localhost` nor `*.localhost`) is the
advanced path, and comes as one bundle: **HTTPS + a hosts entry + a local CA**.

- **Hosts entry.** LDM checks that the name (and each client extension's
  subdomain) resolves to this machine before starting. If not, it asks
  `Add host entries? (Requires sudo)` and edits the hosts file only if you
  agree; `ldm doctor --fix-hosts` does the same later. Under `-y` it attempts
  the edit non-interactively.
- **Local CA.** See *Local HTTPS* below.
- `.local` names are mDNS names: on macOS they resolve slowly and on Windows
  the hosts file may be ignored. LDM warns and suggests the `.localhost` form.

### Local HTTPS (mkcert)

`mkcert` is **not** a base prerequisite (LDM-#2515). It is needed only when a
project uses SSL -- `--ssl`, or a custom domain's auto-SSL -- and LDM sets it
up the first time that happens:

- **Interactively**, if `mkcert` is missing and a user-level package manager is
  available (Homebrew on macOS, Scoop on Windows) LDM offers to install it;
  then, if its local CA has not been created, it offers to run
  `mkcert -install`, which adds the CA to your trust store and may ask for
  your password.
- **Non-interactively** (`-y`), LDM changes nothing outside its own
  directories. It prints the one-time command(s) to run yourself.
- Either way, if the CA is still missing the project starts on Traefik's
  default, untrusted certificate. Run `ldm renew-ssl` after setting up the CA.

`ldm doctor` reports `mkcert` as **not needed** (a pass, not a warning) while
no project uses SSL.

Install it yourself instead, if you prefer:

- macOS: `brew install mkcert nss` then `mkcert -install`
- Windows: `scoop bucket add extras`, `scoop install mkcert`, then
  `mkcert -install` from an Administrator PowerShell
- Linux: `sudo apt install mkcert libnss3-tools` then `mkcert -install`

See [Third-Party Tools](third_party_tools.md) for the full detail, including
the WSL2 CA-sharing steps.

### Custom hostname without SSL

Rule 3 is a *default*, not a constraint: `--no-ssl`, and a `.ldmp` package
manifest that declares `ssl: "false"` alongside a custom `host_name`, both
produce a custom hostname with SSL off. That combination is supported, and LDM
prints a warning at start-up saying what you get:

- **No proxy fronts the project.** For a custom domain, Traefik routing labels
  are emitted only for SSL projects and are TLS-only
  (`entrypoints=websecure`), so there is no plain HTTP router for the
  hostname. (The plain-HTTP router is for `*.localhost` names only.)
- **The access URL keeps Liferay's own port** — `http://my-project.local:8080`,
  not `http://my-project.local`. Point the hostname at the machine in your
  hosts file and keep the port.
- `ldm info`, `ldm list` and the readiness banner all report that same URL;
  they share one resolver, so they cannot disagree (LDM-#1568).

Re-run with `--ssl` to serve the same hostname on `https://my-project.local`
through the global proxy instead.

## Client Extension Routing & Wildcard SSL

LDM automates the routing and SSL orchestration for both the main Liferay instance and its related Client Extensions using a **Wildcard Subdomain Strategy**:

- **Predictable Subdomains**: Server-Side Client Extensions (SSCE) with a `Dockerfile` are automatically assigned a unique subdomain based on their ID. For example, if your project host is `my-project.local`, an extension with ID `custom-logic` will be accessible at `https://custom-logic.my-project.local`.
- **Zero-Config HTTPS**: LDM generates a single SSL certificate that covers both the main host and its wildcard (e.g., `my-project.local` and `*.my-project.local`). This secures all extensions automatically.
- **Automated Routing**: Traffic on port 443 is intercepted by the global Traefik proxy and routed to the correct container using SNI (Server Name Indication) and Docker labels.
- **A host port is published only when SSL is off.** The global Traefik proxy is provisioned for SSL projects, and the subdomain above is then the way in -- such a project publishes no per-extension host port, because it does not need one. A **non-SSL** custom-domain or `localhost` project gets no proxy at all, so each client extension publishes a host port instead and that port is the only way to reach it. A non-SSL **`*.localhost`** project is fronted by the proxy's plain-HTTP router, so an extension is also served at `http://<id>.<project>.localhost`; its host port is still published, so it stays reachable if the proxy could not bind port 80. The port is the one LDM allocated for that extension and is reported by `ldm status`; it is no longer rewritten (LDM-#1969 published everything landing on 8080 as a shared `28080`, so at most one extension on the whole host could bind it, and LDM-#1985 removed the mapping altogether before restoring it correctly).
- **Liferay Integration**: LDM automatically injects `LIFERAY_WEB_SERVER_HOST` and other necessary properties into Liferay to ensure it can communicate seamlessly with its client extension subdomains.

> [!TIP]
> **DNS Resolution**: Standard `/etc/hosts` files do not support wildcards. However, LDM's **`ldm fix-hosts [project]`** and **`ldm doctor --fix-hosts`** commands are intelligent—they scan your project for active client extensions and automatically append entries for both the main hostname and all required subdomains (e.g., `custom-logic.my-project.local`) to your hosts file. Names under `.localhost` are skipped: they need no entry.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-11-25* | *Last Reviewed: 2026-11-25*
