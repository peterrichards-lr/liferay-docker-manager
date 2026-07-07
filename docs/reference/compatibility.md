# Compatibility Table (Standalone Binaries)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.4.0` | ![Colima](https://img.shields.io/badge/Colima-Hardening-FFAB00?style=flat-square&logo=apple) | `2.11.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-colima-pass.txt](https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/references/verification-results/verify-apple-silicon-macos-15-sequoia-colima-pass.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **OrbStack** `v2.1.1` | `29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo=apple) | `2.11.2` | ✅ | [verify-apple-silicon-macos-15-sequoia-orbstack-pass.txt](https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/references/verification-results/verify-apple-silicon-macos-15-sequoia-orbstack-pass.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** `v4.35.0` | `29.4.0` | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardening-00C853?style=flat-square&logo=windows) | `2.11.2` | ✅ | [verify-windows-pc-windows-11-docker-desktop-pass.txt](https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/references/verification-results/verify-windows-pc-windows-11-docker-desktop-pass.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** `WSL 2.4.4` | `29.3.0` | ![WSL2](https://img.shields.io/badge/WSL2-Hardening-blue?style=flat-square&logo=windows) | `2.11.2` | ✅ | [verify-windows-pc-windows-11-native-wsl2-pass.txt](https://github.com/peterrichards-lr/liferay-docker-manager/blob/master/references/verification-results/verify-windows-pc-windows-11-native-wsl2-pass.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support. ES 8.17.x+ required for Liferay 2025.Q2+ (ES 7 deprecated). |

<!-- COMPATIBILITY_END -->

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-07* | *Last Reviewed: 2026-07-02*
