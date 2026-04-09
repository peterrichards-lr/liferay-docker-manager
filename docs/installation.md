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
> **WSL2 Users:** Use the `ldm-linux` binary within your WSL terminal. To enable SSL, you **must** install `mkcert` inside the Linux environment (`sudo apt update && sudo apt install libnss3-tools`).
>
> **Seamless WSL SSL (Green Lock):** To make your Windows browser (Edge/Chrome) trust LDM certificates generated inside WSL, you must share the Root CA:
>
> 1. In **PowerShell**, find your Windows CA path: `mkcert -CAROOT`
> 2. In **WSL**, point to that path by adding this to your `.bashrc` or `.zshrc`:
>    `export CAROOT="/mnt/c/Users/<your_user>/AppData/Local/mkcert"`
> 3. Run `mkcert -install` inside WSL. This links the Linux environment to the Windows-trusted authority.

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

- **Docker Engine**: Required for container orchestration.
  - [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac)
  - [Colima](https://github.com/abiosoft/colima) or [OrbStack](https://orbstack.dev/) (Mac alternatives)
  - [Native Docker Engine](https://docs.docker.com/engine/install/) (Linux)
- **Resources**: Recommended **4 CPUs and 8GB RAM** allocated to Docker.
  - *Note*: `ldm doctor` expects these minimums. If you allocate exactly 8GB, Docker may report ~7.7GB due to system overhead; the tool accounts for this by allowing a 7.5GB threshold.
- **Python**: 3.10+ (only if running from source)
- **SSL Tools**: `mkcert` and `openssl` are required for HTTPS support.

### 🍎 Install SSL Tools (macOS)

Using [Homebrew](https://brew.sh/):

```bash
brew install mkcert nss openssl
mkcert -install
```

### 🐧 Install SSL Tools (Native Linux)

...

```bash
sudo apt update && sudo apt install mkcert libnss3-tools openssl
mkcert -install
```

### 🐳 Linux & WSL Docker Permissions

If `ldm doctor` reports **Docker Engine: Not reachable** or you see **Permission Denied** errors on Linux/WSL, your user needs permission to talk to the Docker socket.

Run these commands to grant access:

```bash
# 1. Add your user to the docker group
sudo usermod -aG docker $USER

# 2. Create a path bridge (if /var/run/docker.sock is missing)
sudo ln -s /run/docker.sock /var/run/docker.sock

# 3. Apply changes (or restart your terminal)
newgrp docker
```

---

## 🛠️ Environment Setup & Troubleshooting

### Global Configuration (The `common/` Folder)

LDM allows you to synchronize files (e.g., OSGi configs, licenses, LPKG modules) to **every** project stack automatically.

#### 1. Baseline Assets (`init-common`)

LDM bundles a "Gold Standard" development baseline internally. To initialize or recreate this baseline in your global `common/` folder, run:

```bash
ldm init-common
```

This will create:

- **`portal-ext.properties`**: Developer-mode overrides and modern image support.
- **`env-blacklist.txt`**: Standard exclusions for environment variables (OAuth2 secrets, etc.).
- **`com.liferay...config`**: OSGi configuration to prevent session timeouts during local demos.

#### 2. Mandatory Infrastructure Settings

LDM is designed to be **self-healing**. Even if you do not use a `common/` folder, the tool will automatically ensure that every project's `portal-ext.properties` contains the essential settings for SSL and virtual hosting (`web.server.host`, `web.server.protocol`, etc.).

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

We maintain "Tier 1" support for the following physical lab configurations:

| Environment Type | Technology | Lab Hardware |
| :--- | :--- | :--- |
| **Standard Mac** | macOS + OrbStack / DD | Apple M1 Pro (32GB) |
| **Linux Workstation** | Fedora 43 (Native) | MacBook Pro 11,3 (Intel i7) |
| **Corporate Windows** | Windows 11 + WSL2 | Intel i7-7800X (16GB) |
| **Legacy/Intel Mac** | macOS + Colima | Apple Intel Core i7 (16GB) |
