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

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-16* | *Last Reviewed: 2026-07-07*
