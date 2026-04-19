import json
import re
import sys
import requests
from collections import defaultdict
from datetime import datetime

API_BASE = "https://hub.docker.com/v2/repositories/liferay/dxp/tags?page_size=100&ordering=-last_updated"
GITHUB_API = "https://api.github.com/repos/peterrichards-lr/liferay-docker-manager/releases/tags/seeded-states"


def get_tags_with_filter(name_filter):
    tags = []
    url = f"{API_BASE}&name={name_filter}"
    try:
        # Fetch 2 pages to ensure we catch all patches for a quarter
        for _ in range(2):
            if not url:
                break
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json()
                for result in data.get("results", []):
                    tags.append(result["name"])
                url = data.get("next")
            else:
                break
    except Exception as e:
        print(f"Error fetching tags for {name_filter}: {e}", file=sys.stderr)
    return tags


def get_existing_seeds():
    """Fetches the list of existing seed assets from the 'seeded-states' release."""
    try:
        headers = {"User-Agent": "ldm-seed-builder"}
        import os

        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        response = requests.get(GITHUB_API, headers=headers, timeout=15)
        if response.status_code == 200:
            data = response.json()
            return [asset["name"] for asset in data.get("assets", [])]
        else:
            print(
                f"Warning: Could not fetch existing seeds (HTTP {response.status_code}).",
                file=sys.stderr,
            )
            return []
    except Exception as e:
        print(
            f"Warning: Could not fetch existing seeds from GitHub ({e}).",
            file=sys.stderr,
        )
        return []


def filter_tags(tags, existing_assets):
    # Standard QR: 2025.q1.5
    qr_pattern = re.compile(r"^(\d{4}\.q[1-4])\.(\d+)$")
    # Modern LTS: 2023.q4.15-lts
    lts_pattern = re.compile(r"^(\d{4}\.q[1-4])\.(\d+)-lts$")

    # Grouping buckets: { "2025.q1": [list of patches] }
    qr_patches = defaultdict(list)
    lts_patches = defaultdict(list)

    for tag in tags:
        # Skip variants and pre-releases
        if any(x in tag.lower() for x in ["snapshot", "nightly", "slim", "pre", "rc"]):
            continue

        # Match LTS first (higher precedence for Q1)
        lts_match = lts_pattern.match(tag)
        if lts_match:
            quarter, patch = lts_match.groups()
            lts_patches[quarter].append(tag)
            continue

        # Match standard QR
        qr_match = qr_pattern.match(tag)
        if qr_match:
            quarter, patch = qr_match.groups()
            qr_patches[quarter].append(tag)
            continue

    def natural_sort_key(s):
        return [
            int(text) if text.isdigit() else text.lower()
            for text in re.split("([0-9]+)", s)
        ]

    # Select latest for each Quarter
    latest_per_quarter = {}

    # 1. Process All LTS lines
    for quarter, versions in lts_patches.items():
        sorted_versions = sorted(versions, key=natural_sort_key)
        latest_per_quarter[quarter] = sorted_versions[-1]

    # 2. Process QR lines (only if no LTS exists for that quarter)
    for quarter, versions in qr_patches.items():
        if quarter not in latest_per_quarter:
            sorted_versions = sorted(versions, key=natural_sort_key)
            latest_per_quarter[quarter] = sorted_versions[-1]

    # Sort the identified quarters
    all_quarters = sorted(latest_per_quarter.keys(), reverse=True)
    lts_quarters = sorted(lts_patches.keys(), reverse=True)

    # Strategy:
    # 1. Last 4 Quarters (regardless of LTS/QR status)
    selected_quarters = set(all_quarters[:4])

    # 2. Last 2 LTS lines
    selected_quarters.update(lts_quarters[:2])

    # Convert back to full tags
    candidates = [
        latest_per_quarter[q] for q in sorted(list(selected_quarters), reverse=True)
    ]

    # Smart Filtering: Skip rebuild if ALL 3 DB seeds already exist for this tag
    result = []

    # Import SEED_VERSION from the repo source
    sys.path.append(".")
    try:
        from ldm_core.constants import SEED_VERSION
    except ImportError:
        SEED_VERSION = "1"

    for tag in candidates:
        db_types = ["postgresql", "mysql", "hypersonic"]
        search_mode = "shared" if tag >= "2025.q1" else "sidecar"

        needed = False
        for db in db_types:
            asset_name = f"seeded-{tag}-{db}-{search_mode}-v{SEED_VERSION}.tar.gz"
            if asset_name not in existing_assets:
                needed = True
                break

        if needed:
            result.append(tag)
        else:
            print(
                f"Skipping {tag}: All v{SEED_VERSION} seeds already exist in 'seeded-states' release.",
                file=sys.stderr,
            )

    return result


if __name__ == "__main__":
    current_year = datetime.now().year
    years = [str(y) for y in range(current_year, current_year - 3, -1)]

    tags = []
    # Search for year.q patterns
    for y in years:
        for q in ["q1", "q2", "q3", "q4"]:
            tags += get_tags_with_filter(f"{y}.{q}")

    existing = get_existing_seeds()
    selected = filter_tags(tags, existing)

    # Return JSON array for GitHub Action matrix
    print(json.dumps(selected))
