# Troubleshooting Guide

This guide provides solutions for common issues encountered while using Liferay Docker Manager (LDM).

## 💾 Disk Space Issues (Elasticsearch Flood Stage)

If you see a warning about `flood stage disk watermark exceeded` in `ldm doctor` (or you receive a high disk space warning), Elasticsearch has marked your indices as **read-only** because your Docker environment is nearly full.

### **Reclaiming Space**

LDM includes a proactive check in `ldm doctor` to warn you about dangling resources. If warned, clean up unused LDM resources and Docker volumes:

```bash
# 1. Prune LDM orphaned resources (snapshots, certs, containers)
# Use --seeds and --samples to also clear large asset caches
ldm prune --seeds --samples

# 2. Prune unused Docker system resources
docker system prune -a --volumes
```

If Elasticsearch is still stuck in read-only mode after clearing space, you may need to restart the global search container:

```bash
ldm infra-restart --search
```

### **Moving Docker to an External Drive (macOS / Colima)**

If your internal disk is full and you have an external drive (e.g., `/Volumes/SanDisk`), you can move the entire Docker engine storage to it.

#### **Option A: Symbolic Link (Recommended for Persistence)**

This method "tricks" Colima into using your external disk without needing to manage environment variables.

1. **Stop Colima**:

    ```bash
    colima stop
    ```

2. **Move the existing data** (Optional: only if you want to keep your current images/containers):

    ```bash
    mv ~/.colima /Volumes/SanDisk/.colima
    ```

3. **Create the Symlink**:
    *CRITICAL: Ensure `~/.colima` does not exist before creating the link, or you will create a nested link.*

    ```bash
    rm -rf ~/.colima
    ln -s /Volumes/SanDisk/.colima ~/.colima
    ```

4. **Start Colima**:

    ```bash
    colima start
    ```

#### **Option B: Environment Variable**

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

3. **Start a new VM**:

    ```bash
    colima start --disk 100 --memory 8 --cpu 4
    ```

4. **Make it permanent**:
    Add `export COLIMA_HOME="/Volumes/SanDisk/colima"` to your `~/.zshrc` or `~/.bash_profile`.

### **Moving LDM Configuration & Search Data (~/.ldm)**

LDM stores shared infrastructure data (Elasticsearch indices, SSL certs, and project registries) in `~/.ldm`. This can grow over time.

To move it to an external drive:

1. **Stop all LDM projects**.
2. **Move the folder**:

    ```bash
    mv ~/.ldm /Volumes/SanDisk/.ldm
    ```

3. **Create the link**:

    ```bash
    ln -s /Volumes/SanDisk/.ldm ~/.ldm
    ```

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

## 🗄️ Database Issues

### **MySQL: Initialization Crash / "The designated data directory is unusable"**

If your database container (`<project>-db-1`) continuously crashes with logs indicating `Table 'mysql.plugin' doesn't exist` or `The designated data directory /var/lib/mysql/ is unusable`, the initial database setup was interrupted or failed (often due to an incorrect configuration flag for that specific version).

Because the initialization aborted, the Docker volume was corrupted.

#### **Solution: Wipe and Re-initialize**

1. Stop the broken project.
2. Remove the project and **explicitly delete its corrupted volumes**.
3. Run the project again to trigger a clean database initialization.

```bash
ldm stop <project>
ldm rm <project> --volumes
ldm run <project>
```

---

## 🐳 Docker Filesystem Errors

### **Docker: failed to Lchown / overlayfs: no such file or directory**

If your automated test or `ldm run` command crashes during the `Pulling` phase with a deep daemon error like:
`failed to extract layer (...) to overlayfs ... failed to Lchown ... no such file or directory`

This is a low-level Docker daemon failure completely unrelated to LDM. It happens when Docker's internal cache or filesystem (`overlayfs`) becomes corrupted or runs out of inodes/space inside the Linux Virtual Machine.

**Understanding Sparse Disks:**
Providers like Colima, Docker Desktop, and OrbStack use "Sparse Disks". Even if `colima list` shows a `100GiB` disk and you have terabytes of space on an external drive, Docker pulls images strictly into this internal virtual disk file. If an image pull gets interrupted or corrupted, a broken ghost file remains in the cache.

#### **Solution: Wipe the Corrupted Cache**

Run the native Docker command (or use `ldm prune`) to blast away the corrupted system cache and force a clean download:

```bash
docker system prune -a --volumes
```

*Note: This clears all unused images and volumes, resolving the corruption.*

