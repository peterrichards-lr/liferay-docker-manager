# Sharing & Tunnels: Exposing Liferay to the Internet

LDM provides a unified interface (`ldm share`) to securely expose your local Liferay environment or containers to the public internet. This is essential for:

- Testing external webhooks (e.g., payment gateways, external CI pipelines).
- Integrating with SaaS services that require a callback URL.
- Sharing your local development environment with colleagues or clients.

---

## Sharing Providers

LDM supports two distinct tunneling providers:

### 1. lfr-tunnel (Default)

`lfr-tunnel` is a lightweight, host-side Go client that routes traffic from public wildcard subdomains (`*.lfr-demo.se` and `*.lfr-demo.online`) directly to your local ports.

- **Pros**: Runs natively on the host, lowest CPU overhead, supports wildcard routing.
- **Requirements**: LDM automatically downloads and updates the binary on the first invocation.

### 2. lfr-tunnel-docker (Zero-Install)

Runs the exact same `lfr-tunnel` client, but completely isolated as a service sidecar inside your project's `docker-compose.yml` stack.

- **Pros**: 100% immune to SentinelOne/EDR host-level alerts that block unknown Go binaries on macOS/Windows. Runs in sync with the project's lifecycle (`ldm run` / `ldm stop`), requiring zero manual background process management.
- **Resource Constraints**: Optimized to consume absolute minimal resources (CPU is restricted to a maximum of `0.10` cores with `0.05` reservations, and memory is bound to a maximum of `50M` with `20M` reservations).
- **Direct Tomcat Bypass**: Directly connects to the `liferay` container internally on Tomcat's port `8080` (rather than routing through the Traefik proxy), which resolves Host header/Virtual Host url conflicts for external redirect services.

#### Conceptual Flow

```text
  Public User
      │ (HTTPS via leased subdomain: e.g., my-subdomain.lfr-demo.se)
      ▼
  VPS Server Gateway (lfr-tunneld)
      │ (WebSockets over TCP)
      ▼
  lfr-tunnel Client (Docker Sidecar Service in Project Network)
      │ (Bypasses local Nginx/Traefik proxy completely)
      ▼ (Internal Docker Network Routing)
  liferay Container (Tomcat Port 8080)
```

### 3. ngrok

`ngrok` runs as a sidecar container inside your project's Docker Compose stack, creating a secure tunnel to ngrok's edge servers.

- **Pros**: Fully self-contained, isolated within the project stack.
- **Requirements**: Requires a free ngrok account and Auth Token.

---

## Direct CLI Usage (`ldm share`)

You can manage tunnels at any time using the `ldm share` command group.

### Start a Tunnel

To share a running project context:

```bash
ldm share start [project] --provider lfr-tunnel --subdomain my-subdomain --ports 8080
```

- If `--provider` is omitted, LDM defaults to `lfr-tunnel`.
- If `--subdomain` is omitted, it defaults to the project name or your machine hostname.
- If `--ports` is omitted, it defaults to port `8080`.

### Check Tunnel Status

To inspect the active tunnel and retrieve its public URL:

```bash
ldm share status [project]
```

### Stop a Tunnel

To terminate the active sharing session:

```bash
ldm share stop [project]
```

---

## Automatic Run Integration (`ldm run --share`)

Instead of starting the tunnel manually, you can tell LDM to automatically boot the sharing tunnel as soon as your Liferay container becomes healthy using the `--share` flag:

```bash
ldm run my-project --share --share-subdomain custom-sub --share-provider lfr-tunnel
```

### Expose Alias (Ngrok Legacy)

For backward compatibility, the `--expose` flag remains supported and behaves as an alias for `--share --share-provider ngrok`:

```bash
ldm run my-project --expose
```

---

## Authentication for Liferay Tunnel (lfr-tunnel)

The Liferay Tunnel (`lfr-tunnel` or `lfr-tunnel-docker`) requires a Client Token to authenticate with the server gateway. LDM looks for this token in the following order of priority:

1. **Environment Variable**: `LFT_CLIENT_TOKEN` (highest priority). Useful for CI/CD or automated scripts:

   ```bash
   export LFT_CLIENT_TOKEN="your_lfr_tunnel_token"
   ```

2. **Token File**: A flat text file containing only the token saved at `~/.lfr-tunnel/token`.
3. **LDM Global Config**: Stored in `~/.ldmrc` under the `lfr_tunnel_token` key.
4. **Interactive Prompt**: If no token is found, LDM will prompt you to enter it interactively and save it to your `~/.ldmrc` global config for future commands.

---

## Authentication for Ngrok

Ngrok requires an Auth Token to bind custom host headers and use HTTPS upstreams. The first time you use ngrok, LDM will prompt you for your Auth Token:

1. Sign up or log in at [dashboard.ngrok.com](https://dashboard.ngrok.com/).
2. Navigate to **Getting Started** -> **Your Authtoken**.
3. Paste the token into the LDM prompt.

LDM will save this token securely in your global configuration (`~/.ldmrc`), so you will never be prompted for it again across any of your projects.

---

## Tunnel Inspector Dashboard

The Liferay Tunnel container exposes a local web-based Inspector Dashboard. This dashboard allows you to view active tunnels, request/response traffic, and WebSocket latency.

### Accessing the Dashboard

When running with `lfr-tunnel-docker` as your provider, LDM automatically maps port `4040` of the sidecar container to your host machine:

- **Dashboard URL**: [http://localhost:4040](http://localhost:4040)

### Custom Inspector Host Binding (`LFT_INSPECTOR_BIND`)

By default, when running inside Docker, the tunnel client binds to `0.0.0.0:4040` to ensure port forwarding works out-of-the-box. When running natively outside Docker, it binds only to `127.0.0.1:4040` for local security.

You can customize the network interfaces the dashboard binds to by setting the `LFT_INSPECTOR_BIND` environment variable in your local `.env` file:

```env
# Bind the inspector dashboard to loopback only (inside container)
LFT_INSPECTOR_BIND=127.0.0.1

# Bind to all interfaces (default inside Docker)
LFT_INSPECTOR_BIND=0.0.0.0
```

---

## Handling SaaS Callbacks (The Host Header)

By default, the sharing tunnels rewrite the `Host` header to match your project's `--host-name` (e.g., `forge.demo`). This ensures that Traefik knows how to route the request to your specific Liferay container.

However, because Liferay "thinks" it is running at `forge.demo`, it may dynamically generate callback URLs or pagination links using `forge.demo` instead of your public tunnel domain (e.g., `custom-sub.lfr-demo.se`). Since external SaaS services cannot resolve `forge.demo`, those links will fail.

### How to Fix This

If your SaaS integration requires Liferay to generate fully qualified URLs (e.g., OAuth redirects), you must temporarily tell Liferay to treat the public tunnel URL as its primary domain:

1. Log into Liferay as an administrator.
2. Go to **Control Panel** -> **Instance Settings** -> **Instance Configuration**.
3. Under **Virtual Host**, enter your public tunnel domain (e.g., `custom-sub.lfr-demo.se` or `a1b2c3d4.ngrok.app`).
4. Save the configuration.

Liferay will now generate all callback URLs using the public tunnel address.
