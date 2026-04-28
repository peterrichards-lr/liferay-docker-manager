#!/usr/bin/env python3
import subprocess
import os
import re

# Base commit before beta cycle
BASE_COMMIT = "ec94f46^"
BRANCH_NAME = "history-rebuild"


def run(cmd, check=True):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def get_commits():
    # Get all commits since BASE_COMMIT in chronological order
    res = run(f"git log {BASE_COMMIT}..master --oneline --reverse")
    commits = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        h, s = line.split(" ", 1)
        commits.append({"hash": h, "subject": s})
    return commits


def clean_subject(subject):
    # 1. Remove version suffixes like [v2.4.26-beta.XX], (v2.4.26-beta.XX), v2.4.26-beta.XX
    subject = re.sub(
        r"\s*[\[\(]?v\d+\.\d+\.\d+-beta\.\d+[\]\)]?", "", subject, flags=re.I
    )
    # 2. Remove [pre-release] and [release] tags
    subject = re.sub(r"\s*\[pre-release\]", "", subject, flags=re.I)
    subject = re.sub(r"\s*\[release\]", "", subject, flags=re.I)
    # 3. Remove generic beta mentions at the end
    subject = re.sub(r"\s+beta\.\d+$", "", subject, flags=re.I)
    return subject.strip()


def is_core_change(commit_hash):
    # Check if this commit modified anything in ldm_core/ excluding pure version bumps
    try:
        diff = run(f"git show --name-only {commit_hash}").stdout
    except Exception:
        return False
    files = [f for f in diff.splitlines() if f.strip() and "ldm_core/" in f]
    actual_logic = [
        f for f in files if "constants.py" not in f and "pyproject.toml" not in f
    ]
    return len(actual_logic) > 0


def update_version_files(version):
    constants_path = "ldm_core/constants.py"
    pyproject_path = "pyproject.toml"

    if os.path.exists(constants_path):
        with open(constants_path, "r") as f:
            content = f.read()
        content = re.sub(r'VERSION = "[^"]+"', f'VERSION = "{version}"', content)
        content = re.sub(
            r"# LDM_MAGIC_VERSION: [^\n]+", f"# LDM_MAGIC_VERSION: {version}", content
        )
        with open(constants_path, "w") as f:
            f.write(content)

    if os.path.exists(pyproject_path):
        with open(pyproject_path, "r") as f:
            content = f.read()
        content = re.sub(r'version = "[^"]+"', f'version = "{version}"', content)
        with open(pyproject_path, "w") as f:
            f.write(content)


def rebuild():
    commits = get_commits()
    print(f"Rebuilding {len(commits)} commits starting from {BASE_COMMIT}...")

    # Ensure we are in a clean state
    run(f"git checkout master")
    run(f"git branch -D {BRANCH_NAME}", check=False)
    run(f"git checkout -b {BRANCH_NAME} {BASE_COMMIT}")

    beta_counter = 0

    for i, c in enumerate(commits):
        h = c["hash"]
        s = clean_subject(c["subject"])

        # Determine if we should bump beta
        bump_needed = is_core_change(h)

        # Apply the original commit content
        res = run(f"git cherry-pick -n {h}", check=False)
        if res.returncode != 0:
            print(f"Conflict in {h}, skipping version files and keeping existing logic...")
            run("git checkout HEAD -- ldm_core/constants.py pyproject.toml", check=False)
            run("git add .", check=False)

        final_subject = s
        if bump_needed:
            beta_counter += 1
            new_beta = f"2.4.26-beta.{beta_counter}"
            print(f"Bump detected: {h} -> {new_beta}")
            update_version_files(new_beta)
            run("git add ldm_core/constants.py pyproject.toml", check=False)
            # All beta releases MUST have [pre-release]
            final_subject = f"{s} [pre-release]"

        # Commit with cleaned message
        run(f"git add .", check=False)
        run(f'git commit -m "{final_subject}" --allow-empty --no-verify')

        if bump_needed:
            run(f"git tag -f v2.4.26-beta.{beta_counter}")

    print(f"Rebuild complete! Final version: 2.4.26-beta.{beta_counter}")


if __name__ == "__main__":
    rebuild()