---

## 🌐 Network & Connectivity

### **Windows/WSL2: Connection Refused (Exit Code 7)**

If LDM cannot connect to infrastructure services:

1. Ensure your WSL2 instance can reach the Windows host.
2. Check if a VPN or Firewall is blocking traffic on port 9200 (Search) or 443 (Proxy).
3. Run `ldm infra-restart` to reset the bridge.

## 📂 Permission & Mount Issues

### **macOS / ExFAT: "Unable to create lock manager" or "access_denied_exception"**

If Liferay fails to start with one of the following errors:

- `java.io.IOException: Unable to create lock manager` (Equinox / OSGi State)
- `access_denied_exception` on `write.lock` (Elasticsearch / Data)

This is caused by the **lack of POSIX file locking support** on ExFAT filesystems or limitations in the macOS-to-Linux filesystem sharing layer (VirtioFS/SSHFS). Liferay's core components require low-level mandatory file locks that these environments cannot provide for host-mapped directories.

#### **Solution: Use the Hybrid Mount Strategy (v2.7.2-beta.20+)**

LDM automatically resolves this by using **Docker Named Volumes** for the most sensitive directories. These volumes live entirely inside the Docker Linux VM's native filesystem.

1. **Verify your LDM version**: `ldm version` (Should be `v2.7.2-beta.20` or higher).
2. **Reset the project state** (if migrating from an older version):

    ```bash
    ldm stop <project>
    ldm rm <project> --volumes
    ldm run <project>
    ```

*Note: Your project code (`deploy/`, `modules/`, etc.) can remain on the external drive; only the internal state and search indices are moved to Docker internal storage.*

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

### **Windows/WSL: mkcert SSL "Not Secure" in Chrome**

When running LDM in WSL (Linux) but accessing the site via Chrome on the Windows host, the browser may report the connection as "Not Secure".
This happens because WSL and Windows use separate Certificate Authorities (CAs). `mkcert` in WSL generated a Linux root CA that Windows does not trust.

#### **Solution: Sync WSL to use the Windows CA**

The most robust solution is to point WSL's `mkcert` to the Windows trust store so that all certificates generated by LDM are automatically trusted by your Windows browsers.

1. Ensure `mkcert` is installed on the Windows host and initialized (`mkcert -install` in PowerShell).
2. Open your WSL terminal and set the `CAROOT` environment variable to point to the Windows `mkcert` directory. Replace `<YourWindowsUsername>` with your actual Windows username. **Note:** If your Windows username contains spaces (e.g., `John Smith`), you *must* wrap the path in quotes!

    ```bash
    export CAROOT="/mnt/c/Users/<YourWindowsUsername>/AppData/Local/mkcert"
    # Example with spaces: export CAROOT="/mnt/c/Users/John Smith/AppData/Local/mkcert"
    ```

3. Re-initialize `mkcert` in WSL so it registers the Windows CA in the Linux trust store:

    ```bash
    mkcert -install
    ```

4. To make this permanent, add the export to your WSL profile (`~/.bashrc` or `~/.zshrc`):

    ```bash
    echo 'export CAROOT="/mnt/c/Users/<YourWindowsUsername>/AppData/Local/mkcert"' >> ~/.bashrc
    ```

5. Finally, force LDM to regenerate the certificates for your project using the new CA:

    ```bash
    # Wipe the old linux-signed certs
    rm -rf ~/.ldm/infra/certs
    # Restart the proxy to regenerate
    ldm infra-restart --proxy
    ```

### **Windows: "charmap" codec can't encode character**

If you see Python encoding errors (like `'\u25cf' character maps to <undefined>`) in your terminal when running `ldm status` or `ldm doctor`:

1. **Upgrade to LDM `v2.4.26-beta.37` or later.** LDM now detects legacy terminal encodings and automatically switches to ASCII fallbacks (e.g., using `*` instead of `●`).
2. **Use a modern terminal.** We highly recommend **Windows Terminal** with PowerShell 7, which supports full Unicode output.

---

## 🔍 Search (Elasticsearch)

### **Elasticsearch: Failed to obtain node locks**

If Elasticsearch fails to boot with `AccessDeniedException: /usr/share/elasticsearch/data/node.lock`:

