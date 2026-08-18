# The First 5 Minutes: Getting Started with LDM

> [!NOTE]
> Welcome to Liferay Docker Manager (LDM)! This 5-minute interactive tutorial guides you from initial setup to running a local Liferay Portal/DXP environment, attaching Client Extensions, monitoring logs, and managing your stack.

---

## ⏱️ Step 1: Run the Interactive Onboarding Guide

LDM includes an interactive onboarding wizard built directly into the CLI. Run:

```bash
ldm guide
```

You will see an interactive menu covering:

1. **Quickstart Workflow**: Standard 4-step developer lifecycle.
2. **LDM Conventions & Defaults**: Out-of-the-box infrastructure settings (PostgreSQL, Elasticsearch, Traefik).
3. **Customizing Defaults**: Config precedence levels (CLI flags > local `.liferay-docker.meta` > global `~/.liferay-docker/config.json`).
4. **Client Extension Integration**: Hot-reloading client extension builds into running containers.
5. **Interactive Diagnostic Doctor**: Proactive environment verification (`ldm doctor`).

---

## 🚀 Step 2: Spin Up Your First Liferay Stack

To start a fresh Liferay DXP/Portal instance with auto-provisioned PostgreSQL database and Traefik routing, run:

```bash
ldm run
```

- **Interactive Prompts**: LDM will prompt for project name, Liferay version tag (defaults to latest LTS), database type, and search mode.
- **Non-Interactive Mode**: To accept all sane defaults automatically, pass the `-y` flag:

  ```bash
  ldm run my-project -y
  ```

Once complete, LDM will output your virtual hostname:

```text
✅ Project 'my-project' started in background.
ℹ Access your environment at: http://localhost:8080 or http://my-project.local
💡 Next step: Run 'ldm link <path-to-cx>' to attach client extensions, or 'ldm logs -f' to tail logs.
```

---

## 🔗 Step 3: Link a Client Extension Workspace

To attach a local Liferay Client Extension (CX) directory and enable live hot-reloading into your running Liferay container, run:

```bash
ldm link ../my-client-extension
```

LDM will:

1. Parse `client-extension.yaml` and `LCP.json`.
2. Configure dynamic routing subdomains (e.g. `http://my-cx.my-project.local:8080`).
3. Start watching for build outputs and synchronize them atomically.

---

## 📜 Step 4: Tail Container Logs

To monitor boot progress, OSGi bundle deployments, or client extension logs:

```bash
ldm logs -f
```

Press `Ctrl+C` at any time to detach without stopping the containers.

---

## ⏹️ Step 5: Stop or Manage Your Stack

When finished with your development session:

```bash
ldm stop
```

To list all registered projects and container statuses across your machine:

```bash
ldm list
```

To open the visual Web Dashboard:

```bash
ldm dashboard
```

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-18* | *Last Reviewed: 2026-08-18*
