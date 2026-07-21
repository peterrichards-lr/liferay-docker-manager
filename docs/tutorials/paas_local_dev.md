# Liferay Cloud PaaS to Local Environment

## The Demo Rescue Strategy

**The Scenario:** You have a critical demo with a prospect, but the Liferay PaaS environment (SSA) goes down or becomes unstable. You don't have time to wait days for a support ticket to be resolved.

**The Solution:** Liferay Docker Manager (LDM) acts as your ultimate fallback. In minutes, you can pull the prospect's real code and live data down to your laptop, spin up an exact local replica, and run the demo flawlessly from your own machine.

This guide outlines the "Golden Path" for mirroring a Liferay Cloud PaaS environment locally.

---

## Understanding the Boundaries

To successfully replicate a PaaS environment, you must understand how Liferay Cloud separates concerns, and how that maps to your local machine:

1. **Code & Configuration (Managed by Git):** Your custom OSGi modules, Client Extensions, and environment variables (e.g., `portal-ext.properties`) live in the prospect's Liferay Cloud Git repository.
2. **Data & State (Managed by LCP Backups):** The database and the Document Library (volume) live in the remote cloud infrastructure.

LDM respects this boundary: **Git manages the code versioning; LDM manages the local runtime and data.**

---

## The 4-Step Golden Path

Follow these steps to create a 1:1 local replica of any Liferay Cloud environment.

### Step 1: Bring Your Code (Git)

First, clone the prospect's Liferay Cloud Git repository to your local machine.

```bash
# Clone the repository to a dedicated folder
git clone git@github.com:my-org/prospect-paas-repo.git
```

### Step 2: Initialize the Local Runtime & Hydrate Data (LDM)

Use LDM to ingest the code and start the local Docker containers. The `ldm link` command will scan the repository, build any modules, and set up a live-syncing workspace.

```bash
# Link LDM to the cloned repository
ldm link ./prospect-paas-repo
```

Because LDM recognizes the Liferay Cloud workspace structure, it will automatically launch an interactive wizard during initialization:

```text
> Detected Liferay Cloud Workspace structure.
❓ Would you also like to pull the remote database and document library to complete the local replica? [Y]: y
❓ Which environment would you like to mirror (e.g., prd, uat): prd
```

*Note: If your repository does not contain an `LCP.json` file at its root, LDM will ask you to provide the Liferay Cloud Project ID manually.*

By answering the prompts, LDM will automatically orchestrate the download and restoration of the real data and environment variables from the remote environment directly into your local containers.

---

## You Are Ready

Your local LDM instance is now an exact, running replica of the Liferay Cloud PaaS environment.

* You can continue developing code in your local `./prospect-paas-repo` folder; LDM will hot-reload the changes automatically.
* You can confidently deliver your presentation using real prospect data, entirely immune to remote network outages.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-21* | *Last Reviewed: 2026-07-02*
