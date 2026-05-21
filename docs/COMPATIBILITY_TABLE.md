# Compatibility Table (Standalone Binaries)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 12 Monterey | **OrbStack** `v1.5.1` | `v25.0.5` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | `2.4.26-pre.7` | ❌ | [verify-apple-intel-macos-12-monterey-orbstack-fail-873ad35e.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-orbstack-fail-873ad35e.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | `2.7.2-beta.68` | ❌ | [verify-apple-silicon-macos-16-16-colima-pass-5ac15c95.txt](../references/verification-results/verify-apple-silicon-macos-16-16-colima-pass-5ac15c95.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | `2.7.2-beta.59` | ❌ | [verify-apple-silicon-macos-16-16-colima-fail-129db080.txt](../references/verification-results/verify-apple-silicon-macos-16-16-colima-fail-129db080.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | `2.7.2-beta.73` | ❌ | [verify-apple-silicon-macos-16-16-colima-pass-d3bf90bf.txt](../references/verification-results/verify-apple-silicon-macos-16-16-colima-pass-d3bf90bf.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **Colima** | `29.4.0` | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | `2.7.2-beta.73` | ❌ | [verify-apple-silicon-macos-16-16-orbstack-pass-e4bd620d.txt](../references/verification-results/verify-apple-silicon-macos-16-16-orbstack-pass-e4bd620d.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **Colima** `v0.10.1` | `29.2.1` | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | `2.7.2-beta.47` | ❌ | [verify-apple-silicon-macos-26-tahoe-colima-fail-e7b430c0.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-colima-fail-e7b430c0.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **OrbStack** `v2.1.1` | `29.4.0` | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | `2.4.26` | ✅ | [verify-apple-silicon-macos-26-tahoe-orbstack-pass-d298f8e3.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-orbstack-pass-d298f8e3.txt) |
| **Linux Workstation** | Fedora | **Unknown** | `29.5.0` | `Unknown` | `2.7.2-beta.73` | ❌ | [verify-linux-workstation-fedora-unknown-fail-de3bb56d.txt](../references/verification-results/verify-linux-workstation-fedora-unknown-fail-de3bb56d.txt) |
| **Linux Workstation** | Fedora 43 | **Native Docker** | `29.4.1` | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | `2.4.26` | ✅ | [verify-linux-workstation-fedora-43-native-docker-pass-5df929ec.txt](../references/verification-results/verify-linux-workstation-fedora-43-native-docker-pass-5df929ec.txt) |
| **Linux Workstation** | Ubuntu 24.04 | **Unknown** | `29.3.0` | `Unknown` | `2.7.2-beta.73` | ❌ | [verify-linux-workstation-ubuntu-24.04-unknown-fail-b26cea98.txt](../references/verification-results/verify-linux-workstation-ubuntu-24.04-unknown-fail-b26cea98.txt) |
| **Windows PC** | Windows 11 | **Docker Desktop** `v4.35.0` | `29.4.0` | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=windows) | `2.7.2-beta.73` | ❌ | [verify-windows-pc-windows-11-docker-desktop-fail-29bb61c9.txt](../references/verification-results/verify-windows-pc-windows-11-docker-desktop-fail-29bb61c9.txt) |
| **Windows PC** | Windows 11 | **Native WSL2** `WSL 2.4.4` | `29.3.0` | ![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows) | `2.7.2-beta.51` | ❌ | [verify-windows-pc-windows-11-native-wsl2-fail-fb08e0dc.txt](../references/verification-results/verify-windows-pc-windows-11-native-wsl2-fail-fb08e0dc.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |

<!-- COMPATIBILITY_END -->
