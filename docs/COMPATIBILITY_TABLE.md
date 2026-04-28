# Compatibility Table (Standalone Binaries)

<!-- COMPATIBILITY_START -->
| Architecture | Host OS | Docker Provider | Hardening | Verified | Report |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Intel** | macOS 12 Monterey | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ❌ | [verify-apple-intel-macos-12-monterey-colima-fail-b7010ae7.txt](../references/verification-results/verify-apple-intel-macos-12-monterey-colima-fail-b7010ae7.txt) |
| **Apple Silicon** | macOS 15 Sequoia | **Unknown** | `Unknown` | ✅ | [verify-apple-silicon-macos-15-sequoia-colima-pass-597605e2.txt](../references/verification-results/verify-apple-silicon-macos-15-sequoia-colima-pass-597605e2.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **Colima** | ![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple) | ✅ | [verify-apple-silicon-macos-26-tahoe-colima-pass-e1a2a2e1.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-colima-pass-e1a2a2e1.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **Docker Desktop** | ![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=apple) | ✅ | [verify-apple-silicon-macos-26-tahoe-docker-desktop-pass-bfc80857.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-docker-desktop-pass-bfc80857.txt) |
| **Apple Silicon** | macOS 26 Tahoe | **OrbStack** | ![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple) | ✅ | [verify-apple-silicon-macos-26-tahoe-orbstack-pass-b36484d1.txt](../references/verification-results/verify-apple-silicon-macos-26-tahoe-orbstack-pass-b36484d1.txt) |
| **Linux Workstation** | Linux | **Native Docker** | ![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo=linux) | ✅ | [verify-linux-workstation-linux-native-docker-pass-82bba80c.txt](../references/verification-results/verify-linux-workstation-linux-native-docker-pass-82bba80c.txt) |

## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |

<!-- COMPATIBILITY_END -->
