# Installation Guide

LDM can be installed either as a standalone binary or manually via Python for development.

## 1. Standalone Binary (Recommended)

The standalone binary is a single-file executable that includes all dependencies.

### macOS / Linux / WSL2

Download the latest `ldm` directly using your terminal:

```bash

# For macOS (Apple Silicon)
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos-arm64 -o /usr/local/bin/ldm

# For macOS (Apple Intel)
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos-x86_64 -o /usr/local/bin/ldm

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
- **Docker Compose**: **v2 (Plugin)** is mandatory. Legacy v1 standalone is not supported.
- **Resources**: Recommended **4 CPUs and 8GB RAM** allocated to Docker.
  - *Note*: `ldm doctor` expects these minimums. If you allocate exactly 8GB, Docker may report ~7.7GB due to system overhead; the tool accounts for this by allowing a 7.5GB threshold.
- **Python**: 3.10+ (only if running from source)
- **SSL Tools**: `mkcert` and `openssl` are required for HTTPS support.

### 🍎 Install SSL Tools (macOS)

**Using [Homebrew](https://brew.sh/):**

```bash

# Install Docker CLI and SSL tools
brew install docker docker-compose mkcert nss openssl
mkcert -install
```

**Using [MacPorts](https://www.macports.org/):**

```bash

# Install Docker CLI and SSL tools
sudo port install docker docker-compose mkcert nss openssl
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

### Global Preferences (`config`)

LDM supports global user preferences stored in `~/.ldmrc`. You can manage these settings without editing files manually:

```bash

# View all current settings
ldm config

# Set a preference (e.g., enable verbose mode by default)
ldm config verbose true

# Remove a preference
ldm config verbose --remove
```

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

### Docker Resource Alignment (Windows/WSL2/macOS)

If `ldm doctor` reports insufficient memory or CPU cores:

1. **Align Docker Settings**:
    - **Docker Desktop**: Go to **Settings > Resources** and ensure the slider is at least **8GB** and **4 CPUs**.
    - **Colima**: Restart with higher limits: `colima stop && colima start --cpu 4 --memory 8`.

2. **The "Lenient" Threshold (LDM v1.6.36+)**:
    - LDM is now more lenient for older hardware.
    - **2-3 CPUs** or **4GB-7.5GB RAM** will now trigger a **Warning (⚠️)** instead of a hard failure.
    - This allows LDM to function on dual-core Intel Macs, though performance may be degraded.

## 🛠️ Troubleshooting: Version Loop & Integrity Issues

