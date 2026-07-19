#!/usr/bin/env python3
import os
import re
import sys
from collections import defaultdict

import requests


def version_key(version_str):
    # e.g. 2026.q2.8 -> (2026, 'q2', 8)
    parts = version_str.split(".")
    year = int(parts[0]) if parts[0].isdigit() else 0
    q = parts[1] if len(parts) > 1 else ""
    patch = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    return (year, q, patch)


def get_quarter(version_str):
    # e.g. 2026.q2.8 -> 2026.q2
    parts = version_str.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return version_str


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is not set.")
        sys.exit(1)

    repo = "peterrichards-lr/liferay-docker-manager"
    tag = "seeded-states"
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ldm-prune-script",
    }

    print("Fetching release assets...")
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"Failed to fetch release: {response.status_code} {response.text}")
        sys.exit(1)

    data = response.json()
    assets = data.get("assets", [])

    # group by (db, search, quarter) -> list of assets
    # format: seeded-2026.q2.8-postgresql-shared-v2.tar.gz
    groups = defaultdict(list)

    for asset in assets:
        name = asset["name"]
        match = re.search(
            r"^seeded-(.*?)-((?:postgresql|mysql|hypersonic)-(?:shared|sidecar|remote))-",
            name,
        )
        if match:
            version = match.group(1)
            arch = match.group(2)
            quarter = get_quarter(version)
            groups[(quarter, arch)].append(
                {"id": asset["id"], "name": name, "version": version}
            )

    # For each group, keep only the latest version, delete others
    to_delete = []
    for (quarter, arch), items in groups.items():
        if len(items) <= 1:
            continue

        # Sort items by version
        items.sort(key=lambda x: version_key(x["version"]), reverse=True)
        latest = items[0]
        older = items[1:]

        print(f"Group {quarter} {arch}: Latest is {latest['name']}")
        for item in older:
            print(f"  -> Marking for deletion: {item['name']}")
            to_delete.append(item)

    if not to_delete:
        print("No obsolete seeds to prune.")
        sys.exit(0)

    for item in to_delete:
        del_url = f"https://api.github.com/repos/{repo}/releases/assets/{item['id']}"
        print(f"Deleting {item['name']}...")
        del_resp = requests.delete(del_url, headers=headers)
        if del_resp.status_code == 204:
            print(f"  Successfully deleted {item['name']}")
        else:
            print(
                f"  Failed to delete {item['name']}: {del_resp.status_code} {del_resp.text}"
            )


if __name__ == "__main__":
    main()
