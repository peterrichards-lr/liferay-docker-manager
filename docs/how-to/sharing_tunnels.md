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

> [!WARNING]
> **EDR / SentinelOne Quarantine:** Because `lfr-tunnel` (host-side) is a Go binary downloaded dynamically to the host, corporate endpoint protection tools (such as SentinelOne) may flag it as an untrusted threat and quarantine it. This will prevent LDM from starting the tunnel and cause the error: `❌ Failed to verify lfr-tunnel installation after download.`
>
> **The Solution:** If you encounter EDR interference, use the **`lfr-tunnel-docker`** (Zero-Install) provider instead. It runs the exact same client inside the isolated project Docker Compose network, bypassing host-level EDR checks entirely. Set it via `--share-provider lfr-tunnel-docker` on startup:
>
> ```bash
> ldm run <project> --share --share-provider lfr-tunnel-docker --share-subdomain pjrtest
> ```

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

## Toggling Routing Modes (`ldm config ssl-mode`)

When developing with LDM, you may want to toggle your project network routing between:

- **Local Hosts-based SSL Mode**: Testing offline using a local custom domain name (e.g. `https://my-project.local` mapped in `/etc/hosts` with certificates managed via `mkcert`).
- **Public Share Tunnel Mode**: Exposing Liferay publicly via a leased subdomain on a secure tunnel gateway (e.g. `https://my-project.lfr-demo.online`).

Manually updating Liferay virtual hosts, rebuilding project properties, and updating environment config files inside your client extensions can be error-prone. LDM automates this using the `ssl-mode` command:

```bash
ldm config ssl-mode <hosts | share> [project] [--subdomain <subdomain>] [--domain <domain>] [--no-restart]
```

### Modes

#### 1. Hosts Mode (`hosts`)

- Switches the project's metadata `ssl` parameter to `true`.
- Sets the project's `host_name` to `<project_name>.local` (or retains your custom local domain).
- Surgically searches for and updates all local `.env` files (e.g., `LIFERAY_URL`, `LIFERAY_PORTAL_URL`, `AICA_LIFERAY_URL`) to point to `https://<project_name>.local`.
- Rebuilds project properties so Liferay aligns its internal virtual host settings.

#### 2. Share Mode (`share`)

- Switches the project's metadata `ssl` parameter to `false` (since SSL termination happens at the public tunnel edge).
- Sets `host_name` to `localhost`.
- Configures your optional `--subdomain` (defaults to project name) and `--domain` (defaults to `lfr-demo.online`).
- Surgically searches for and updates all local `.env` files to point to the public tunnel URL (e.g. `https://<subdomain>.<domain>`).
- Rebuilds project properties so Liferay maps internal endpoints to the public gateway URL.

> [!WARNING]
>
> **Container Stack Downtime Warning:**
>
> Changing routing modes modifies Traefik reverse-proxy bindings, project metadata, and `portal-ext.properties`. By default, if the project container stack is currently running, changing the routing mode will **briefly stop and restart** the container stack to apply the changes (causing a short downtime of the Liferay instance).
>
> If you prefer to change the configuration offline or manually manage the container lifecycle later, pass the **`--no-restart`** flag:
>
> ```bash
> ldm config ssl-mode hosts my-project --no-restart
> ```

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

### Securing the Liferay Tunnel Personal Access Token (PAT)

Because `LFT_CLIENT_TOKEN` grants access to tunnel your local environments, it should be stored securely on your machine.

#### Option 1: The Restricted Secrets File (Recommended)

This approach stores the token in a startup configuration file but restricts its POSIX/Windows ACL permissions so other users or unauthorized local processes cannot read it.

##### On Linux / macOS (Zsh & Bash)

1. **Create the restricted directory and file:**

   ```bash
   mkdir -p ~/.config/lfr
   touch ~/.config/lfr/secrets
   chmod 600 ~/.config/lfr/secrets
   ```

   *(The `chmod 600` command ensures only the owner can read or write to this file).*

2. **Add the token variable to the file:**

   ```bash
   echo 'export LFT_CLIENT_TOKEN="your_actual_token_here"' >> ~/.config/lfr/secrets
   ```

3. **Source it on shell startup:**

   Add this to the bottom of your `~/.zshrc` or `~/.bashrc`:

   ```bash
   [ -f ~/.config/lfr/secrets ] && source ~/.config/lfr/secrets
   ```

##### On Windows (PowerShell)

1. **Create the secret file and restrict its ACLs:**

   Run these commands in PowerShell to create the file and strip away all permissions except for your explicit user account:

   ```powershell
   # Create the directory and file
   New-Item -ItemType Directory -Path "$HOME\.config\lfr" -Force
   $SecretFile = New-Item -ItemType File -Path "$HOME\.config\lfr\secrets.ps1" -Force

   # Restrict permissions so ONLY you can access it
   $Acl = Get-Acl $SecretFile.FullName
   $Acl.SetAccessRuleProtection($true, $false) # Break inheritance
   $User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
   $Rule = New-Object System.Security.AccessControl.FileSystemAccessRule($User, "FullControl", "Allow")
   $Acl.AddAccessRule($Rule)
   Set-Acl $SecretFile.FullName $Acl
   ```

