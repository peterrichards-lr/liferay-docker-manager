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

### Universal Platform Compatibility

LDM multi-node orchestration can be used **anywhere Docker and SSH are running**. This includes:

- **Cloud Infrastructure**: AWS EC2, GCP Compute Engine, Azure VMs, DigitalOcean Droplets.
- **Windows Compute Nodes**: Windows 10/11 running WSL2 (Ubuntu / Debian).
- **Local Infrastructure**: LAN Linux servers, macOS nodes, local VMs, bare-metal hardware.

### Remote Node Setup Commands

Choose the package manager appropriate for your Linux distribution:

#### Option A: Amazon Linux 2023 / RHEL / Fedora (`dnf`)

```bash
# Update system package index and install Docker & rsync
sudo dnf install -y docker rsync

# Enable and start Docker service
sudo systemctl enable --now docker

# Add your SSH user (e.g. ec2-user) to the docker group
sudo usermod -aG docker $USER
newgrp docker
```

#### Option B: Ubuntu / Debian (`apt-get`)

```bash
# Update system package index and install prerequisites
sudo apt-get update && sudo apt-get install -y curl rsync ca-certificates gnupg

# Install Docker Engine and Docker Compose V2
curl -fsSL https://get.docker.com | sh

# Add your SSH user (e.g. ubuntu) to the docker group
sudo usermod -aG docker $USER
newgrp docker
```

### Platform Example 1: AWS EC2 Compute Node Setup (Amazon Linux 2023 / Ubuntu)

1. **Security Group Config**: Inbound rules needed:
   - **TCP Port `22` (SSH)**: Required for LDM orchestrator control plane (`ldm target`). Restrict to your IP or VPN for security.
   - **TCP Port `80` (HTTP) & Port `443` (HTTPS)**: Optional. Required ONLY for direct browser access. Can be closed if using outbound tunnels (`lfr-tunnel-docker`).
2. **Key Pair**: Download the `.pem` key (e.g. `aws-key.pem`) and set permissions (`chmod 400 aws-key.pem`).
3. **Register AWS Node**:

   ```bash
   # Amazon Linux 2023 uses ec2-user (Ubuntu uses ubuntu)
   ldm target add aws-1 --host 34.200.10.5 --user ec2-user --key ~/.ssh/aws-key.pem
   ldm target status aws-1
   ```

### Platform Example 2: Windows WSL2 Compute Node Setup

1. **Enable OpenSSH inside WSL2**:

   ```bash
   sudo apt-get install -y openssh-server rsync
   sudo service ssh start
   ```

2. **Add Local Public Key to WSL2**: Append your SSH public key (`~/.ssh/id_rsa.pub`) to `~/.ssh/authorized_keys` in WSL2.
3. **Register Windows WSL2 Node**:

   ```bash
   ldm target add win-wsl --host 192.168.1.50 --user developer --key ~/.ssh/id_rsa
   ldm target status win-wsl
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
