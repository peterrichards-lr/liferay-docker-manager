# Liferay DXP Nightly & Master Builds Guide

This guide describes how to run, upgrade, and configure **Liferay DXP Nightly / Master builds** using Liferay Docker Manager (LDM) without compiling source code locally.

---

## 📌 Overview & Concept

When testing bleeding-edge features or validating bug fixes against the latest Liferay codebase, developers often need to run "master" builds.

Rather than compiling the Liferay source code locally, LDM leverages automated nightly builds published directly to Docker Hub ([`liferay/dxp`](https://hub.docker.com/r/liferay/dxp/tags)). These multi-architecture images (`amd64` and `arm64`) are built and published multiple times per day by Liferay release automation.

---

## 🚀 Quick Start: Running a Nightly Build

To launch a sandbox using the latest nightly build, use the `--nightly` (or `--master`) flag:

```bash
ldm run my-nightly-sandbox --nightly
```

Or pass a specific timestamped tag directly using `--tag`:

```bash
ldm run my-nightly-sandbox --tag 7.4.13.nightly-d10.0.72-20260730141335
```

> [!NOTE]
> **Vanilla Baseline Default**: Pre-warmed seed databases are not generated for moving nightly builds. When `--nightly` or `--master` is specified, LDM defaults to a clean vanilla database initialization (`--vanilla`) to prevent schema incompatibility errors.

---

## ⚙️ Multi-Level Configuration Cascading

If you frequently work with nightly builds, you can configure your preference at the machine, user, or project level so you do not need to specify `--nightly` on the CLI each time.

LDM resolves settings using the following precedence hierarchy (lowest to highest):

1. **Machine Level (`/etc/ldmrc`)**: System-wide default for workstation setups or CI build agents.

   ```bash
   ldm defaults set-global release_type nightly
   ```

2. **User Level (`~/.ldmrc`)**: Developer user preference across all local projects.

   ```bash
   ldm config set release_type nightly
   ldm config set auto_pull_nightly prompt
   ```

3. **Project Level (`[project]/.liferay-docker.meta`)**: Project-specific metadata overriding user/machine defaults.

   ```json
   {
     "project_name": "my-nightly-sandbox",
     "tag": "7.4.13.nightly",
     "release_type": "nightly",
     "auto_pull_nightly": "prompt"
   }
   ```

4. **CLI Flags (`--nightly` / `--tag`)**: Explicit command-line arguments override all config levels.

---

## 🔄 Upgrading Nightly Builds

Nightly builds on Docker Hub update under the floating `7.4.13.nightly` tag as new code commits land. LDM provides non-intrusive image pull options and automated schema upgrades:

### 1. On-Demand Image Pull

To pull the latest nightly image layers before starting your stack:

```bash
ldm start my-nightly-sandbox --pull
```

### 2. Startup Update Prompt

When `auto_pull_nightly` is set to `prompt` (default), starting a nightly sandbox will check Docker Hub for newer image digests and prompt:

```text
[!] A newer nightly build is available on Docker Hub (published 2 hours ago).
Would you like to pull the latest nightly image before starting? (y/N) [default: N]
```

Pressing `Enter` defaults to `No`, ensuring your local workflow is never interrupted unless requested.

### 3. Pre-Upgrade Backup & Schema Migration

When accepting a nightly build update, LDM's version upgrade pipeline ensures data safety:

1. **Pre-Upgrade Safety Backup**: Offers an automated database dump snapshot (`ldm snapshot`) prior to updating image layers.
2. **Schema Auto-Upgrade**: Enables Liferay's schema upgrade tool (`LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN=true`) during startup if database schema modifications were included in the nightly build.
3. **Persistent Volume Remapping**: Named Docker data volumes (`liferay-data`) and host bind mounts remain preserved across container recreations.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-31* | *Last Reviewed: 2026-07-31*
