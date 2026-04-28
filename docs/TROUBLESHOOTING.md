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

## 🔐 SSL & Certificates

### **Browser says "Not Secure" (WSL2)**

On WSL2, `mkcert -install` only trusts the CA *inside* Linux. You must also run it on the Windows side:

1. Open PowerShell as Administrator.
2. Install `mkcert` (via `choco install mkcert` or `scoop install mkcert`).
3. Run `mkcert -install`.
