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
# Note: We use openssl-mingw instead of the default openssl to bypass a broken 
# global Scoop dependency (innounp 404 error) that affects the standard installer.
scoop install mkcert openssl-mingw
```

_After Scoop finishes, open an **Administrator PowerShell** to initialize the trust store:_

```powershell
# 5. Initialize Local Trust Store (Requires Administrator)
mkcert -install
```

## Windows reserved port ranges

Windows reserves blocks of TCP ports, usually for Hyper-V or WSL via WinNAT.
A bind inside one fails with a message that reads like a permissions problem
and is not:

```text
ports are not available: exposing port TCP 0.0.0.0:3001 -> 127.0.0.1:0:
listen tcp4 0.0.0.0:3001: bind: An attempt was made to access a socket in a
way forbidden by its access permissions.
```

Nothing is listening on a reserved port, so every "what is using this port"
check comes back empty. List the ranges with:

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

**LDM allocates around these automatically.** Ports it chooses itself -- the
host ports of client-extension services, and the proxy ports -- skip reserved
ranges, and a stored port that has since fallen inside one is replaced rather
than reused. The ranges move when the host or WinNAT restarts, so this is
re-checked on each run rather than decided once.

A port you pin _explicitly_ is still yours to choose, so `--port` or
`--ssl-port` pointing into a reserved range will still fail. Pick another, or
free the range: restarting Docker Desktop usually does it and is the safe
option. Restarting WinNAT (`net stop winnat` then `net start winnat`, from an
elevated prompt) also works but disrupts WSL2 and the Docker Desktop WSL2
backend -- run `wsl --shutdown` afterwards to restore them.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-10-09* | *Last Reviewed: 2026-10-09*
