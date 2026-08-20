# Liferay Cloud PaaS Deployment (`ldm cloud deploy`)

The `ldm cloud deploy` command enables developers to deploy LDM projects, Liferay Workspace customizations, OSGi modules, portal configurations, and client extensions directly to Liferay Cloud PaaS environments.

---

## Deployment Modes

### 1. Git-Driven Jenkins Deployment (Default)

In the default Git-driven workflow, LDM injects metadata into `LCP.json` manifests, configures common Nginx response headers, and pushes workspace updates directly to the target environment branch. Liferay Cloud's managed Jenkins CI/CD pipeline compiles the build artifacts and deploys the release.

```bash
ldm cloud deploy my-project -e dev
```

Options:

- `--no-wait`: Initiate deployment asynchronously without tailing build compilation logs.
- `-g` / `--git`: Explicitly specify Git-driven deployment mode.

### 2. Direct Fast-Path CLI Deployment (`--direct`)

For rapid developer prototyping or urgent hotfixes, the `--direct` flag compiles local Docker service contexts and executes fast-path `lcp deploy` directly via the LCP CLI, bypassing Jenkins compilation.

```bash
ldm cloud deploy my-project -e dev --direct --service liferay
```

Options:

- `-d` / `--direct`: Enable fast-path deployment.
- `-s` / `--service`: Target specific Cloud service (default: `liferay`, or `webserver`, `db`, `search`, etc.).

---

## Go-Live Production Safety Checks (`prd`)

When deploying to a Production environment (`prd`), LDM enforces automated pre-flight safety locks:

1. **Clean Working Tree**: Ensures no uncommitted local git changes exist before initiating a release build.
2. **Interactive Confirmation**: Prompts the developer to manually type `prd` to confirm the deployment action.
3. **Non-Interactive Override**: In CI/CD pipelines (`-y` / `--non-interactive`), deployments to `prd` require the explicit `--force` flag.

```bash
ldm cloud deploy my-project -e prd --force -y
```

---

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-19* | *Last Reviewed: 2026-08-19*
