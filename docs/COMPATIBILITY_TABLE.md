# Compatibility Table (Standalone Binaries)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Hardening | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 12 Monterey | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ❌ | [verify-apple-intel-macos-12-monterey-colima-fail-1dfd4717.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-colima-fail-1dfd4717.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ | [verify-apple-silicon-macos-15-sequoia-colima-pass-597605e2.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-colima-pass-597605e2.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ | [verify-apple-silicon-macos-26-tahoe-colima-pass-895bed5e.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-colima-pass-895bed5e.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **OrbStack** | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | ✅ | [verify-apple-silicon-macos-26-tahoe-orbstack-pass-b36484d1.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-orbstack-pass-b36484d1.txt) |
| **Linux Workstation** | Fedora 43 | **Native Docker** | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | ✅ | [verify-linux-workstation-fedora-43-native-docker-pass-61f9b96d.txt](../references/verification-results/verify-linux-workstation-fedora-43-native-docker-pass-61f9b96d.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=windows) | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass-aa2707d1.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass-aa2707d1.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass-aa347d81.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-aa347d81.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |

<!-- COMPATIBILITY_END -->
