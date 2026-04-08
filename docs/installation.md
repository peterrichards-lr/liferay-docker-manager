# Installation Guide

LDM can be installed either as a standalone binary or manually via Python for development.

## 1. Standalone Binary (Recommended)

The standalone binary is a single-file executable that includes all dependencies.

### macOS / Linux / WSL2

Download the latest `ldm` directly using your terminal:

```bash
# For macOS (Intel or Apple Silicon)
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos -o /usr/local/bin/ldm

# For Linux / WSL2 (Native Linux)
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-linux -o /usr/local/bin/ldm

# Make it executable
sudo chmod +x /usr/local/bin/ldm

# Verify
ldm --version
```

> [!TIP]
> **WSL2 Users:** Use the `ldm-linux` binary within your WSL terminal. To enable SSL, you **must** install `mkcert` inside the Linux environment (`sudo apt update && sudo apt install libnss3-tools`) and run `mkcert -install` there. LDM will automatically detect the Windows-side browser when launching URLs.

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

```powershell
git clone https://github.com/peterrichards-lr/liferay-docker-manager.git
cd liferay-docker-manager
# Note: On Windows, use the ldm.bat wrapper or the local python script
.\ldm.bat --help
```

---

## Prerequisites

- **Docker Engine**: Docker Desktop, Colima, or native WSL2.
- **Resources**: Recommended **4 CPUs and 8GB RAM** allocated to Docker.
  - *Note*: `ldm doctor` expects these minimums. If you allocate exactly 8GB, Docker may report ~7.7GB due to system overhead; the tool accounts for this by allowing a 7.5GB threshold.
- **Python**: 3.10+ (if not using binary)
- **SSL Tools**: `mkcert` and `openssl` are required for HTTPS support.
  - **Windows Users**: Install **Git for Windows** or use **Scoop** (`scoop install openssl`) to provide the required `openssl` command.

---

## 🛠️ Environment Setup & Troubleshooting

### Windows: Installing SSL Tools (Scoop)

To enable "Green Lock" SSL on Windows, we recommend using **Scoop** to manage `mkcert` and `openssl`. Run these commands in PowerShell:

```powershell
# 1. Install Scoop (The Package Manager)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser; iwr -useb get.scoop.sh | iex

# 2. Install Git (Required for Scoop Buckets and OpenSSL)
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

## 🐳 Colima (Advanced macOS Setup)

Colima is a lightweight, open-source alternative to Docker Desktop. While highly performant on Apple Silicon, it is much stricter regarding file sharing and permissions.

### 1. Recommended Start Command

For the best compatibility with Liferay and SSCE build processes, we recommend using the macOS Virtualization Framework (`vz`) with **VirtioFS**.

```bash
# Delete existing instance if it's misbehaving
colima stop
colima delete

# Start with optimized settings
# Note: We mount both home AND the Volumes directory for external disks (e.g. SanDisk)
colima start --cpu 4 --memory 8 --vm-type=vz --mount-type=virtiofs --mount /Users/$(whoami):w --mount /Volumes:w
```

### 2. The "Ghost Mount" Issue

If LDM reports `FATAL: VOLUME MOUNTING IS BROKEN`, it means Colima's VM can see the folder but cannot see the files inside it.

**The Fix**: Ensure your home directory is explicitly mounted with write permissions (`:w`). If your project is on an external volume, add it to the mount list using the command above.

### 3. Permissions

Unlike Docker Desktop, Colima does not "mask" file owners. LDM automatically handles this by running a **Permission Fixer** before every stack startup to ensure the `liferay` user (UID 1000) has access to your host files. **Note:** LDM is smart enough to skip the `.git` directory to preserve your repository metadata permissions.

---

### 🌐 DNS & Subdomain Configuration

Server-Side Client Extensions (SSCE) in LDM use subdomains (e.g., `https://my-ext.forge.demo`) for routing. Because these are local domains, your operating system needs to be told to route them to your machine.

#### 1. Manual Mapping (The Standard Way)

You must add each domain and subdomain to your system's `hosts` file.

- **macOS / Linux**: Edit `/etc/hosts` using sudo:

  ```bash
  sudo nano /etc/hosts
  ```

  Add a line like this:

  ```text
  127.0.0.1 forge.demo ecopulse-microservice.forge.demo ecopulse-theme.forge.demo
  ```

- **Windows**: Edit `C:\Windows\System32\drivers\etc\hosts` as an Administrator.

> [!TIP]
> LDM will proactively check your DNS configuration during `ldm run` or `ldm doctor` and provide the exact line you need to copy-paste if any subdomains are missing.

#### 2. Wildcard Mapping (The "Set and Forget" Way)

If you frequently add new client extensions, we recommend using a local DNS proxy like **dnsmasq** to resolve all `*.demo` domains to `127.0.0.1` automatically.

---

### 🔐 Fixing SSL Trust Issues (mkcert)

If your browser (Chrome, Edge, etc.) shows "Your connection is not private" or a red warning even though LDM is running with SSL, follow these steps to fix the trust relationship:

#### 1. Initialize the Root CA (Mandatory)

The most common cause is that the `mkcert` Root CA has not been added to your system's trust store. Run this in your terminal:

```bash
mkcert -install
```

- **macOS**: You may be prompted for your password or Touch ID to modify the System Keychain.
- **WSL2**: You **must** run this inside your WSL terminal to allow `curl` and Liferay modules to verify internal HTTPS traffic.
- **Windows**: You will see a security prompt asking to install the "mkcert development CA." Click **Yes**.

#### 2. Fully Restart your Browser

Chrome and other Chromium-based browsers often cache certificate trust.

- Simply refreshing the page is often **not enough**.
- **Action**: **Completely Quit** (Cmd+Q on Mac) the browser and restart the application.

#### 3. Verify with `ldm doctor`

Run `ldm doctor` to verify that your system sees the Root CA as trusted:

```bash
ldm doctor
```

Look for: `mkcert ✅ Installed (Root CA Trusted)`

---

## 🚀 Running Commands

### Project Scope

Most LDM commands (like `run`, `stop`, `logs`) require a project context.

1. **Implicit**: If you are inside a project folder, LDM will detect it automatically.
2. **Explicit**: You can target a project from anywhere using `ldm run <project_name>`.

If LDM cannot find a project, it will provide a helpful error message instead of crashing.

### Version Verification

LDM binaries use **"Magic Byte" detection** to accurately report their checksum in `ldm doctor`. This ensures that even if you rename the file, LDM can still verify that you are running a valid, hardened production build.

---

## 🛡️ Supported & Tested Environments

We maintain "Tier 1" support for the following configurations to ensure LDM works exactly as expected for Sales Engineers.

| Environment Type | Technology | Purpose |
| :--- | :--- | :--- |
| **Corporate Standard** | WSL2 + Docker Desktop | Verify Windows file system (`C:\Users`) mapping. |
| **"Pure" Linux** | VirtualBox + Ubuntu | Verify `chown` and Linux permissions. |
| **"Locked" SE** | Hyper-V + Windows 11 | Verify LDM in "Nested" environments (Common for cloud VMs). |
| **Modern Mac** | macOS + OrbStack | High-performance Apple Silicon testing. |
