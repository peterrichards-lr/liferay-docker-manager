# Troubleshooting Guide

This guide provides solutions for common issues encountered while using Liferay Docker Manager (LDM).

## 💾 Disk Space Issues (Elasticsearch Flood Stage)

If you see a warning about `flood stage disk watermark exceeded` in `ldm doctor`, Elasticsearch has marked your indices as **read-only** because your Docker environment is nearly full.

### **Reclaiming Space**

Before moving data, try cleaning up unused Docker resources:

```bash
docker system prune -a --volumes
```

### **Moving Docker to an External Drive (macOS / Colima)**

If your internal disk is full and you have an external drive (e.g., `/Volumes/SanDisk`):

1. **Stop and Delete the current environment**:

    ```bash
    colima stop
    colima delete
    ```

2. **Point Colima to the External Disk**:

    ```bash
    export COLIMA_HOME="/Volumes/SanDisk/colima"
    mkdir -p $COLIMA_HOME
    ```

3. **Start a new, larger VM**:

    ```bash
    colima start --disk 100 --memory 8 --cpu 4
    ```

4. **Make it permanent**:
    Add `export COLIMA_HOME="/Volumes/SanDisk/colima"` to your `~/.zshrc` or `~/.bash_profile`.

### **Moving Docker to an External Drive (Windows / WSL2)**

Docker Desktop on Windows stores data in a WSL2 VHDX file. To move it:

1. **Shutdown Docker Desktop**.
2. **Export the data**:

    ```powershell
    wsl --export docker-desktop-data "D:\docker-data.tar"
    wsl --unregister docker-desktop-data
    ```

3. **Import to the new location**:

    ```powershell
    wsl --import docker-desktop-data "D:\Docker" "D:\docker-data.tar" --version 2
    ```

4. **Restart Docker Desktop**.

---

## 🌐 Network & Connectivity

### **Windows/WSL2: Connection Refused (Exit Code 7)**

If LDM cannot connect to infrastructure services:

1. Ensure your WSL2 instance can reach the Windows host.
2. Check if a VPN or Firewall is blocking traffic on port 9200 (Search) or 443 (Proxy).
3. Run `ldm infra-restart` to reset the bridge.

## 📂 Permission & Mount Issues

### **macOS 12 Monterey (Intel): Read-Only Volumes**

On older macOS versions (12.x and below) using the QEMU backend, standard `sshfs` mounts may appear as read-only to the `liferay` container user (UID 1000), even if they are writable by you.

**Signs:**

- Errors like `FATAL: VOLUME MOUNT IS READ-ONLY` during `ldm run`.
- Permission denied errors when creating snapshots.

#### **Solution 1: Switch to 9p Mounts (Recommended)**

The `9p` protocol is often more reliable for permissions on macOS 12 Intel.

```bash
colima stop
colima start --mount-type 9p --mount /Users/$(whoami):w
```

#### **Solution 2: Force Writable Flag**

Ensure your Colima configuration explicitly enables write access. Run `colima edit` and ensure the `mounts` section is configured:

```yaml
mounts:
  - location: /Users/[username]
    writable: true
```

---

## 🖥️ Platform Specifics

### **Windows: "charmap" codec can't encode character**

If you see Python encoding errors (like `'\u25cf' character maps to <undefined>`) in your terminal when running `ldm status` or `ldm doctor`:

1. **Upgrade to LDM `v2.4.26-beta.37` or later.** LDM now detects legacy terminal encodings and automatically switches to ASCII fallbacks (e.g., using `*` instead of `●`).
2. **Use a modern terminal.** We highly recommend **Windows Terminal** with PowerShell 7, which supports full Unicode output.

---

## 🔍 Search (Elasticsearch)

### **Elasticsearch: Failed to obtain node locks**

If Elasticsearch fails to boot with `AccessDeniedException: /usr/share/elasticsearch/data/node.lock`:

1. **Ensure LDM is up to date.** LDM `v2.4.26-beta.38+` includes an automated "Permission Reclamation" step that runs `chown -R 1000:1000` inside the volume via a helper container.
2. **Manual Fix:** If using a custom mount, ensure the host directory has open permissions:

```bash
chmod -R 777 ~/.ldm/infra/search/data
```

### **Elasticsearch: Failed to parse mappings**

If Elasticsearch fails to start after a crash or version change due to mapping errors:

LDM includes an **Auto-Repair** feature. If the health check fails for 5 minutes, LDM will automatically wipe the `~/.ldm/infra/search/data` directory and perform a clean boot. You can trigger this manually by running:

```bash
ldm infra-down
rm -rf ~/.ldm/infra/search/data
ldm infra-setup --search
```
