#!/usr/bin/env python3
import re
from pathlib import Path
from ldm_core.ui import UI


def parse_report(report_path):
    """Parses an LDM E2E verification report and returns environment metadata."""
    content = report_path.read_text()

    # 1. Detect Verification Status
    passed = "🎯 ALL E2E VERIFICATIONS PASSED!" in content

    # 2. Extract Platform/OS info from doctor output
    # Example: Platform ✅ macOS-26.4.1-arm64-arm-64bit-Mach-O
    platform_match = re.search(r"Platform\s+✅\s+([^\n]+)", content)
    platform_str = platform_match.group(1) if platform_match else "Unknown"

    # 3. Extract Docker Provider
    # Example: Docker Provider ✅ Colima
    provider_match = re.search(r"Docker Provider\s+✅\s+([^\n]+)", content)
    provider = provider_match.group(1) if provider_match else "Unknown"

    # Derive Architecture and Host OS
    arch = "Unknown"
    host_os = "Unknown"

    if "arm64" in platform_str.lower() or "aarch64" in platform_str.lower():
        arch = "Apple Silicon" if "mac" in platform_str.lower() else "ARM64"
    elif "x86_64" in platform_str.lower() or "amd64" in platform_str.lower():
        arch = "Apple Intel" if "mac" in platform_str.lower() else "Windows PC/Linux"

    if "mac" in platform_str.lower():
        host_os = "macOS 11+"
    elif "microsoft" in platform_str.lower() or "windows" in platform_str.lower():
        host_os = "Windows 11"
        arch = "Windows PC"

    return {
        "arch": arch,
        "os": host_os,
        "provider": provider,
        "verified": "✅" if passed else "❌",
    }


def update_compatibility_table():
    script_dir = Path(__file__).parent.parent
    results_dir = script_dir / "references" / "verification-results"
    source_file = script_dir / "docs" / "COMPATIBILITY_TABLE.md"

    if not source_file.exists():
        UI.error(f"Source file not found: {source_file}")
        return

    # 1. Parse all reports
    reports = list(results_dir.glob("*.txt"))
    verified_envs = []
    for report in reports:
        try:
            verified_envs.append(parse_report(report))
        except Exception as e:
            UI.warning(f"Failed to parse {report.name}: {e}")

    if not verified_envs:
        UI.info("No verification reports found to sync.")
        return

    # 2. Read master table
    content = source_file.read_text()

    # 3. Update existing rows or add new ones
    # We'll regenerate the table rows between markers
    table_header = "| Architecture | Host OS | Docker Provider | Hardening | Verified |"
    table_sep = "| :--- | :--- | :--- | :--- | :--- |"

    # Mapping for badges (Hardening column)
    badges = {
        "Colima": "![Colima](https://img.shields.io/badge/Colima-Hardened-FFAB00?style=flat-square&logo=apple)",
        "OrbStack": "![OrbStack](https://img.shields.io/badge/OrbStack-Hardened-00B0FF?style=flat-square&logo=apple)",
        "Docker Desktop": "![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardened-00C853?style=flat-square&logo=apple)",
        "Native WSL2": "![WSL2](https://img.shields.io/badge/WSL2-Hardened-blue?style=flat-square&logo=windows)",
    }

    # Extract current table to preserve manual entries if not in reports
    marker_regex = re.compile(
        r"<!-- COMPATIBILITY_START -->.*?<!-- COMPATIBILITY_END -->", re.DOTALL
    )
    current_table_match = marker_regex.search(content)

    rows = []
    if current_table_match:
        # Parse existing rows to keep them
        existing_lines = current_table_match.group(0).splitlines()
        for line in existing_lines:
            if (
                "|" in line
                and ":---" not in line
                and "Architecture" not in line
                and "<!--" not in line
            ):
                rows.append(line)

    # Update rows with data from new reports
    for env in verified_envs:
        badge = badges.get(env["provider"], env["provider"])
        new_row = f"| **{env['arch']}** | {env['os']} | **{env['provider']}** | {badge} | {env['verified']} |"

        # Replace if exists (based on Arch + OS + Provider)
        updated = False
        for i, row in enumerate(rows):
            if env["arch"] in row and env["os"] in row and env["provider"] in row:
                rows[i] = new_row
                updated = True
                break
        if not updated:
            rows.append(new_row)

    # 4. Reconstruct table
    new_table_block = (
        f"<!-- COMPATIBILITY_START -->\n{table_header}\n{table_sep}\n"
        + "\n".join(sorted(list(set(rows))))
        + "\n<!-- COMPATIBILITY_END -->"
    )

    new_content = marker_regex.sub(new_table_block, content)
    source_file.write_text(new_content)
    UI.success(f"Updated {source_file.name} with {len(verified_envs)} report(s).")

    # 5. Trigger documentation sync to other files
    from scripts.sync_docs import sync_table

    sync_table()


if __name__ == "__main__":
    update_compatibility_table()