1. **Ensure LDM is up to date.** LDM `v2.7.2-beta.24+` includes **Just-in-Time (JIT) Permission Hardening**. If LDM hits a "Permission Denied" error on Linux/macOS while writing to a directory, it will automatically attempt to reclaim the folder's permissions via a temporary helper container before retrying.
2. **Fedora / Linux Workstations**: If Docker Desktop is not used, search indices are created as `root` by the Docker daemon. LDM's JIT hardening is specifically designed to resolve these ownership mismatches on native Linux filesystems.
3. **Manual Fix**: If the automated hardening is bypassed, ensure the host directory has open permissions:

```bash
sudo chmod -R 777 ~/.ldm/infra/search/data
```

### **Elasticsearch: Failed to parse mappings**

If Elasticsearch fails to start after a crash or version change due to mapping errors:

LDM includes an **Auto-Repair** feature. If the health check fails for 5 minutes, LDM will automatically wipe the `~/.ldm/infra/search/data` directory and perform a clean boot. You can trigger this manually by running:

```bash
ldm infra-down
rm -rf ~/.ldm/infra/search/data
ldm infra-setup --search
```

### **Elasticsearch: index_not_found_exception / Cascading OSGi Failures**

If Liferay's `ElasticsearchSearchEngine` fails to activate, causing dozens of cascading `NullPointerException` and `ServiceException` logs, the search index is likely missing or out-of-sync with the database. This commonly happens if a pre-warmed seed lacked the corresponding search snapshot.

#### **Solution 1: Surgical Search Reset (Recommended)**

If you want to keep your database intact but rebuild the broken search indices from scratch:

```bash
ldm stop <project>
ldm reset <project> global-search
ldm run <project>
```

#### **Solution 2: Total Project Reseed**

If the database and search index are completely desynchronized (often resulting in `NoSuchResourcePermissionException` logs), wipe all data and start fresh:

```bash
ldm stop <project>
ldm reseed <project>
```

---

## 🚀 LDM Self-Upgrade

### **Failed to check for updates (GitHub API Rate Limiting)**

If you are running LDM behind a corporate proxy, VPN, or shared public IP address, you might occasionally see the following error when checking for updates or upgrading:

```text
=== LDM Self-Upgrade ===
❌  Failed to check for updates.
ℹ  Please check your internet connection or try again later.
```

This occurs when the shared IP address hits the GitHub unauthenticated REST API limit (60 requests/hour).

**The Solution:** LDM automatically falls back to an HTML redirect check on `https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest` (which is not rate-limited). If the fallback also fails, verify your network connectivity or try again later.

---

## 🛡️ EDR / SentinelOne Quarantine (lfr-tunnel)

### **Issue: "Failed to verify lfr-tunnel installation after download"**

When using `--share` or `ldm share start` with the default `--share-provider lfr-tunnel`, LDM downloads a native host-side Go client executable (`lfr-tunnel`). Because this binary is compiled and downloaded dynamically, corporate Endpoint Detection and Response (EDR) platforms like **SentinelOne** or **Microsoft Defender** may flag it as an unsigned/untrusted executable and quarantine it.

This causes the executable to be deleted or blocked immediately after download, resulting in the error:
`❌ Failed to verify lfr-tunnel installation after download.`

### **Solution: Use the Containerized Provider (`lfr-tunnel-docker`)**

The easiest and safest workaround is to run `lfr-tunnel` inside a Docker container sidecar instead. EDR tools do not inspect or quarantine binaries running inside the isolated Docker Compose network:

1. Stop the current project.
2. Run the project with the `--share-provider lfr-tunnel-docker` parameter:

```bash
ldm run <project> --share --share-provider lfr-tunnel-docker --share-subdomain <subdomain>
```

Alternatively, if you are sharing a running project using `ldm share start`, explicitly request the Docker-based provider:

```bash
ldm share start <project> --provider lfr-tunnel-docker --subdomain <subdomain>
```

---

## 🔒 Project Concurrency Locks

### **Issue: "Concurrency Violation: Another instance of LDM is running on this project"**

If a previous `ldm` process was hard-killed (e.g. via `kill -9` or a VM reboot/OOM event) or crashed, the project concurrency lock file might remain on disk. While LDM includes **stale-lock auto-recovery** (automatically clearing the lock if the process is no longer running), in rare network/virtualized filesystem configurations the lock check can block subsequent commands.

**The Solution:**
You can force-clear the project lock using the rescue command:

```bash
ldm rescue --clear-lock [project]
```

Alternatively, you can manually delete the lock file located in your project work directory:

```bash
rm -f <project-path>/.liferay-docker/.ldm_lock
```

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-15* | *Last Reviewed: 2026-07-15*
