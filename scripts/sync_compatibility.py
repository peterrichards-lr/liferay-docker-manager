#!/usr/bin/env python3
import re
import sys
import hashlib
import shutil
from pathlib import Path
from datetime import datetime

# Add project root to sys.path to allow importing ldm_core and scripts
sys.path.append(str(Path(__file__).parent.parent))

from ldm_core.ui import UI


def strip_ansi(text):
    """Removes ANSI escape sequences from a string."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def anonymize_content(content):
    """Removes identifiable information from the report content."""
    # 1. Specific headers
    content = re.sub(r"Hostname:\s+[^\n]+", "Hostname:  [ANONYMIZED]", content)
    content = re.sub(r"Binary:\s+[^\n]+", "Binary:    [ANONYMIZED]", content)
    content = re.sub(r"Worker ID:\s+[^\n]+", "Worker ID: [ANONYMIZED]", content)
    content = re.sub(r"Azure Region:\s+[^\n]+", "Azure Region: [ANONYMIZED]", content)

    # 2. Absolute paths (macOS and Linux)
    content = re.sub(r"(/Users/[^/\s]+|/home/[^/\s]+)", "[HOME]", content)

    # 3. Path markers
    content = re.sub(r"✅\s+/[^\n]+", "✅  [PATH]", content)
    content = re.sub(r"⚠️\s+/[^\n]+", "⚠️  [PATH]", content)
    content = re.sub(r"❌\s+/[^\n]+", "❌  [PATH]", content)

    return content


def get_report_metadata(report_path):
    """Parses an LDM E2E verification report and returns metadata."""
    raw_content = report_path.read_text()
    content = strip_ansi(raw_content)

    # 1. Detect Verification Status
    passed = "🎯 ALL E2E VERIFICATIONS PASSED!" in content

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
    platform_match = re.search(r"Platform\s+✅\s+([^\n]+)", content)
    if not platform_match:
        platform_match = re.search(r"Platform:\s+([^\n]+)", content)

    platform_str = platform_match.group(1).strip() if platform_match else "Unknown"

    # 4. Extract Docker Provider
    provider_match = re.search(r"Docker Provider\s+✅\s+([^\n]+)", content)
    if not provider_match:
        provider_match = re.search(r"Docker Provider\s+([^\n]+)", content)

    provider = provider_match.group(1).strip() if provider_match else "Unknown"

    arch = "Unknown"
    host_os = "Unknown"
    p_low = platform_str.lower()

    # --- Standardize Environment ---
    is_mac = "mac" in p_low or "darwin" in p_low
    is_fedora = "fc" in p_low or "fedora" in p_low or "fedora" in content.lower()
    is_ubuntu = "ubuntu" in p_low or "ubuntu" in content.lower()

    is_wsl = (
        ("microsoft" in p_low or "wsl" in p_low or "wsl" in content.lower())
        and not is_fedora
        and not is_ubuntu
    )

    is_windows_native = (
        ("win32" in p_low or "windows" in p_low)
        and not is_wsl
        and not is_fedora
        and not is_ubuntu
    )

    is_linux_native = (
        (is_fedora or is_ubuntu or "linux" in p_low) and not is_wsl and not is_mac
    )

    # 4.1 Force Provider standardization
    if is_mac:
        if provider == "Unknown" or provider == "Docker Desktop":
            provider = "Colima"
            if "orbstack" in content.lower() or "orbstack" in p_low:
                provider = "OrbStack"
    elif is_wsl:
        if provider == "Unknown" or provider == "desktop-linux":
            provider = "Native WSL2"
    elif is_windows_native:
        if provider == "Unknown" or provider == "desktop-linux":
            provider = "Docker Desktop"

    # --- FALLBACK MAPPINGS (Timestamps) ---
    if timestamp_str == "Tue 28 Apr 12:25:38 BST 2026":
        platform_str = "linux-gnu (wsl)"
        provider = "Native WSL2"
        is_wsl = True
        is_windows_native = False
    elif (
        timestamp_str == "Tue 28 Apr 2026 12:48:13 BST"
        or timestamp_str == "Tue 28 Apr 2026 12:28:09 BST"
    ):
        platform_str = "darwin25 (arm64)"
        provider = "Colima"
    elif (
        timestamp_str == "Tue 28 Apr 2026 15:18:10 BST"
        or timestamp_str == "Tue 28 Apr 2026 15:18:49 BST"
    ):
        platform_str = "darwin25 (arm64)"
        provider = "OrbStack"
    elif timestamp_str == "Tue 28 Apr 10:07:43 BST 2026":
        platform_str = "linux (native)"
        provider = "Native Docker"
        is_linux_native = True

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
        if name:
            host_os = f"macOS {v_num} {name}"
        elif v_num > 0:
            host_os = f"macOS {v_num}"
        else:
            host_os = "macOS 11+"

        if "arm64" in p_low or "aarch64" in p_low:
            arch = "Apple Silicon"
        elif "x86_64" in p_low or "amd64" in p_low or "i386" in p_low:
            arch = "Apple Intel"
        else:
            if "arm64" in content.lower() or "darwin25" in p_low:
                arch = "Apple Silicon"
            elif "x86_64" in content.lower() or "darwin21" in p_low:
                arch = "Apple Intel"
    elif is_wsl or is_windows_native:
        host_os = "Windows 11"
        arch = "Windows PC"
    elif is_fedora:
        arch = "Linux Workstation"
        fedora_match = re.search(r"fc(\d+)", p_low)
        host_os = f"Fedora {fedora_match.group(1) if fedora_match else ''}".strip()
    elif is_ubuntu:
        arch = "Linux Workstation"
        ubuntu_match = re.search(r"(\d+\.\d+)", p_low)
        host_os = f"Ubuntu {ubuntu_match.group(1) if ubuntu_match else ''}".strip()
    elif is_linux_native:
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
        "passed": passed,
        "status_slug": status_slug,
        "internal_slug": internal_slug,
        "content": raw_content,
        "timestamp": dt,
        "report_path": report_path,
    }


def sync_reports():
    script_dir = Path(__file__).parent.parent
    results_dir = script_dir / "references" / "verification-results"
    history_dir = results_dir / "history"
    source_file = script_dir / "docs" / "COMPATIBILITY_TABLE.md"

    if not source_file.exists():
        UI.error(f"Source file not found: {source_file}")
        return

    history_dir.mkdir(exist_ok=True)

    # 1. Process and normalize all reports
    reports = list(results_dir.glob("*.txt"))
    all_metas = []
    for report in reports:
        if report.name == ".gitkeep":
            continue
        try:
            meta = get_report_metadata(report)
            if meta["arch"] == "Unknown":
                continue
            all_metas.append(meta)
        except Exception as e:
            UI.error(f"Failed to process {report.name}: {e}")

    # 2. Archival Logic
    latest_per_env = {}
    for meta in all_metas:
        key = (meta["arch"], meta["os"], meta["provider"])
        if (
            key not in latest_per_env
            or meta["timestamp"] > latest_per_env[key]["timestamp"]
        ):
            latest_per_env[key] = meta

    for meta in all_metas:
        key = (meta["arch"], meta["os"], meta["provider"])
        is_latest = latest_per_env[key]["report_path"] == meta["report_path"]

        expected_name = f"verify-{meta['internal_slug']}-{meta['status_slug']}"
        name_parts = meta["report_path"].stem.split("-")
        name_hash = (
            name_parts[-1]
            if (len(name_parts) >= 6 and meta["report_path"].name.startswith("verify-"))
            else hashlib.sha256(meta["report_path"].name.encode()).hexdigest()[:8]
        )
        new_name = f"{expected_name}-{name_hash}.txt"

        if is_latest:
            target_path = results_dir / new_name
            if meta["report_path"] != target_path:
                UI.info(f"Standardizing: {meta['report_path'].name} -> {new_name}")
                if not meta["report_path"].name.startswith("verify-"):
                    clean_content = anonymize_content(meta["content"])
                    target_path.write_text(clean_content)
                    meta["report_path"].unlink()
                else:
                    meta["report_path"].rename(target_path)
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

    table_header = (
        "| Architecture | Host OS | Docker Provider | Hardening | Verified | Report |"
    )
    table_sep = "| :--- | :--- | :--- | :--- | :--- | :--- |"
    rows = []
    for meta in sorted(table_metas, key=lambda x: (x["arch"], x["os"], x["provider"])):
        badge = get_badge(meta["provider"], meta["os"])
        icon = "✅" if meta["passed"] else "❌"
        report_link = f"[{meta['report_path'].name}](../references/verification-results/{meta['report_path'].name})"
        rows.append(
            f"| **{meta['arch']}** | {meta['os']} | **{meta['provider']}** | {badge} | {icon} | {report_link} |"
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
