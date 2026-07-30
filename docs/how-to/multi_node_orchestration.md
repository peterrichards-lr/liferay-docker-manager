# Multi-Node Orchestration & Remote Node Setup Guide

This guide details how to prepare remote Linux compute nodes, configure multi-node target registries, run remote workloads, and perform live workload migrations with Liferay Docker Manager (LDM).

---

## 1. Preparing a Remote Compute Node

LDM can orchestrate workloads on any remote machine (cloud VM, local Linux server, or WSL instance) reachable over SSH with Docker installed.

### Requirements on Remote Node

- **Operating System**: Linux (Ubuntu, Debian, RHEL, AlmaLinux, Fedora) or macOS.
- **Utilities**: `openssh-server`, `rsync` (or `tar`).
- **Docker**: Docker Engine and Docker Compose V2.
- **User Permissions**: SSH user must be a member of the `docker` group (non-sudo Docker access).

### Remote Node Setup Commands (Copy-Paste on Target Server)

```bash
# Update system package index and install prerequisites
sudo apt-get update && sudo apt-get install -y curl rsync ca-certificates gnupg

# Install Docker Engine and Docker Compose V2
curl -fsSL https://get.docker.com | sh

# Add your SSH user to the docker group
sudo usermod -aG docker $USER

# Apply group membership
newgrp docker
```

---

## 2. Remote Node Connectivity Verification

From your local machine, verify passwordless SSH and non-sudo Docker engine access:

```bash
# Test SSH access
ssh -i ~/.ssh/id_rsa user@remote-ip "echo 'SSH Connection OK'"

# Test non-sudo Docker access
ssh -i ~/.ssh/id_rsa user@remote-ip "docker info && docker compose version"
```

---

## 3. LDM Multi-Node Target Registry Management

Register and manage target compute nodes directly via the LDM CLI:

```bash
# 1. Register a new compute target node
ldm target add prod-node --host 34.200.10.5 --user ubuntu --key ~/.ssh/id_rsa

# 2. List target nodes and verify active target
ldm target ls

# 3. Test target connectivity and Docker engine health probe
ldm target status prod-node

# 4. Set default active target compute node
ldm target use prod-node
```

---

## 4. Remote Workload Deployment & Execution

Deploy and manage workloads on remote nodes seamlessly:

```bash
# Deploy and run project workload on remote node
ldm run my-project --target prod-node

# Stream remote container logs
ldm logs my-project --target prod-node

# Open interactive shell inside remote Liferay container
ldm shell my-project --target prod-node

# Run safe SELECT query against remote database
ldm db query "SELECT userId, emailAddress FROM User_" --target prod-node

# Lifecycle management on remote node
ldm stop my-project --target prod-node
ldm start my-project --target prod-node
ldm restart my-project --target prod-node
```

---

## 5. Live Target Workload Migration

Migrate a running workload live between target compute nodes with zero manual data loss:

```bash
# Migrate workload from local machine to remote compute node
ldm target migrate local prod-node

# Migrate workload from one remote node to another
ldm target migrate prod-node aws-west
```

### Migration Pipeline Overview

1. **Pre-flight Target Probe**: Verifies SSH and Docker connectivity on source and destination targets.
2. **Snapshot Creation**: Takes a database and named volume snapshot on the source node.
3. **Graceful Stack Stop**: Stops containers on the source node.
4. **Workspace Directory Sync**: Auto-syncs workspace files via `rsync` or `tar` stream.
5. **Target Metadata Reassignment**: Updates `.liferay-docker.meta` to point to the destination target.
6. **Workload Launch**: Spins up containers and verifies health on the destination compute node.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-30* | *Last Reviewed: 2026-07-30*
