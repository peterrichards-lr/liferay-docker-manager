# Ngrok Integration: Exposing Liferay to the Internet

LDM includes native, zero-configuration integration with [ngrok](https://ngrok.com/) to securely expose your local Liferay environment to the public internet. This is essential for:

- Testing external webhooks (e.g., payment gateways, external CI pipelines).
- Integrating with SaaS services that require a callback URL.
- Sharing your local development environment with colleagues or clients.

## How It Works

Instead of routing traffic through your host machine's ports, LDM injects a dedicated **ngrok sidecar container** directly into your project's Docker Compose stack.

This sidecar creates a secure tunnel to ngrok's edge servers and routes the incoming traffic *internally* to LDM's Traefik SSL proxy over HTTPS. Because the connection between ngrok and Traefik remains encrypted and internal to the Docker network, **Client Extensions and custom domains work perfectly** without triggering certificate trust errors.

## Usage

To expose an LDM stack, simply add the `--expose` flag to your `ldm init` or `ldm run` command:

```bash
ldm run my-project --tag 2024.q4.0 --host-name forge.demo --expose
```

### Authentication

Ngrok requires an Auth Token to bind custom host headers and use HTTPS upstreams. The first time you use `--expose`, LDM will prompt you for your Auth Token:

1. Sign up or log in at [dashboard.ngrok.com](https://dashboard.ngrok.com/).
2. Navigate to **Getting Started** -> **Your Authtoken**.
3. Paste the token into the LDM prompt.

LDM will save this token securely in your global configuration (`~/.ldmrc`), so you will never be prompted for it again across any of your projects.

*(Note: If you use automation, you can also inject the token directly via the `NGROK_AUTHTOKEN` environment variable).*

## Connecting

Once the stack finishes booting, LDM will query the internal ngrok container and print your unique public URL directly to the terminal:

```text
🌍 Public ngrok Tunnel Active: https://a1b2c3d4.ngrok.app
```

Any traffic sent to `https://a1b2c3d4.ngrok.app` will be securely tunneled to `https://forge.demo` inside your Liferay container.

## Handling SaaS Callbacks (The Host Header)

By default, ngrok automatically rewrites the `Host` header to match your project's `--host-name` (e.g., `forge.demo`). This ensures that Traefik knows how to route the request to your specific Liferay container.

However, because Liferay "thinks" it is running at `forge.demo`, it may dynamically generate callback URLs or pagination links using `forge.demo` instead of your public `ngrok.app` domain. Since external SaaS services cannot resolve `forge.demo`, those links will fail.

**How to Fix This:**
If your SaaS integration requires Liferay to generate fully qualified URLs (e.g., OAuth redirects), you must temporarily tell Liferay to treat the ngrok URL as its primary domain:

1. Log into Liferay as an administrator.
2. Go to **Control Panel** -> **Instance Settings** -> **Instance Configuration**.
3. Under **Virtual Host**, enter your public ngrok domain (e.g., `a1b2c3d4.ngrok.app`).
4. Save the configuration.

Liferay will now generate all callback URLs using the public ngrok address.
