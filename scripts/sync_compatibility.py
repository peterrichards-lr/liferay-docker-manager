import hashlib
import re
import shutil
from datetime import datetime
from pathlib import Path

from ldm_core.ui import UI


def strip_ansi(text):
    """Removes ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*[mK]", "", text)


def anonymize_content(content):
    """Redacts sensitive host-specific paths."""
    # Redact HOME paths
    home = str(Path.home())
    content = content.replace(home, "[HOME]")
    # Redact hostname if detected in report headers
    content = re.sub(r"Hostname:\s+[^\n]+", "Hostname:  [ANONYMIZED]", content)
    # Redact binary path
    return re.sub(r"Binary:\s+[^\n]+", "Binary:    [ANONYMIZED]", content)


def get_report_metadata(report_path):
    """Parses an LDM E2E verification report and returns metadata."""
    raw_content = report_path.read_text()
    content = strip_ansi(raw_content)

    # 1. Detect Verification Status
    passed = (
        "🎯 ALL E2E VERIFICATIONS PASSED!" in content
        or "ALL E2E VERIFICATIONS PASSED!" in content
    )

    # Robustness check for errors
    lines = content.splitlines()
    for line in lines:
        upper_line = line.upper()
        if ("ERROR:" in upper_line or "FATAL:" in upper_line) and "ℹ" not in line:
            passed = False
            break

    status_slug = "pass" if passed else "fail"

    # 2. Extract Timestamp
    ts_match = re.search(r"Timestamp:\s+([^\n]+)", content)
    timestamp_str = ts_match.group(1).strip() if ts_match else ""
    dt = None
    if timestamp_str:
        try:
            # Format: Tue 28 Apr 2026 12:48:13 BST or Tue 28 Apr 12:25:38 BST 2026
            ts_clean = re.sub(r"\s+[A-Z]{3,4}$", "", timestamp_str)
            for fmt in [
                "%a %d %b %Y %H:%M:%S",
                "%a %d %b %H:%M:%S %Y",
                "%m/%d/%Y %H:%M:%S",
            ]:
                try:
                    dt = datetime.strptime(ts_clean, fmt)
                    break
                except ValueError:
                    continue
            if not dt:
                dt = datetime.fromtimestamp(report_path.stat().st_mtime)
        except Exception:
            dt = datetime.fromtimestamp(report_path.stat().st_mtime)
    else:
        dt = datetime.fromtimestamp(report_path.stat().st_mtime)

    # 3. Extract Platform/OS info
    platform_match = re.search(r"Platform\s+(?:✅|\[OK\])\s+([^\n]+)", content)
    if not platform_match:
        platform_match = re.search(r"Platform:\s+([^\n]+)", content)

    platform_str = platform_match.group(1).strip() if platform_match else "Unknown"

    # 4. Extract Docker Provider
    provider_match = re.search(r"Docker Provider\s+(?:✅|\[OK\])\s+([^\n]+)", content)
    if not provider_match:
        provider_match = re.search(r"Docker Provider\s+([^\n]+)", content)

    provider = provider_match.group(1).strip() if provider_match else "Unknown"

    # 4. Extract LDM Version
    version = "Unknown"
    version_match = re.search(r"Version:\s+ldm\s+([^\n]+)", content)
    if not version_match:
        version_match = re.search(r"Version:\s+([^\n]+)", content)

    if version_match:
        cand = version_match.group(1).strip()
        if not cand.startswith("$("):  # Ignore malformed PS output
            version = cand

    if version == "Unknown" or version.startswith("$("):
        # Fallback: Extract from doctor output
        v_doctor_match = re.search(r"LDM Version\s+.*?v([0-9a-z.-]+)", content)
        if v_doctor_match:
            version = v_doctor_match.group(1).strip()

    # 5. Extract Docker Engine version
    engine_v = "Unknown"
    engine_match = re.search(r"Docker Engine\s+.*?v([0-9.]+)", content)
    if engine_match:
        engine_v = f"v{engine_match.group(1)}"

    # 6. Extract specific provider versions (OrbStack/Colima)
    provider_v = ""
    # OrbStack Version may appear in new reports
    ov_match = re.search(r"OrbStack Version\s+.*?v([0-9.]+)", content)
    if ov_match:
        provider_v = f"v{ov_match.group(1)}"
    else:
        # Colima Version may appear in new reports
        cv_match = re.search(r"Colima Version\s+.*?v([0-9.]+)", content)
        if cv_match:
            provider_v = f"v{cv_match.group(1)}"

    arch = "Unknown"
    host_os = "Unknown"
    p_low = platform_str.lower()

    # --- Standardize Environment ---
    is_mac = "mac" in p_low or "darwin" in p_low
    is_fedora = "fc" in p_low or "fedora" in p_low
    is_ubuntu = "ubuntu" in p_low

    # WSL: Platform MUST be Linux, and mention microsoft/wsl
    is_wsl = "linux" in p_low and ("microsoft" in p_low or "wsl" in p_low)

    # Windows Native: Platform contains Windows, and NOT linux
    is_windows_native = (
        "windows" in p_low or "win32" in p_low
    ) and "linux" not in p_low

    # 4.1 Force Provider standardization
    if is_mac:
        if provider in {"Unknown", "Docker Desktop"}:
            provider = "Colima"
            if "orbstack" in content.lower() or "orbstack" in p_low:
                provider = "OrbStack"
    elif is_wsl:
        if provider in {"Unknown", "desktop-linux"}:
            provider = "Native WSL2"
    elif is_windows_native and provider in {"Unknown", "desktop-linux"}:
        provider = "Docker Desktop"

    # --- FALLBACK MAPPINGS (Timestamps) ---
    if timestamp_str == "Tue 28 Apr 12:25:38 BST 2026":
        provider = "Native WSL2"
        is_wsl = True
    elif timestamp_str == "Tue 28 Apr 10:07:43 BST 2026":
        provider = "Native Docker"
        is_fedora = True

    if is_mac:
        v_num = 0
        macos_match = re.search(r"macos[-]?(\d+)", p_low)
        if macos_match:
            v_num = int(macos_match.group(1))
        else:
            darwin_match = re.search(r"darwin[-]?(\d+)", p_low)
            if darwin_match:
                darwin_v = int(darwin_match.group(1))
                if darwin_v >= 26:
                    v_num = 26
                elif darwin_v >= 24:
                    v_num = 15
                else:
                    v_num = darwin_v - 9

        real_names = {
            11: "Big Sur",
            12: "Monterey",
            13: "Ventura",
            14: "Sonoma",
            15: "Sequoia",
            26: "Tahoe",
        }
        name = real_names.get(v_num, "")
        host_os = (
            f"macOS {v_num} {name}"
            if name
            else (f"macOS {v_num}" if v_num > 0 else "macOS 11+")
        )

        if "arm64" in p_low or "aarch64" in p_low or "darwin25" in p_low:
            arch = "Apple Silicon"
        else:
            arch = "Apple Intel"
    elif is_wsl or is_windows_native:
        host_os = "Windows 11"
        arch = "Windows PC"
    elif is_fedora or "fedora" in content.lower():
        arch = "Linux Workstation"
        fedora_match = re.search(r"fc(\d+)", p_low)
        host_os = f"Fedora {fedora_match.group(1) if fedora_match else ''}".strip()
    elif is_ubuntu:
        arch = "Linux Workstation"
        ubuntu_match = re.search(r"(\d+\.\d+)", p_low)
        host_os = f"Ubuntu {ubuntu_match.group(1) if ubuntu_match else ''}".strip()
    else:
        arch = "Linux Workstation"
        host_os = "Linux"

    clean_arch = arch.lower().replace(" ", "-")
    clean_os = host_os.lower().replace(" ", "-").replace("+", "")
    clean_provider = provider.lower().replace(" ", "-")
    internal_slug = f"{clean_arch}-{clean_os}-{clean_provider}"

    return {
        "arch": arch,
        "os": host_os,
        "provider": provider,
        "engine_v": engine_v,
        "provider_v": provider_v,
        "version": version,
        "passed": passed,
        "status_slug": status_slug,
        "internal_slug": internal_slug,
        "content": raw_content,
        "timestamp": dt,
        "report_path": report_path,
    }


def sync_reports():
    """Main synchronization logic."""
    results_dir = Path("references/verification-results")
    history_dir = results_dir / "history"
    source_file = Path("docs/COMPATIBILITY_TABLE.md")

    if not results_dir.exists():
        UI.error(f"Directory not found: {results_dir}")
        return

    # 1. Gather and Parse all reports (including history)
    all_txt = list(results_dir.glob("*.txt")) + list(history_dir.glob("*.txt"))
    report_metas = []
    for r in all_txt:
        if r.name == ".gitkeep":
            continue
        try:
            report_metas.append(get_report_metadata(r))
        except Exception as e:
            UI.warning(f"Failed to parse {r.name}: {e}")

    # 2. Standardize Filenames & Archive Old Reports
    # We only keep the LATEST report for each environment (internal_slug) in the root
    latest_by_env = {}
    for meta in sorted(report_metas, key=lambda x: x["timestamp"]):
        latest_by_env[meta["internal_slug"]] = meta

    for meta in report_metas:
        is_latest = latest_by_env[meta["internal_slug"]] == meta
        expected_name = f"verify-{meta['internal_slug']}-{meta['status_slug']}"

        # Generate a unique hash for the filename to prevent collisions if timestamps are identical
        name_hash = hashlib.md5(
            f"{meta['internal_slug']}{meta['timestamp']}".encode()
        ).hexdigest()[:8]
        new_name = f"{expected_name}-{name_hash}.txt"

        if is_latest:
            target_path = results_dir / new_name
            UI.info(
                f"Standardizing & Anonymizing: {meta['report_path'].name} -> {new_name}"
            )
            clean_content = anonymize_content(meta["content"])

            # Remove the old file if it has a different name
            if meta["report_path"].exists() and meta["report_path"] != target_path:
                meta["report_path"].unlink()

            target_path.write_text(clean_content)
            meta["report_path"] = target_path
        else:
            UI.info(f"Archiving old report: {meta['report_path'].name}")
            shutil.move(
                str(meta["report_path"]), str(history_dir / meta["report_path"].name)
            )

    # 3. Table Generation Logic
    root_reports = list(results_dir.glob("*.txt"))
    final_metas = []
    for r in root_reports:
        if r.name == ".gitkeep":
            continue
        final_metas.append(get_report_metadata(r))

    table_metas = []
    for meta in final_metas:
        if meta["provider"] == "Unknown":
            has_better = any(
                m
                for m in final_metas
                if m["arch"] == meta["arch"]
                and m["os"] == meta["os"]
                and m["provider"] != "Unknown"
            )
            if has_better:
                continue
        table_metas.append(meta)

    # 4. Update COMPATIBILITY_TABLE.md
    def get_badge(provider, host_os):
        logo = (
            "apple"
            if "mac" in host_os.lower()
            else ("windows" if "windows" in host_os.lower() else "linux")
        )
        mapping = {
            "Colima": f"![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo={logo})",
            "OrbStack": f"![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo={logo})",
            "Docker Desktop": f"![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo={logo})",
            "Native WSL2": f"![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo={logo})",
            "Native Docker": f"![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo={logo})",
        }
        return mapping.get(provider, f"`{provider}`")

    table_header = "| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |"
    table_sep = "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    rows = []
    for meta in sorted(table_metas, key=lambda x: (x["arch"], x["os"], x["provider"])):
        badge = get_badge(meta["provider"], meta["os"])
        icon = "✅" if meta["passed"] else "❌"
        report_link = f"[{meta['report_path'].name}](../references/verification-results/{meta['report_path'].name})"

        provider_display = f"**{meta['provider']}**"
        if meta["provider_v"]:
            provider_display += f" `{meta['provider_v']}`"

        rows.append(
            f"| **{meta['arch']}** | {meta['os']} | {provider_display} | `{meta['engine_v']}` | {badge} | `{meta['version']}` | {icon} | {report_link} |"
        )

    new_table = f"{table_header}\n{table_sep}\n" + "\n".join(rows)

    content = source_file.read_text()
    content = content.replace(
        "# Compatibility Table (Source)", "# Compatibility Table (Standalone Binaries)"
    )
    marker_regex = re.compile(
        r"<!-- COMPATIBILITY_START -->.*?<!-- COMPATIBILITY_END -->", re.DOTALL
    )

    infra_block = """
## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |
"""
    new_block = f"<!-- COMPATIBILITY_START -->\n{new_table}\n{infra_block}\n<!-- COMPATIBILITY_END -->"
    source_file.write_text(marker_regex.sub(new_block, content))
    UI.success(
        f"Updated COMPATIBILITY_TABLE.md. Unique environments in table: {len(table_metas)}"
    )

    try:
        from scripts.sync_docs import sync_table

        sync_table()
    except Exception as e:
        UI.error(f"Sync docs failed: {e}")


if __name__ == "__main__":
    sync_reports()
