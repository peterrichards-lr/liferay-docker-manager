# Install on Windows

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

Windows does not include all the optional developer tools by default.

> [!IMPORTANT]
> **Terminal Encoding:** Older Windows consoles (cmd.exe / PowerShell 5) may have trouble displaying Unicode symbols (●, ✅). LDM **v2.4.26-beta.37+** automatically detects these terminals and switches to safe ASCII fallbacks. For the best experience, we recommend using **Windows Terminal**.

## 1. Enable Telnet Client (Administrator PowerShell)

Open PowerShell **as an Administrator** to enable Telnet (required for OSGi Gogo Shell access):

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName TelnetClient
```

### 2. Install SSL Tools (Chocolatey or Scoop)

To enable "Green Lock" SSL on Windows, you must install `mkcert` and `openssl`. You can use either Chocolatey or Scoop.

**Option A: Chocolatey** (Requires Administrator PowerShell)

```powershell
choco install mkcert openssl
mkcert -install
```

**Option B: Scoop** (Run as a Standard User!)
_Note: Scoop refuses to run as Administrator. Run these in a standard, non-elevated PowerShell:_

```powershell
# 1. Install Scoop (The Package Manager)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser; iwr -useb get.scoop.sh | iex

# 2. Install Git (Required for Scoop Buckets and OpenSSL)
scoop install git

# 3. Add the Extras Bucket (Required for mkcert)
scoop bucket add extras

# 4. Install SSL Tools
scoop install mkcert openssl
```

_After Scoop finishes, open an **Administrator PowerShell** to initialize the trust store:_

```powershell
# 5. Initialize Local Trust Store (Requires Administrator)
mkcert -install
```

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-15* | *Last Reviewed: 2026-07-07*
