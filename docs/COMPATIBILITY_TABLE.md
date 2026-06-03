# Compatibility Table (Standalone Binaries)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.4.0` | ![Colima](https://img.shields.io/badge/Colima-Hardening-FFAB00?style=flat-square&logo=apple) | `2.11.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-colima-pass-708f2f1e.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-colima-pass-708f2f1e.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **OrbStack** `v2.1.1` | `29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo=apple) | `2.11.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-orbstack-pass-6c4fb01e.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-orbstack-pass-6c4fb01e.txt) |
| **Linux Workstation** | Fedora 43 | **Native Docker** | `29.4.1` | ![Linux](https://img.shields.io/badge/Linux-Hardening-success?style=flat-square&logo=linux) | `2.11.2` | ✅ | [verify-linux-workstation-fedora-43-native-docker-pass-cfe4adaf.txt](../references/verification-results/verify-linux-workstation-fedora-43-native-docker-pass-cfe4adaf.txt) |
| **Linux Workstation** | Ubuntu 24.04 | **Native Docker** | `29.3.0` | ![Linux](https://img.shields.io/badge/Linux-Hardening-success?style=flat-square&logo=linux) | `2.11.2` | ✅ | [verify-linux-workstation-ubuntu-24.04-native-docker-pass-15740f3b.txt](../references/verification-results/verify-linux-workstation-ubuntu-24.04-native-docker-pass-15740f3b.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** `v4.35.0` | `29.4.0` | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardening-00C853?style=flat-square&logo=windows) | `2.11.2` | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass-43079b65.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass-43079b65.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** `WSL 2.4.4` | `29.3.0` | ![WSL2](https://img.shields.io/badge/WSL2-Hardening-blue?style=flat-square&logo=windows) | `2.11.2` | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass-10e8e0f2.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-10e8e0f2.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support. ES 8.17.x+ required for Liferay 2025.Q2+ (ES 7 deprecated). |

<!-- COMPATIBILITY_END -->
