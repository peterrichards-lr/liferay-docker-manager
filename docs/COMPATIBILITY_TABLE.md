# Compatibility Table (Standalone Binaries)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Hardening | Verified | LDM Version | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 12 Monterey | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ❌ | `v2.4.26-beta.46` | [verify-apple-intel-macos-12-monterey-colima-fail-b7010ae7.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-colima-fail-b7010ae7.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ | `v2.4.26-beta.67` | [verify-apple-silicon-macos-26-tahoe-colima-pass-bfc80857.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-colima-pass-bfc80857.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **OrbStack** | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | ✅ | `v2.4.26-beta.43` | [verify-apple-silicon-macos-26-tahoe-orbstack-pass-b36484d1.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-orbstack-pass-b36484d1.txt) |
| **Linux Workstation** | Linux | **Native Docker** | ![Linux](https://img.shields.io/badge/Linux-Hardened-success?style=flat-square&logo=linux) | ✅ | `v2.4.26-beta.67` | [verify-linux-workstation-linux-native-docker-pass-82bba80c.txt](../references/verification-results/verify-linux-workstation-linux-native-docker-pass-82bba80c.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | ✅ | `v2.4.26-beta.31` | [verify-windows-pc-windows-11-native-wsl2-pass-d02fbff2.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-d02fbff2.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |

<!-- COMPATIBILITY_END -->
