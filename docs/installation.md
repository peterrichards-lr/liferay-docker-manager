# Installation Guide

LDM can be installed either as a standalone binary or manually via Python for development.

## 1. Standalone Binary (Recommended)

The standalone binary is a single-file executable that includes all dependencies.

### macOS / Linux / WSL2

Download the latest `ldm` directly using your terminal:

```bash
# For macOS (Intel or Apple Silicon)
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos -o /usr/local/bin/ldm

# For Linux / WSL2
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-linux -o /usr/local/bin/ldm

# Make it executable
sudo chmod +x /usr/local/bin/ldm

# Verify
ldm --version
```

> [!TIP]
> **WSL2 Users:** Use the `ldm-linux` binary within your WSL terminal. Ensure your Docker Desktop is configured to "Use the WSL 2 based engine" and that integration is enabled for your specific distribution. LDM will automatically detect the Windows-side browser when launching URLs.

### Windows

Open PowerShell as an Administrator and run:

```powershell
# Create a bin folder if it doesn't exist
New-Item -ItemType Directory -Force -Path "$HOME\bin"

# Download the executable
Invoke-WebRequest -Uri "https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-windows.exe" -OutFile "$HOME\bin\ldm.exe"

# Add to your User PATH (one-time setup)
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$HOME\bin", "User")

# Verify (in a new terminal window)
ldm --version
```

---

## 2. Manual Installation (Development)

Clone this repository and use the provided wrapper script for your platform. The wrapper will automatically set up a local Python virtual environment and install the required dependencies on its first run.

### macOS / Linux / WSL2

```bash
git clone https://github.com/peterrichards-lr/liferay-docker-manager.git
cd liferay-docker-manager
./ldm --help
```

### Windows

```cmd
git clone https://github.com/peterrichards-lr/liferay-docker-manager.git
cd liferay-docker-manager
ldm.bat --help
```

---

## Prerequisites

- **Docker Engine**: Docker Desktop, Colima, or native WSL2.
- **Resources**: Recommended **4 CPUs and 8GB RAM** allocated to Docker.
  - *Note*: `ldm doctor` expects these minimums. If you allocate exactly 8GB, Docker may report ~7.7GB due to system overhead; the tool accounts for this by allowing a 7.5GB threshold.
- **Python**: 3.10+ (if not using binary)
- **SSL Tools**: `mkcert` and `openssl` are required for HTTPS support.

---

## 🛠️ Environment Setup & Troubleshooting

### Windows: Installing SSL Tools (Scoop)

To enable "Green Lock" SSL on Windows, we recommend using **Scoop** to manage `mkcert` and `openssl`. Run these commands in PowerShell:

```powershell
# 1. Install Scoop (The Package Manager)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser; iwr -useb get.scoop.sh | iex

# 2. Install Git (Required for Scoop Buckets)
scoop install git

# 3. Add the Extras Bucket (Required for mkcert)
scoop bucket add extras

# 4. Install SSL Tools
scoop install mkcert openssl

# 5. Initialize Local Trust Store
mkcert -install
```

### Docker Resource Alignment (Windows/WSL2)

If `ldm doctor` reports insufficient memory even though you have 8GB+ installed:

1. **Align Docker Desktop Settings**:
    - Open the Docker Desktop Dashboard.
    - Go to **Settings > Resources**.
    - Ensure "Resource Saver" mode is not aggressively capping memory.
    - Set the slider to at least **8GB**.

2. **The "Nuclear" WSL2 Restart**:
    If you've recently modified `.wslconfig`, you must force a full reload:

    ```powershell
    # In Windows PowerShell (not inside WSL)
    wsl --shutdown
    ```

    Wait 10 seconds, restart Docker Desktop, and run `ldm doctor` again.

### Increasing Resources in Colima (macOS)

If `ldm doctor` reports insufficient resources, restart Colima with higher limits:

```bash
colima stop
colima start --cpu 4 --memory 8
```

---

## 🛡️ Supported & Tested Environments

We maintain "Tier 1" support for the following configurations to ensure LDM works exactly as expected for Sales Engineers.

| Environment Type | Technology | Purpose |
| :--- | :--- | :--- |
| **Corporate Standard** | WSL2 + Docker Desktop | Verify Windows file system (`C:\Users`) mapping. |
| **"Pure" Linux** | VirtualBox + Ubuntu | Verify `chown` and Linux permissions. |
| **"Locked" SE** | Hyper-V + Windows 11 | Verify LDM in "Nested" environments (Common for cloud VMs). |
| **Modern Mac** | macOS + OrbStack | High-performance Apple Silicon testing. |
