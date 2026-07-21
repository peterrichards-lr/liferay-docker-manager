# Install on macOS

Download the latest `ldm` directly using your terminal. Copy and run the block specific to your environment:

## macOS (Apple Silicon)

```bash
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos-arm64 -o /usr/local/bin/ldm
sudo chmod +x /usr/local/bin/ldm
ldm --version
```

## macOS (Apple Intel)

```bash
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-macos-x86_64 -o /usr/local/bin/ldm
sudo chmod +x /usr/local/bin/ldm
ldm --version
```

## Linux / WSL2 (Native Linux)

```bash
sudo curl -L https://github.com/peterrichards-lr/liferay-docker-manager/releases/latest/download/ldm-linux -o /usr/local/bin/ldm
sudo chmod +x /usr/local/bin/ldm
ldm --version
```

> [!IMPORTANT]
> **macOS/Linux Runtime Requirement:** The standalone binaries for macOS and Linux require **Python 3.10 or higher** installed on the host machine. If running `ldm --version` fails with a traceback (e.g. `TypeError: unsupported operand type(s) for |`), please install or update Python (on macOS, you can run `brew install python@3.12`).

<!-- -->

> [!TIP]
>
> **WSL2 Users:** Use the `ldm-linux` binary within your WSL terminal. To enable SSL, you **must** install `mkcert` inside the Linux environment (`sudo apt update && sudo apt install libnss3-tools`).
>
> **Seamless WSL SSL (Green Lock):** To make your Windows browser (Edge/Chrome) trust LDM certificates generated inside WSL, you must share the Root CA:
>
> 1. In **PowerShell**, find your Windows CA path: `mkcert -CAROOT`
> 2. In **WSL**, point to that path by adding this to your `.bashrc` or `.zshrc`:
>    `export CAROOT="/mnt/c/Users/<your_user>/AppData/Local/mkcert"`
> 3. Run `mkcert -install` inside WSL. This links the Linux environment to the Windows-trusted authority.

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

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-21* | *Last Reviewed: 2026-07-07*
