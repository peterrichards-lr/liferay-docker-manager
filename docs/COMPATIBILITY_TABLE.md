# Compatibility Table (Source)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Hardening | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 11+ | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ❌ | [verify-apple-intel-macos-11-colima-fail-7391a07d.txt](../references/verification-results/verify-apple-intel-macos-11-colima-fail-7391a07d.txt) |
| **Apple Intel** | macOS 11+ | **Unknown** | `Unknown` | ✅ | [verify-apple-intel-macos-11-unknown-pass-b8fa44ff.txt](../references/verification-results/verify-apple-intel-macos-11-unknown-pass-b8fa44ff.txt) |
| **Apple Silicon** | macOS 11+ | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ | [verify-apple-silicon-macos-11-colima-pass-e134c19d.txt](../references/verification-results/verify-apple-silicon-macos-11-colima-pass-e134c19d.txt) |
| **Linux Workstation** | Linux | **Native Docker** | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | ✅ | [verify-linux-workstation-linux-native-docker-pass-75c318fa.txt](../references/verification-results/verify-linux-workstation-linux-native-docker-pass-75c318fa.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass-d02fbff2.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass-d02fbff2.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |

<!-- COMPATIBILITY_END -->
