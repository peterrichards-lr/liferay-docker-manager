# LDM Runtime Overrides & Fragment Substitutions

Liferay Docker Manager (LDM) provides powerful dynamic substitution capabilities during the `ldm import` runtime injection step. This allows `.ldmp` packages to adapt to the developer's specific local environment (e.g., custom domains, `localhost` port bindings, or shared infrastructure tunnels) automatically.

When a `.ldmp` package contains an `.ldm/fragment-overrides.json` file, LDM processes this JSON file immediately after importing the workspace and starting the Liferay container. It uses Python's `string.Template` engine to perform variable expansion on all values before using Liferay's Headless APIs to patch the Fragment Entry or Custom Element configurations.

## Available Variables for Substitution

The following variables are available to be referenced using standard shell syntax (e.g., `${VARIABLE_NAME}`) in your `fragment-overrides.json`.

### 1. Client Extension Routing Variables

These are the most commonly used variables for routing frontend components to backend microservices.

* `LIFERAY_EXTERNAL_URL_CLIENT_EXTENSION_[ID]`
  * **Description**: The absolute, public-facing URL mapped to the microservice by LDM's Traefik reverse proxy (or direct port binding on `localhost`).
  * **Usage**: Use this when a *frontend* component (running in the user's browser) needs to connect to the microservice. This guarantees the correct protocol (`http`/`https`), subdomain, and port based on the host environment's SSL and routing configurations.
  * **Example**: `https://ai-commerce-accelerator-microservice.aica.local`

* `LIFERAY_ROUTES_CLIENT_EXTENSION_[ID]`
  * **Description**: The internal Docker network route to the client extension backend (e.g., `http://[container-name]:[port]`).
  * **Usage**: This variable is natively provided by Liferay and is intended for *backend* or internal Liferay communication (such as OAuth2 application integrations or Liferay objects calling out to the extension). **Do not use this for frontend requests**, as the browser cannot resolve internal Docker container hostnames.
  * **Example**: `http://aica-ai-commerce-accelerator-microservice:3001`

*(Note: Replace `[ID]` with the normalized uppercase ID of your client extension. Any hyphens `-` in the ID are converted to underscores `_`.)*

### 2. Standard LDM Metadata Variables

LDM explicitly injects a suite of standard metadata variables into the expansion environment, allowing fragments to configure themselves based on the active LDM project settings.

* `LDM_PROJECT_ID`
  * **Description**: The internal, sanitized name/ID of the LDM project.
  * **Example**: `aica`
* `LDM_HOST_NAME`
  * **Description**: The primary host domain assigned to the project.
  * **Example**: `aica.local` (or `localhost`)
* `LDM_SSL_ENABLED`
  * **Description**: A boolean string indicating whether SSL and Traefik routing is enabled for the LDM project.
  * **Example**: `true` or `false`
* `LDM_HTTP_SCHEME`
  * **Description**: The active HTTP scheme based on the SSL configuration.
  * **Example**: `https` or `http`
* `LDM_BASE_URL`
  * **Description**: The fully qualified base URL to reach the Liferay instance.
  * **Example**: `https://aica.local` (or `http://localhost:8080`)

### 3. Native Docker Container Environment Variables

In addition to the variables listed above, LDM parses the active Liferay container's entire environment stack. This means **any environment variable present inside the Liferay container** is also available for substitution in your `fragment-overrides.json`.

* **Example**: `${LIFERAY_WORKSPACE_ENVIRONMENT}` could resolve to `dev`, `local`, `uat`, or `prd`.

## How the override is applied

LDM tries three routes in order, stopping at the first that works.

### 1. Headless API

`PATCH /o/headless-delivery/v1.0/page-elements/{id}` — the supported path, and
the only one needing no extra setup. It works on ordinary pages.

It fails on **published site initializer pages**, which reject specification
updates with HTTP 400 `UnsupportedOperationException` (upstream
[LPD-99955](https://liferay.atlassian.net/browse/LPD-99955)). Packages built
from a site initializer — the AI Commerce Accelerator among them — hit this
every time, which is why the routes below exist.

### 2. The `fragment-override` OSGi module *(recommended, opt-in)*

Runs inside the portal, so the update goes through
`FragmentEntryLinkLocalService` and Liferay invalidates its own cache. **No
restart.** It merges your overrides over the existing values, leaving every key
you did not mention untouched.

Two one-time steps, both deliberate — LDM does not do either for you:

```bash
# 1. Fetch the bundle and deploy it (v2.0.0 or later)
gh release download v2.0.0 \
  --repo peterrichards-lr/liferay-custom-osgi-modules \
  --pattern 'com.liferay.fragment.override*'

shasum -a 256 -c com.liferay.fragment.override-*.sha256

ldm deploy <project> com.liferay.fragment.override-2.0.0-dxp-2026.q1.12-lts.jar
```

```properties
# 2. In files/portal-ext.properties
feature.flag.LPD-99955=true
```

**Require v2.0.0 or later.** v1.0.0 replaced `editableValues` wholesale, so a
partial override destroyed every other value on the fragment.

**Match the DXP line.** The bundle declares bounded OSGi ranges and resolves
only on the line named in its filename. Deployed against another line it will
not start, and LDM falls through to route 3.

### 3. Direct database patch *(last resort)*

Used only when both routes above fail. It works, but it is the weakest option
by some distance:

* a regex over a JSON column, so a value containing a quote can corrupt it
* an unscoped `WHERE` — every matching row in the instance is rewritten
* PostgreSQL and MySQL only; Hypersonic projects get a warning and no override
* **it cannot apply to a running portal.** Liferay caches fragment
  configuration in memory and no Gogo command can invalidate it, so the change
  is invisible until you `ldm restart`.

Route 2 exists to avoid all four.

## Example Usage

Create an `.ldm/fragment-overrides.json` file in the root of your project workspace. This file maps the Fragment `externalReferenceCode` to the specific configuration properties you want to update.

```json
{
    "AICA-SEARCH-BAR-FRAGMENT": {
        "microserviceUrl": "${LIFERAY_EXTERNAL_URL_CLIENT_EXTENSION_AI_COMMERCE_ACCELERATOR_MICROSERVICE}",
        "environment": "${LIFERAY_WORKSPACE_ENVIRONMENT}",
        "gatewayHost": "${LDM_BASE_URL}"
    }
}
```

When LDM imports this `.ldmp` package, it will dynamically calculate these values and update the fragment configuration in Liferay via the headless API.

## When the Headless API Refuses: the Database Fallback

On **published Site Initializer pages**, Liferay rejects `PUT` on page specifications with `UnsupportedOperationException` — an upstream limitation tracked in [#883](https://github.com/peterrichards-lr/liferay-docker-manager/issues/883) ([LPD-99955](https://liferay.atlassian.net/browse/LPD-99955)).

When the headless attempt patches nothing, LDM falls back to updating `fragmententrylink.editablevalues` directly in PostgreSQL/MySQL.

> [!IMPORTANT]
> **A database-fallback patch requires a restart to become visible.** Liferay holds fragment configuration in memory, so the updated rows are not served until the portal reloads them:
>
> ```bash
> ldm restart <project>
> ```
>
> LDM tells you when this applies. Note that whether the old value is still on screen depends on whether that page had already been rendered and cached, so an un-restarted patch can *appear* to have worked intermittently — always restart before concluding an override is broken.

Two further limitations of the fallback are worth knowing, since it is a regex rewrite rather than a JSON-aware edit:

* A setting whose **current value is empty** (`"key":""`) is not matched and stays unpatched — LDM reports `0 rows` for it.
* The `WHERE` clause matches on the key name alone, so a **generic key** (e.g. `url`) is rewritten in every fragment carrying that key across the whole instance. Prefer distinctive key names.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-09-04* | *Last Reviewed: 2026-09-04*
