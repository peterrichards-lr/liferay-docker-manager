# End-to-End Testing with LDM

Liferay Docker Manager (LDM) provides an excellent orchestration layer for testing complex Liferay setups, such as Client Extensions, Headless APIs, and custom fragments. By using LDM, you can automate the provisioning of a fully-configured Liferay environment within your CI/CD pipelines or locally.

## Why use LDM for Testing?

- **Automated Orchestration**: Spin up Liferay, databases, Search, and Traefik routing in a single command.
- **Dynamic Routing**: Automatic reverse-proxying of Microservice Client Extensions via `*.lfr.cloud` subdomains.
- **Pre-warmed Seeds**: drastically reduce startup times using `.ldmp` snapshot packages.
- **Deterministic Teardown**: easily nuke the environment between runs to prevent state leakage.

## Local E2E Testing Workflow

When developing Client Extensions locally (e.g., Liferay AI Commerce Accelerator or Fragment repositories), you can use LDM to quickly spin up your test rig:

1. **Import the Workspace**
   Run the following command at the root of your Client Extension workspace. This will build the workspace using the local `gradlew` or `npm` toolchain and automatically map the deployed OSGi and CX modules to the Liferay container.

   ```bash
   ldm import . --build
   ```

2. **Wait for Health**
   LDM will automatically block and tail logs until Liferay and its dependencies report as healthy.

3. **Run Tests**
   You can now execute your E2E test suite (e.g., Playwright, Cypress, or pytest).
   - Liferay is accessible at `http://localhost:8080`.
   - Microservices are accessible via their respective Traefik subdomains (e.g., `https://my-microservice.lfr.cloud` if you are using `--share` or using `--ssl`).

4. **Teardown**
   Once tests complete, safely destroy the environment to reclaim resources.

   ```bash
   ldm nuke --force
   ```

## CI/CD Pipeline Integration

You can easily use LDM inside GitHub Actions to run your test suite automatically. Since CI environments require non-interactive execution, always use the `-y` or `--non-interactive` flag.

```yaml
name: E2E Test Suite

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Setup Java
        uses: actions/setup-java@v4
        with:
          java-version: '21'
          distribution: 'temurin'

      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: '22'

      - name: Install LDM
        run: |
          pipx install liferay-docker-manager
          ldm dev-setup -y

      - name: Import Workspace & Start Environment
        run: |
          # The --leave-running flag ensures the containers stay up for the tests
          ldm import . --build --non-interactive --leave-running

      - name: Run Playwright Tests
        run: |
          npm ci
          npx playwright test

      - name: Teardown Environment
        if: always()
        run: |
          ldm nuke --force -y
```

## Using Pre-Warmed Snapshot Packages

If your tests require a specific database state (e.g., test users, pre-configured instances), you should create an `.ldmp` package locally containing your desired database state.

1. **Create the Package Locally**:

   ```bash
   ldm package --export-database --name "e2e-seed"
   ```

2. **Hydrate in CI**:
   You can then pass the package URL or file path during import.

   ```bash
   ldm import . --build --non-interactive --leave-running --hydrate-url "https://github.com/your-org/repo/releases/download/v1.0/e2e-seed.ldmp"
   ```

This completely eliminates the need for expensive API-driven setup scripts before running your tests.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-20* | *Last Reviewed: 2026-07-08*
