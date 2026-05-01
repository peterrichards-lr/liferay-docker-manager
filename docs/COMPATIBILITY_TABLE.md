# Compatibility Table (Standalone Binaries)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 12 Monterey | **Colima** | `v29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | `2.4.26-pre.3` | ❌ | [verify-apple-intel-macos-12-monterey-colima-fail-e12cd384.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-colima-fail-e12cd384.txt) |
| **Apple Intel** | macOS 12 Monterey | **OrbStack** | `v25.0.5` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | `2.4.26-pre.13` | ✅ | [verify-apple-intel-macos-12-monterey-orbstack-pass-b95ca9d0.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-orbstack-pass-b95ca9d0.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `Unknown` | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | `2.4.26-beta.43` | ❌ | [verify-apple-silicon-macos-15-sequoia-colima-fail-2e6f4f6f.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-colima-fail-2e6f4f6f.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **Colima** | `v29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | `2.4.26-pre.3` | ✅ | [verify-apple-silicon-macos-26-tahoe-colima-pass-7644e4c4.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-colima-pass-7644e4c4.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **OrbStack** | `v29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | `2.4.26-beta.43` | ✅ | [verify-apple-silicon-macos-26-tahoe-orbstack-pass-ce5a6995.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-orbstack-pass-ce5a6995.txt) |
| **Linux Workstation** | Fedora | **Native Docker** | `Unknown` | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | `2.4.26-beta.30` | ✅ | [verify-linux-workstation-fedora-native-docker-pass-5df197ea.txt](../references/verification-results/verify-linux-workstation-fedora-native-docker-pass-5df197ea.txt) |
| **Linux Workstation** | Fedora 43 | **Native Docker** | `v29.4.1` | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | `2.4.26-pre.3` | ✅ | [verify-linux-workstation-fedora-43-native-docker-pass-79809fa5.txt](../references/verification-results/verify-linux-workstation-fedora-43-native-docker-pass-79809fa5.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** | `v29.4.0` | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=windows) | `2.4.26-pre.3` | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass-3847cb83.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass-3847cb83.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** | `v29.3.0` | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | `2.4.26-pre.3` | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass-07a44029.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-07a44029.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |

<!-- COMPATIBILITY_END -->
