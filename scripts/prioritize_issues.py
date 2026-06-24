#!/usr/bin/env python3
import json
import subprocess
import sys


def run_cmd(
    cmd: list[str], check: bool = True, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    """Helper to run a shell command."""
    res = subprocess.run(cmd, capture_output=capture, text=True)
    if check and res.returncode != 0:
        print(f"Error executing command: {' '.join(cmd)}")
        if capture:
            print(res.stderr)
        sys.exit(res.returncode)
    return res


def ensure_labels_exist() -> None:
    """Ensure the target priority labels exist in the repository."""
    labels = [
        ("priority: p1", "ff0000", "High priority feature request (10+ upvotes)"),
        ("priority: p2", "ffaa00", "Medium priority feature request (5+ upvotes)"),
        ("priority: p3", "888888", "Low priority feature request (<5 upvotes)"),
    ]
    for name, color, desc in labels:
        # Check if label exists first
        res = run_cmd(
            ["gh", "label", "list", "--json", "name"], capture=True, check=False
        )
        if res.returncode == 0:
            existing = [lbl["name"] for lbl in json.loads(res.stdout)]
            if name in existing:
                continue
        # Create label if not existing
        print(f"Creating label: {name}...")
        run_cmd(
            [
                "gh",
                "label",
                "create",
                name,
                "--color",
                color,
                "--description",
                desc,
            ],
            check=False,
        )


def main() -> None:
    ensure_labels_exist()

    # Get open feature request issues
    print("Fetching open issues with enhancement label...")
    res = run_cmd(
        [
            "gh",
            "issue",
            "list",
            "--label",
            "enhancement",
            "--json",
            "number,title,labels,reactionGroups",
            "--limit",
            "100",
        ],
        capture=True,
    )

    issues = json.loads(res.stdout)
    print(f"Found {len(issues)} open enhancement issues.")

    for issue in issues:
        number = issue["number"]
        title = issue["title"]
        labels = [lbl["name"] for lbl in issue.get("labels", [])]
        reaction_groups = issue.get("reactionGroups", [])

        # Find thumbs up count
        thumbs_up = 0
        for group in reaction_groups:
            if group.get("content") == "THUMBS_UP":
                thumbs_up = group.get("users", {}).get("totalCount", 0)
                break

        # Calculate priority
        if thumbs_up >= 10:
            target_priority = "priority: p1"
        elif thumbs_up >= 5:
            target_priority = "priority: p2"
        else:
            target_priority = "priority: p3"

        current_priority = None
        other_priorities = []
        for lbl in labels:
            if lbl.startswith("priority: "):
                if lbl == target_priority:
                    current_priority = lbl
                else:
                    other_priorities.append(lbl)

        # If priority is correct and no extra priorities exist, skip
        if current_priority == target_priority and not other_priorities:
            continue

        print(
            f"Updating Issue #{number} ({title}): thumbs_up={thumbs_up} -> target={target_priority}"
        )

        # Add target priority and remove other priorities
        cmd = ["gh", "issue", "edit", str(number), "--add-label", target_priority]
        for old in other_priorities:
            cmd.extend(["--remove-label", old])

        run_cmd(cmd)


if __name__ == "__main__":
    main()
