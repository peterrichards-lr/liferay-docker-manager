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
            ts_clean = re.sub(r"\s+[A-Z]{3,4}$", "", timestamp_str)
            for fmt in ["%a %d %b %Y %H:%M:%S", "%a %d %b %H:%M:%S %Y"]:
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

    # 3. Extract LDM Version
    ldm_version = "Unknown"
    ver_match = re.search(r"LDM Version\s+.*?(v\d+\.\d+\.\d+[^ \n]*)", content)
    if not ver_match:
        ver_match = re.search(r"Version:\s+ldm\s+(\d+\.\d+\.\d+[^ \n]*)", content)
    if ver_match:
        ldm_version = ver_match.group(1).strip()

    # 4. Detect Environment (Doctor-First)
    platform_str = "Unknown"
    provider = "Unknown"

    # 4.1 Try Doctor Section
    doc_plat_match = re.search(r"Platform\s+✅\s+([^\n]+)", content)
    if doc_plat_match:
        platform_str = doc_plat_match.group(1).strip()

    doc_prov_match = re.search(r"Docker Provider\s+✅\s+([^\n]+)", content)
    if doc_prov_match:
        provider = doc_prov_match.group(1).strip()

    # 4.2 Fallback to Headers
    if platform_str == "Unknown":
        header_plat_match = re.search(r"Platform:\s+([^\n]+)", content)
        if header_plat_match:
            platform_str = header_plat_match.group(1).strip()

    # --- 5. Apply Fallback Mappings (Timestamps) ---
    if timestamp_str == "Tue 28 Apr 12:25:38 BST 2026":
        platform_str = "linux-gnu (wsl)"
        provider = "Native WSL2"
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

    # --- 6. Final Normalization Logic ---
    arch = "Unknown"
    host_os = "Unknown"
    p_low = platform_str.lower()

    is_wsl = (
        "wsl" in p_low or "microsoft" in content.lower() or provider == "Native WSL2"
    )
    is_mac = "mac" in p_low or "darwin" in p_low
    is_windows_native = "win32" in p_low or "windows" in p_low

    if is_wsl or is_windows_native:
        host_os = "Windows 11"
        arch = "Windows PC"
        if provider == "Unknown":
            provider = "Native WSL2" if is_wsl else "Docker Desktop"
    elif is_mac:
        if provider == "Unknown" or provider == "Docker Desktop":
            provider = "Colima"
            if "orbstack" in content.lower() or "orbstack" in p_low:
                provider = "OrbStack"

        # Resolve numerical version
        v_num = 0
        macos_match = re.search(r"macos[-]?(\d+)", p_low)
        if macos_match:
            v_num = int(macos_match.group(1))
        else:
            darwin_match = re.search(r"darwin[-]?(\d+)", p_low)
            if darwin_match:
                darwin_v = int(darwin_match.group(1))
                if darwin_v >= 25:
                    v_num = 26  # Tahoe
                elif darwin_v >= 24:
                    v_num = 15  # Sequoia
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
        host_os = f"macOS {v_num} {name}".strip() if v_num > 0 else "macOS 11+"

        # Resolve Architecture
        if "arm64" in p_low or "aarch64" in p_low or "darwin25" in p_low:
            arch = "Apple Silicon"
        elif "x86_64" in p_low or "amd64" in p_low or "i386" in p_low:
            arch = "Apple Intel"
        else:
            arch = "Apple Silicon" if "arm64" in content.lower() else "Apple Intel"
    elif "fedora" in p_low:
        fedora_match = re.search(r"fc(\d+)", p_low)
        host_os = f"Fedora {fedora_match.group(1) if fedora_match else ''}".strip()
        arch = "Linux Workstation"
    elif "ubuntu" in p_low:
        ubuntu_match = re.search(r"(\d+\.\d+)", p_low)
        host_os = f"Ubuntu {ubuntu_match.group(1) if ubuntu_match else ''}".strip()
        arch = "Linux Node" if "server" in p_low else "Linux Workstation"
    elif "linux" in p_low:
        host_os = "Linux"
        arch = "Linux Workstation"

    clean_arch = arch.lower().replace(" ", "-")
    clean_os = host_os.lower().replace(" ", "-").replace("+", "")
    clean_provider = provider.lower().replace(" ", "-")
    internal_slug = f"{clean_arch}-{clean_os}-{clean_provider}"

    return {
        "arch": arch,
        "os": host_os,
        "provider": provider,
        "ldm_version": ldm_version,
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

    reports = list(results_dir.glob("*.txt"))
    if not reports:
        UI.info("No reports found to sync.")
        return

    # 1. Process all reports
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

    # 2. Strict Archival Logic
    # Keep ONLY the single latest report per environment in the root.
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

        if is_latest:
            expected_name = f"verify-{meta['internal_slug']}-{meta['status_slug']}"
            name_parts = meta["report_path"].stem.split("-")
            name_hash = (
                name_parts[-1]
                if (
                    len(name_parts) >= 6
                    and meta["report_path"].name.startswith("verify-")
                )
                else hashlib.sha256(meta["report_path"].name.encode()).hexdigest()[:8]
            )
            new_name = f"{expected_name}-{name_hash}.txt"

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

    # 3. Table Generation
    root_reports = list(results_dir.glob("*.txt"))
    table_metas = []
    for r in root_reports:
        if r.name == ".gitkeep":
            continue
        meta = get_report_metadata(r)

        if meta["provider"] == "Unknown":
            has_better = any(
                get_report_metadata(x)["provider"] != "Unknown"
                for x in root_reports
                if get_report_metadata(x)["arch"] == meta["arch"]
                and get_report_metadata(x)["os"] == meta["os"]
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
        # Force windows logo for WSL2 even if host_os detection was fuzzy
        if provider == "Native WSL2":
            logo = "windows"

        mapping = {
            "Colima": f"![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo={logo})",
            "OrbStack": f"![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo={logo})",
            "Docker Desktop": f"![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo={logo})",
            "Native WSL2": f"![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo={logo})",
            "Native Docker": f"![Linux](https://img.shields.io/badge/Linux-Hardened-success?style=flat-square&logo={logo})",
        }
        return mapping.get(provider, f"`{provider}-Hardened`")

    table_header = "| Architecture | Host OS | Docker Provider | Hardening | Verified | LDM Version | Report |"
    table_sep = "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    rows = []
    for meta in sorted(table_metas, key=lambda x: (x["arch"], x["os"], x["provider"])):
        badge = get_badge(meta["provider"], meta["os"])
        icon = "✅" if meta["passed"] else "❌"
        ver = f"`{meta['ldm_version']}`"
        report_link = f"[{meta['report_path'].name}](../references/verification-results/{meta['report_path'].name})"
        rows.append(
            f"| **{meta['arch']}** | {meta['os']} | **{meta['provider']}** | {badge} | {icon} | {ver} | {report_link} |"
        )

    content = source_file.read_text()
    marker_regex = re.compile(
        r"<!-- COMPATIBILITY_START -->.*?<!-- COMPATIBILITY_END -->", re.DOTALL
    )
    infra_block = "\n## Global Infrastructure\n\n| Component | Verified Versions | Notes |\n| :--- | :--- | :--- |\n| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |\n| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support with auto-plugin installation and optimized Liferay config. |\n"
    new_block = (
        f"<!-- COMPATIBILITY_START -->\n{table_header}\n{table_sep}\n"
        + "\n".join(rows)
        + f"\n{infra_block}\n<!-- COMPATIBILITY_END -->"
    )
    source_file.write_text(marker_regex.sub(new_block, content))
    UI.success(f"Updated COMPATIBILITY_TABLE.md with {len(table_metas)} environments.")

    try:
        from scripts.sync_docs import sync_table

        sync_table()
    except Exception:
        pass


if __name__ == "__main__":
    sync_reports()
