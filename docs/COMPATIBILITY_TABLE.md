# Compatibility Table (Standalone Binaries)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 12 Monterey | **OrbStack** `v1.5.1` | `v25.0.5` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | `2.4.26-pre.13` | ✅ | [verify-apple-intel-macos-12-monterey-orbstack-pass-b95ca9d0.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-orbstack-pass-b95ca9d0.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **Colima** `v0.10.1` | `29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | `2.4.26-pre.13` | ✅ | [verify-apple-silicon-macos-26-tahoe-colima-pass-68556025.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-colima-pass-68556025.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **OrbStack** `v2.1.1` | `29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | `2.4.26-pre.13` | ✅ | [verify-apple-silicon-macos-26-tahoe-orbstack-pass-5d5d411c.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-orbstack-pass-5d5d411c.txt) |
| **Linux Workstation** | Fedora 43 | **Native Docker** | `29.4.1` | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | `2.4.26-pre.13` | ✅ | [verify-linux-workstation-fedora-43-native-docker-pass-e671aaa3.txt](../references/verification-results/verify-linux-workstation-fedora-43-native-docker-pass-e671aaa3.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** `v4.35.0` | `v29.4.0` | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=windows) | `2.4.26-pre.13` | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass-4e6f327d.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass-4e6f327d.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** `WSL 2.4.4` | `29.3.0` | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | `2.4.26-pre.13` | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass-5b62ddab.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-5b62ddab.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |

<!-- COMPATIBILITY_END -->