2. **Add the token to the file:**

   ```powershell
   Set-Content -Path "$HOME\.config\lfr\secrets.ps1" -Value '$env:LFT_CLIENT_TOKEN="your_actual_token_here"'
   ```

3. **Load the secret automatically on startup:**

   Open your PowerShell profile (create it if missing via `New-Item -Type File -Path $PROFILE -Force`):

   ```powershell
   notepad $PROFILE
   ```

   Add this line to it:

   ```powershell
   if (Test-Path "$HOME\.config\lfr\secrets.ps1") { . "$HOME\.config\lfr\secrets.ps1" }
   ```

#### Option 2: The OS Credential Manager Route (Alternative)

This approach stores the token in the OS native credential store (encrypted at rest).

##### On Linux (Secret Service API / GNOME Keyring)

1. **Store the token:**

   ```bash
   secret-tool store --label="LFT Token" lfr tunnel_token
   # You will be securely prompted to enter the token
   ```

2. **Retrieve it automatically on shell startup:**

   Add this to your `~/.bashrc` or `~/.zshrc`:

   ```bash
   export LFT_CLIENT_TOKEN=$(secret-tool lookup lfr tunnel_token)
   ```

##### On Windows (Credential Manager)

1. **Store the token:**

   ```powershell
   cmdkey /generic:"LFT_CLIENT_TOKEN" /user:"$env:USERNAME" /pass:"your_actual_token_here"
   ```

2. **Retrieve it in your `$PROFILE`:**

   Since retrieving generic credentials in plain text via PowerShell can be verbose, the **Restricted Secrets File** approach (Option 1) is generally recommended for Windows CLI environments.

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

---

## 🛠️ LDM Sharing Command-Line Switches

When starting or running projects via LDM, you can configure the sharing behavior using the following CLI flags:

### 1. Stack Execution Flags (`ldm run`)

- **`--share`**: Enables automatic sharing. Starts a tunnel when the Liferay container reaches a healthy state.
- **`--share-provider <lfr-tunnel | lfr-tunnel-docker | ngrok>`**: Selects the sharing engine. (Defaults to `lfr-tunnel` host mode).
- **`--share-subdomain <name>`**: Specifies a custom subdomain (e.g., `pjrtest`). If omitted, defaults to the project name.
- **`--share-image <image>`**: Overrides the sidecar Docker image (useful for testing custom tunnel builds).
- **`--share-inspector`**: Maps the tunnel's local web dashboard to host port `4040` (allows visiting [http://localhost:4040](http://localhost:4040)).

### 2. Standalone Share Flags (`ldm share start`)

- **`--provider <lfr-tunnel | lfr-tunnel-docker | ngrok>`**: Explicitly selects the provider.
- **`--subdomain <name>`**: Requests a specific subdomain.
- **`--ports <ports>`**: Overrides target downstream port mapping (e.g. `--ports 8080,3000`).
- **`--image <image>`**: Overrides sidecar Docker image.
- **`--inspector`**: Exposes the Web Inspector Dashboard on host port `4040`.

---

## 🚀 Advanced `lfr-tunnel` Binary Switches (Go Client Native Flags)

If you are running the `lfr-tunnel` Go executable directly or writing custom scripts outside LDM's standard workflow, the native client binary supports the following switches:

### 1. Connection & Routing

- **`-server <url>`**: Specifies the `lfr-tunneld` gateway VPS URL (e.g. `-server https://tunnel.lfr-demo.se`).
- **`-token <auth-token>`**: Authenticates client credentials against the gateway.
- **`-subdomain <prefix>`**: Requests a prefix for public wildcard access (e.g. `-subdomain pjrtest`).
- **`-ports <port,port,...>`**: Comma-separated downstream local ports to bind/expose (e.g., `-ports 8080,3000`).
- **`-target-host <host/ip>`**: Points the tunnel upstream destination to a specific IP or hostname (defaults to `localhost`).
- **`-preserve-host`**: Ingests incoming Host headers down the socket directly instead of rewriting them to target host.

### 2. Dashboard & Security

- **`-inspector-port <port>`**: Sets a custom port to run the web inspector dashboard on (defaults to `4040`).
  - *Example:* `lfr-tunnel -ports 8080 -inspector-port 4045` runs dashboard on [http://localhost:4045](http://localhost:4045).
- **`-basic-auth <user:pass>`**: Activates basic authentication on the public HTTPS endpoints to protect your shared sandbox environment from unauthorized public access.
- **`-rate-limit <req/sec>`**: Restricts the maximum throughput routed down the tunnel to protect local servers (0 = unlimited).
- **`-add-header "Name: Value"`**: Automatically injects standard HTTP headers into downstream packets (e.g. `-add-header "X-Bypass-CORS: true"`).

### 3. Background Daemon Control

- **`-background`**: Fork/daemonize the client process into the background. Output logs are redirected to `~/.lfr-tunnel/client.log` and the process ID is tracked in `~/.lfr-tunnel/lfr-tunnel.pid`.
- **`-status`**: Introspects and outputs the running status/PID of the background daemon.
- **`-stop`**: Sends a shutdown signal (`SIGINT`) to clean up and close the background tunnel daemon.
- **`-upgrade`**: Triggers a self-upgrade sequence downloading the latest release binary.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-17* | *Last Reviewed: 2026-07-02*
