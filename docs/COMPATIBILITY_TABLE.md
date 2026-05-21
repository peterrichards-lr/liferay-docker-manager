# Compatibility Table (Standalone Binaries)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardening-FFAB00?style=flat-square&logo=apple) | `2.7.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-colima-pass-e1b5b25c.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-colima-pass-e1b5b25c.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **OrbStack** `v2.1.1` | `29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo=apple) | `2.7.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-orbstack-pass-6c4fb01e.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-orbstack-pass-6c4fb01e.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **Colima** `v0.10.1` | `29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardening-FFAB00?style=flat-square&logo=apple) | `2.7.2` | ✅ | [verify-apple-silicon-macos-26-tahoe-colima-pass-a2b991a2.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-colima-pass-a2b991a2.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **OrbStack** `v2.1.1` | `29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo=apple) | `2.7.2` | ✅ | [verify-apple-silicon-macos-26-tahoe-orbstack-pass-d298f8e3.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-orbstack-pass-d298f8e3.txt) |
| **Linux Workstation** | Fedora 43 | **Native Docker** | `29.4.1` | ![Linux](https://img.shields.io/badge/Linux-Hardening-success?style=flat-square&logo=linux) | `2.7.2` | ✅ | [verify-linux-workstation-fedora-43-native-docker-pass-4b080620.txt](../references/verification-results/verify-linux-workstation-fedora-43-native-docker-pass-4b080620.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** `v4.69.0` | `29.4.0` | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardening-00C853?style=flat-square&logo=windows) | `2.7.2` | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass-9d1ea613.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass-9d1ea613.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** `WSL 2.4.4` | `29.3.0` | ![WSL2](https://img.shields.io/badge/WSL2-Hardening-blue?style=flat-square&logo=windows) | `2.7.2` | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass-2a963c24.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-2a963c24.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |

<!-- COMPATIBILITY_END -->
