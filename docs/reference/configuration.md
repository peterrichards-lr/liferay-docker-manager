# Configuration Guide

## Cascading Defaults

LDM uses a robust cascading configuration system to ensure consistent, reproducible environments while offering flexibility across system, user, and project levels. This architecture prevents a project's foundational settings from mysteriously changing if global configurations are updated later.

### The Resolution Hierarchy

When LDM needs a default value (e.g., for the Liferay tag, database type, or port), it resolves it in the following order (highest to lowest priority):

1. **Project Metadata (`.liferay-docker.meta`)**: Once a project is created (via `init`, `run`, or `hydrate`), the resolved settings are permanently "frozen" into the project's metadata. This ensures stability.
2. **Workspace Defaults (`.ldmrc`)**: A committed config file at the root of the Liferay Workspace. During project initialization (e.g., via `ldm link` or `ldm import`), LDM automatically reads these settings to seed the default metadata.
3. **User Defaults (`~/.ldmrc`)**: Custom defaults specific to the current developer's machine.
4. **Global Defaults (`/etc/ldmrc`)**: System-wide defaults, typically managed by system administrators or CI/CD provisioning scripts.
5. **Convention Defaults**: Hardcoded fallback values within the LDM source code (e.g., Port `8080`, DB `postgresql`, Search `sidecar`).

### Managing Defaults

You can view and manage these settings interactively or programmatically using the `ldm defaults` command.

```bash
# View the current resolution tree for all configuration keys
ldm defaults

# Set a custom user-level default (e.g., always prefer MySQL)
ldm defaults db_type mysql

# Set a system-wide default (requires appropriate permissions)
sudo ldm defaults port 9090 --global

# Remove a custom default to fall back to the convention
ldm defaults --remove tag
```

## Configuration Files

- **`logging.json`**: Managed via `log-level` command.
- **`common/`**: Files here (configs, XML licenses, LPKG files) are synced to all project stacks.
- **`services/`**: Place standalone `Dockerfile` directories here for orchestration.

## Shared Configuration (`LDM_COMMON_DIR`)

By default, LDM uses a `common/` directory located in the project's parent, the current working directory, or `~/.ldm/common/` to store shared configurations and licenses.

If you need to use a specific, shared configuration across multiple independent project directories or CI pipelines, you can override this by setting the `LDM_COMMON_DIR` environment variable:

```bash
# Example: Point LDM to a shared organization config folder
export LDM_COMMON_DIR="/path/to/shared/organization/common"
ldm run my-project
```

## Environment Variable Forwarding

LDM automatically forwards specific host environment variables into your project containers using a prefix-based logic.

### 1. Global Prefix Stripping (`LDM_`)

Any host variable starting with `LDM_` is forwarded to **all** containers in the stack with the prefix removed. This is the recommended way to inject global configurations.

- **Host**: `export LDM_COMPANY_ID=123`
- **Container**: `COMPANY_ID=123`

### 2. Automatic Passthrough (AI & Liferay Cloud)

To ease CI integration, LDM automatically forwards variables from known providers as-is (preserving the prefix) to all containers:

- **Liferay Cloud**: `LXC_`, `COM_LIFERAY_LXC_`
- **AI Providers**: `OPENAI_`, `GEMINI_`, `ANTHROPIC_`, `MISTRAL_`

### 3. Custom Passthrough Prefixes

You can extend the automatic passthrough list by setting `LDM_FORWARD_PREFIXES` on your host:

- **Host**: `export LDM_FORWARD_PREFIXES="AWS_,STRIPE_"`
- **Result**: Any variable starting with `AWS_` or `STRIPE_` will be forwarded to all containers.

### 4. Service-Specific Targeting

You can target a specific service (including Client Extensions) by prefixing the variable with the **Service ID** (uppercased, with dashes replaced by underscores):

- **Service ID**: `my-custom-extension`
- **Host Variable**: `export MY_CUSTOM_EXTENSION_DEBUG=true`
- **Container** (`my-custom-extension` only): `DEBUG=true`

---

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-16* | *Last Reviewed: 2026-07-02*
