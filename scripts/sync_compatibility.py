#!/usr/bin/env python3
import re
import sys
import hashlib
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

    # 3. Extract Platform/OS info
    platform_match = re.search(r"Platform\s+✅\s+([^\n]+)", content)
    if not platform_match:
        platform_match = re.search(r"Platform:\s+([^\n]+)", content)

    platform_str = platform_match.group(1).strip() if platform_match else "Unknown"

    # 4. Extract Docker Provider
    provider_match = re.search(r"Docker Provider\s+✅\s+([^\n]+)", content)
    provider = provider_match.group(1).strip() if provider_match else "Unknown"

    # --- FALLBACK MAPPINGS (for historical report restoration) ---
    if timestamp_str == "Tue 28 Apr 12:25:38 BST 2026":
        platform_str = "linux-gnu (wsl)"
        provider = "Native WSL2"
    elif timestamp_str == "Tue 28 Apr 2026 12:48:13 BST":
        platform_str = "darwin25 (arm64)"
        provider = "Colima"
    elif timestamp_str == "Tue 28 Apr 10:07:43 BST 2026":
        platform_str = "linux (native)"
        provider = "Native Docker"

    # Standardize provider names
    p_prov_low = provider.lower()
    if "colima" in p_prov_low:
        provider = "Colima"
    elif "orbstack" in p_prov_low:
        provider = "OrbStack"
    elif "docker desktop" in p_prov_low:
        provider = "Docker Desktop"
    elif (
        "native wsl" in p_prov_low
        or "wsl" in content.lower()
        or "wsl" in platform_str.lower()
    ):
        provider = "Native WSL2"
    elif "native docker" in p_prov_low or "docker engine" in p_prov_low:
        provider = "Native Docker"

    arch = "Unknown"
    host_os = "Unknown"
    p_low = platform_str.lower()

    # WSL and Docker Desktop on Windows are different
    is_wsl = "microsoft" in p_low or "wsl" in p_low or "wsl" in content.lower()
    is_windows_native = "win32" in p_low or "windows" in p_low

    if "mac" in p_low or "darwin" in p_low:
        host_os = "macOS 11+"
        arch = (
            "Apple Silicon"
            if ("arm64" in p_low or "aarch64" in p_low)
            else "Apple Intel"
        )
    elif is_windows_native or is_wsl:
        host_os = "Windows 11"
        arch = "Windows PC"
        if provider == "Unknown":
            provider = "Native WSL2" if is_wsl else "Docker Desktop"
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
        "passed": passed,
        "status_slug": status_slug,
        "internal_slug": internal_slug,
        "content": raw_content,
        "timestamp": dt,
    }


def sync_reports():
    script_dir = Path(__file__).parent.parent
    results_dir = script_dir / "references" / "verification-results"
    source_file = script_dir / "docs" / "COMPATIBILITY_TABLE.md"

    if not source_file.exists():
        UI.error(f"Source file not found: {source_file}")
        return

    reports = list(results_dir.glob("*.txt"))
    if not reports:
        UI.info("No reports found to sync.")
        return

    # 1. Process and rename all reports
    all_metas = []
    for report in reports:
        if report.name == ".gitkeep":
            continue

        try:
            meta = get_report_metadata(report)
            if meta["arch"] == "Unknown":
                UI.warning(f"Skipping unknown environment in {report.name}")
                continue

            # Rename if necessary to match the identified slug
            expected_slug = f"verify-{meta['internal_slug']}-{meta['status_slug']}"
            name_parts = report.stem.split("-")
            name_hash = (
                name_parts[-1]
                if (len(name_parts) >= 6 and report.name.startswith("verify-"))
                else hashlib.sha256(report.name.encode()).hexdigest()[:8]
            )

            new_name = f"{expected_slug}-{name_hash}.txt"
            new_path = report.parent / new_name

            if report.name != new_name:
                UI.info(f"Renaming/Updating: {report.name} -> {new_name}")
                if not report.name.startswith("verify-"):
                    # Anonymize raw reports
                    clean_content = anonymize_content(meta["content"])
                    new_path.write_text(clean_content)
                    if report.exists():
                        report.unlink()
                else:
                    # Just rename processed reports if slug changed
                    report.rename(new_path)
                report = new_path

            meta["path"] = report
            all_metas.append(meta)

        except Exception as e:
            UI.error(f"Failed to process {report.name}: {e}")

    # 2. Select the LATEST report for each (arch, os, provider)
    latest_per_env = {}
    for meta in all_metas:
        key = (meta["arch"], meta["os"], meta["provider"])
        if (
            key not in latest_per_env
            or meta["timestamp"] > latest_per_env[key]["timestamp"]
        ):
            latest_per_env[key] = meta

    # 3. Update COMPATIBILITY_TABLE.md
    content = source_file.read_text()

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
            "Docker Engine": f"![Linux](https://img.shields.io/badge/Linux-Native-success?style=flat-square&logo={logo})",
        }
        return mapping.get(provider, f"`{provider}`")

    # Generate the new table content
    table_header = (
        "| Architecture | Host OS | Docker Provider | Hardening | Verified | Report |"
    )
    table_sep = "| :--- | :--- | :--- | :--- | :--- | :--- |"

    rows = []
    for key in sorted(latest_per_env.keys()):
        meta = latest_per_env[key]
        badge = get_badge(meta["provider"], meta["os"])
        verified_icon = "✅" if meta["passed"] else "❌"
        report_link = f"[{meta['path'].name}](../references/verification-results/{meta['path'].name})"
        rows.append(
            f"| **{meta['arch']}** | {meta['os']} | **{meta['provider']}** | {badge} | {verified_icon} | {report_link} |"
        )

    new_table = f"{table_header}\n{table_sep}\n" + "\n".join(rows)

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

    new_content = marker_regex.sub(new_block, content)
    source_file.write_text(new_content)
    UI.success(f"Updated {source_file.name} with {len(latest_per_env)} latest reports.")

    # Final Sync to README/TESTING
    try:
        from scripts.sync_docs import sync_table

        sync_table()
    except Exception as e:
        UI.error(f"Sync docs failed: {e}")


if __name__ == "__main__":
    sync_reports()
