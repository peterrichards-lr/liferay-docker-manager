#!/usr/bin/env python3
import re
import sys
import hashlib
from pathlib import Path

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
    status_slug = "pass" if passed else "fail"

    # 2. Extract Platform/OS info
    platform_match = re.search(r"Platform\s+✅\s+([^\n]+)", content)
    platform_str = platform_match.group(1).strip() if platform_match else "Unknown"

    # 3. Extract Docker Provider
    provider_match = re.search(r"Docker Provider\s+✅\s+([^\n]+)", content)
    provider = provider_match.group(1).strip() if provider_match else "Unknown"

    # Standardize provider names for badging
    p_prov_low = provider.lower()
    if "colima" in p_prov_low:
        provider = "Colima"
    elif "orbstack" in p_prov_low:
        provider = "OrbStack"
    elif "docker desktop" in p_prov_low:
        provider = "Docker Desktop"
    elif "native wsl" in p_prov_low:
        provider = "Native WSL2"
    elif "native docker" in p_prov_low or "docker engine" in p_prov_low:
        provider = "Native Docker"

    # Derive Architecture and Host OS (Categorized)
    arch = "Unknown"
    host_os = "Unknown"
    p_low = platform_str.lower()

    if "mac" in p_low:
        host_os = "macOS 11+"
        arch = (
            "Apple Silicon"
            if ("arm64" in p_low or "aarch64" in p_low)
            else "Apple Intel"
        )
    elif "microsoft" in p_low or "windows" in p_low or provider == "Native WSL2":
        host_os = "Windows 11"
        arch = "Windows PC"
        if provider == "Unknown":
            provider = "Native WSL2"
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

    # Construct the internal slug (arch-os-provider)
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

    verified_envs = []

    for report in reports:
        if report.name == ".gitkeep":
            continue

        try:
            meta = get_report_metadata(report)
            if meta["arch"] == "Unknown":
                UI.warning(f"Skipping unknown environment in {report.name}")
                continue

            # Verify and potentially rename the report
            # Format: verify-{arch}-{os}-{provider}-{status}-{hash}.txt
            name_parts = report.stem.split("-")
            # If it already looks like a processed report, we verify the slug
            if report.name.startswith("verify-") and len(name_parts) >= 6:
                expected_slug = f"verify-{meta['internal_slug']}-{meta['status_slug']}"
                actual_slug = "-".join(name_parts[:-1])  # everything except hash

                if expected_slug != actual_slug:
                    UI.warning(
                        f"Filename mismatch! Expected slug '{expected_slug}' but found '{actual_slug}'. Renaming..."
                    )
                    name_hash = name_parts[-1]
                    new_name = f"{expected_slug}-{name_hash}.txt"
                    report = report.rename(report.parent / new_name)
            else:
                # Raw report from verification script (e.g. ldm-verify-hostname-date.txt)
                # or a rename is needed.
                name_hash = hashlib.sha256(report.name.encode()).hexdigest()[:8]
                new_name = f"verify-{meta['internal_slug']}-{meta['status_slug']}-{name_hash}.txt"
                UI.info(
                    f"Anonymizing and renaming raw report: {report.name} -> {new_name}"
                )

                # Anonymize before final write
                clean_content = anonymize_content(meta["content"])
                new_path = report.parent / new_name
                new_path.write_text(clean_content)
                if report.resolve() != new_path.resolve():
                    report.unlink()
                report = new_path

            meta["path"] = report
            verified_envs.append(meta)

        except Exception as e:
            UI.error(f"Failed to process {report.name}: {e}")

    # 2. Update COMPATIBILITY_TABLE.md
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

    marker_regex = re.compile(
        r"<!-- COMPATIBILITY_START -->.*?<!-- COMPATIBILITY_END -->", re.DOTALL
    )
    current_table_match = marker_regex.search(content)

    rows = []
    if current_table_match:
        for line in current_table_match.group(0).splitlines():
            if (
                "|" in line
                and ":---" not in line
                and "Architecture" not in line
                and "<!--" not in line
            ):
                if "Unknown" not in line:
                    rows.append(line)

    for env in verified_envs:
        badge = get_badge(env["provider"], env["os"])
        report_link = f"[{env['path'].name}](../references/verification-results/{env['path'].name})"
        verified_icon = "✅" if env["passed"] else "❌"
        new_row = f"| **{env['arch']}** | {env['os']} | **{env['provider']}** | {badge} | {verified_icon} | {report_link} |"

        updated = False
        for i, row in enumerate(rows):
            if f"**{env['arch']}**" in row and f"**{env['provider']}**" in row:
                rows[i] = new_row
                updated = True
                break
        if not updated:
            rows.append(new_row)

    table_header = (
        "| Architecture | Host OS | Docker Provider | Hardening | Verified | Report |"
    )
    table_sep = "| :--- | :--- | :--- | :--- | :--- | :--- |"
    new_table_block = (
        f"<!-- COMPATIBILITY_START -->\n{table_header}\n{table_sep}\n"
        + "\n".join(sorted(list(set(rows))))
        + "\n<!-- COMPATIBILITY_END -->"
    )

    new_content = marker_regex.sub(new_table_block, content)
    source_file.write_text(new_content)
    UI.success(f"Updated {source_file.name} with {len(verified_envs)} reports.")

    # 3. Final Sync to README/TESTING
    try:
        from scripts.sync_docs import sync_table

        sync_table()
    except Exception as e:
        UI.error(f"Sync docs failed: {e}")


if __name__ == "__main__":
    sync_reports()