If `ldm doctor` reports **Executable Integrity: TAMPERED** or if you are stuck in a **"Version Loop"** (where you upgrade but the version doesn't change), follow these steps:

### 1. The "Intel Hash Mismatch" (macOS Intel)

Earlier versions of LDM (v1.6.32-v1.6.33) had an integrity check that was not fully architecture-aware. This often caused Intel Macs to flag official binaries as "TAMPERED" because their hash didn't match the Apple Silicon metadata.

**The Fix**: Upgrade to **v1.6.36+**, which introduces architecture-specific integrity verification.

### 2. Force a Repair

If your binary is corrupted or misbehaving, force a re-download of the official assets:

```bash

# Force a repair of the current version
ldm upgrade --repair
```

### 3. The "Manual Reset" (Last Resort)

If the tool is too broken to self-repair, manually overwrite the binary with the latest version:

```bash

# For macOS (Intel or Apple Silicon)
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos -o /usr/local/bin/ldm
sudo chmod +x /usr/local/bin/ldm
```

### 4. Version Shadowing

If you have cloned the repository and also have the binary installed, `ldm doctor` may report a "Shadowed" version.

- **Recommendation**: Use `./ldm` (local wrapper) for development and `ldm` (global binary) for daily use.

## 🛠️ Troubleshooting: Sudo & Root Issues

LDM strictly prohibits being run with `sudo` or as the `root` user (except for internal, just-in-time elevation).

### 1. "Security Risk: Do not run LDM with sudo"

If you see this error, it means LDM has detected that it is running with root privileges.

**The Cause**: Standalone LDM binaries use a cache directory in your home folder (`~/.shiv`). Running as root causes this cache to become owned by root, which prevents the tool from functioning correctly when run as a standard user later.

**The Fix**:

1. **Never use the sudo prefix**: Run `ldm <command>` directly.
2. **Fix Cache Ownership**: If you have already run with sudo and are now seeing "Permission Denied" errors, wipe the cache:

   ```bash
   sudo rm -rf ~/.shiv
   ```

3. **Fix Docker Permissions**: If you were using `sudo` because of Docker errors, add your user to the `docker` group instead:

   ```bash
   sudo usermod -aG docker $USER
   ```

   *Note: You must log out and back in for this to take effect.*

---

## 3. Shell Autocompletion

LDM supports full **TAB completion** for all commands and project names. This significantly improves productivity by allowing you to quickly cycle through projects when running `stop`, `logs`, or `run`.

### Step 1: Install argcomplete (Source Installs Only)

If you are using the Standalone Binary, `argcomplete` is already bundled. If you are using a Python Source installation (`pip install -e .`), ensure the library is installed:

```bash
pip install argcomplete
```

### Step 2: Enable Completion for your Shell

Run the following command to see the specific instruction for your active shell:

```bash
ldm completion
```

#### **For Zsh (macOS Default)**

Add this to your `~/.zshrc`:

```bash
eval "$(ldm completion zsh)"
```

#### **For Bash**

Add this to your `~/.bashrc`:

```bash
eval "$(ldm completion bash)"
```

#### **For Fish**

Add this to your `~/.config/fish/config.fish`:

```fish
ldm completion fish | source
```

### Step 3: Restart your Terminal

After adding the line to your profile, restart your terminal or source the file (e.g., `source ~/.zshrc`) for the changes to take effect.

---

## 🐳 Colima (Advanced macOS Setup)

Colima is a lightweight, open-source alternative to Docker Desktop. While highly performant on Apple Silicon, it is much stricter regarding file sharing and permissions.

### 0. Installation

**Homebrew**: `brew install colima`  
**MacPorts**: `sudo port install colima`

### 1. Recommended Start Command (Apple Silicon)

For the best compatibility with Liferay and SSCE build processes, we recommend using the macOS Virtualization Framework (`vz`) with **VirtioFS**.

```bash

# Optimized for M1/M2/M3 (16GB+ RAM)
colima start --cpu 4 --memory 8 --vm-type=vz --mount-type=virtiofs --mount /Users/$(whoami):w --mount /Volumes:w
```

### 2. Recommended Start Command (Legacy Intel Mac)

If you are running on an older Intel Mac (e.g. Early 2015 with 8GB RAM), use these settings to ensure Liferay has enough host memory to function:

```bash

# Optimized for Dual-Core Intel (8GB RAM)
# Note: uses sshfs as virtiofs requires macOS 14+ on Intel
colima start --cpus 2 --memory 6 --mount-type sshfs --mount /Users/$(whoami):w
```

### 3. The "Ghost Mount" Issue

If LDM reports `FATAL: VOLUME MOUNTING IS BROKEN`, it means Colima's VM can see the folder but cannot see the files inside it.

**The Fix**: Ensure your home directory is explicitly mounted with write permissions (`:w`). If your project is on an external volume, add it to the mount list using the command above.

### 3. Permissions

Unlike Docker Desktop, Colima does not "mask" file owners. LDM automatically handles this by running a **Permission Fixer** before every stack startup to ensure the `liferay` user (UID 1000) has access to your host files. **Note:** LDM is smart enough to skip the `.git` directory to preserve your repository metadata permissions.

### 4. Automatic Start (macOS Service)

For a seamless experience, you can set up Colima to start automatically in the background when you log in.

#### Step 1: Create the Startup Script

Save this script as `/usr/local/bin/colima-start-fg` and make it executable (`chmod +x`). It is architecture-aware and will automatically select the best VM and mount settings for your Mac.

```bash

#!/bin/bash

# 1. Path includes Homebrew (M1/M2) and MacPorts
export PATH="/opt/homebrew/bin:/opt/local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# 2. Robust Home Directory Logic
if [ -z "$HOME" ]; then
    export HOME="$(eval echo ~$USER)"
fi

function shutdown() {
  colima stop 
  exit 0
}

trap shutdown SIGTERM 
trap shutdown SIGINT 

# 3. Detect Architecture and Set Optimized Parameters
ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    # Apple Silicon (M1/M2/M3)
    # Optimized for vz/virtiofs
    START_CMD="colima start --cpu 4 --memory 8 --vm-type=vz --mount-type=virtiofs --mount $HOME:w --mount /Volumes:w"
else
    # Intel Mac
    # Optimized for sshfs (virtiofs requires macOS 14+ on Intel)
    START_CMD="colima start --cpu 2 --memory 6 --mount-type sshfs --mount $HOME:w"
fi

# 4. The Start/Monitor Loop
while true; do
  colima status &>/dev/null
  if [[ $? -eq 0 ]]; then
    break;
  fi

  # Execute the detected start command
  $START_CMD
  sleep 5
done

# 5. Keep alive for launchd
tail -f /dev/null &
wait $!
```

#### Step 2: Create the `launchd` Service

Create a file named `~/Library/LaunchAgents/com.github.abiosoft.colima.plist` with the following content:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.github.abiosoft.colima</string>
    <key>ProgramArguments</key>
    <array>
      <string>/usr/local/bin/colima-start-fg</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/colima.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/colima.err</string>
  </dict>
</plist>
```

#### Step 3: Load the Service

```bash

launchctl load ~/Library/LaunchAgents/com.github.abiosoft.colima.plist
```

---

### 🪟 Windows & 🐧 Linux Auto-Start

Colima is primarily a macOS tool. If you are on Windows or Linux, auto-start is handled differently:

- **Windows (Docker Desktop)**: Open Settings and check **"Start Docker Desktop when you log in"**.
- **Linux (Native/WSL2)**: Ensure the docker service is enabled via systemd:

  ```bash

  sudo systemctl enable --now docker
  ```

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

### 🐧 Docker Permissions (WSL & Linux)

If you see a "Permission Denied" error or LDM keeps asking for `sudo`, your user likely doesn't have permission to access the Docker socket. **Do not run LDM with sudo.** Instead, add your user to the `docker` group:

1. **Add the group**:

   ```bash

   sudo usermod -aG docker $USER
   ```

2. **Apply the changes**:
   - **WSL Users**: Completely restart your WSL terminal.
   - **Native Linux Users**: Log out and log back in.

3. **Verify**:
   Run `docker ps`. If it works without `sudo`, LDM will also work.

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

Run `ldm doctor` to verify that your system sees the Root CA as trusted and that all required tools (`telnet`, `nc`, `lcp`, `docker compose`) are correctly installed and reachable:

```bash

ldm doctor
```

Look for: `mkcert ✅ Installed (Root CA Trusted)` and ensure other tools show as `✅ Installed`.

---

## 🚀 Running Commands

### Project Scope

Most LDM commands (like `run`, `stop`, `logs`) require a project context.

1. **Implicit**: If you are inside a project folder, LDM will detect it automatically.
2. **Explicit**: You can target a project from anywhere using `ldm run <project_name>` or a direct path `ldm run ./my-project`.
3. **Workspace Support**: LDM automatically searches for projects in:
    - The current directory.
    - `~/ldm` (default).
    - `/Volumes/SanDisk/ldm` (for external storage users).
    - Any directory specified in the `LDM_WORKSPACE` environment variable.

**Pro-Tip**: Add `export LDM_WORKSPACE="/path/to/your/projects"` to your `.bashrc` or `.zshrc` to make your projects accessible from anywhere.

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
