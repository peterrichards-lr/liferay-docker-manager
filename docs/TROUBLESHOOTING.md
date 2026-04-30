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

On older macOS versions (12.x and below), Colima relies on the QEMU backend which presents a "Catch-22" for Liferay Docker Manager:

1. **`sshfs` mounts**: Appear as read-only to the `liferay` container user (UID 1000), preventing the project from starting and blocking snapshots.
2. **`9p` mounts**: Allow write access but do **not** support POSIX file locking (`flock` / `fcntl`), causing databases like Elasticsearch to crash on boot with `AccessDeniedException` on `node.lock`.

Because LDM requires both writable bind mounts and POSIX file locking, **Colima on macOS 12 (Intel) is explicitly unsupported**.

#### **Solution: Use Docker Desktop or OrbStack**

To run LDM on macOS 12 Monterey, you must use a Docker provider that supports gRPC FUSE, `osxfs`, or a custom optimized filesystem:

1. **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** (Recommended for macOS 12)
2. **[OrbStack](https://orbstack.dev/)** (Lightweight alternative)

Once installed, simply uninstall Colima (`brew uninstall colima`) and start your new provider. LDM will detect it automatically.

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
