#!/usr/bin/env python3
import subprocess
import os
import re

# Base commit before beta cycle (v2.4.24)
BASE_COMMIT = "eff9254"
# The clean source of truth for the code
SOURCE_BRANCH = "04fcc68" 
TARGET_BRANCH = "history-rebuild-safe"


def run(cmd, check=True):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def get_commits():
    # Get all commits from SOURCE_BRANCH since BASE_COMMIT
    res = run(f"git log {BASE_COMMIT}..{SOURCE_BRANCH} --oneline --reverse")
    commits = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        h, s = line.split(" ", 1)
        commits.append({"hash": h, "subject": s})
    return commits


def clean_subject(subject):
    # Strip existing version markers and release tags
    subject = re.sub(r"\s*[\[\(]?v\d+\.\d+\.\d+-beta\.\d+[\]\)]?", "", subject, flags=re.I)
    subject = re.sub(r"\s*\[pre-release\]", "", subject, flags=re.I)
    subject = re.sub(r"\s*\[release\]", "", subject, flags=re.I)
    return subject.strip()


def is_core_change(commit_hash):
    try:
        diff = run(f"git show --name-only {commit_hash}").stdout
    except Exception:
        return False
    # Any change in ldm_core/ excluding constants
    files = [f for f in diff.splitlines() if f.strip() and "ldm_core/" in f and "constants.py" not in f]
    return len(files) > 0


def update_version_files(version):
    paths = ["ldm_core/constants.py", "pyproject.toml"]
    for p in paths:
        if os.path.exists(p):
            content = open(p).read()
            if "constants.py" in p:
                content = re.sub(r'VERSION = "[^"]+"', f'VERSION = "{version}"', content)
                content = re.sub(r'META_VERSION = "[^"]+"', f'META_VERSION = "{version}"', content)
                content = re.sub(r'# LDM_MAGIC_VERSION: [^\n]+', f'# LDM_MAGIC_VERSION: {version}', content)
            else:
                content = re.sub(r'version = "[^"]+"', f'version = "{version}"', content)
            open(p, "w").write(content)


def rebuild():
    commits = get_commits()
    print(f"Rebuilding {len(commits)} commits from {SOURCE_BRANCH} onto {BASE_COMMIT}...")

    run(f"git checkout {BASE_COMMIT}")
    run(f"git branch -D {TARGET_BRANCH}", check=False)
    run(f"git checkout -b {TARGET_BRANCH}")

    beta_counter = 0

    for c in commits:
        h = c["hash"]
        s = clean_subject(c["subject"])
        
        # Skip utility scripts
        if "rebuild" in s.lower() or "utility" in s.lower() or "linearize" in s.lower():
            continue

        bump_needed = is_core_change(h)
        
        # Cherry-pick with -Xtheirs to avoid conflict markers
        # This will automatically resolve almost all conflicts by taking the incoming code.
        res = run(f"git cherry-pick -Xtheirs -n {h}", check=False)
        if res.returncode != 0:
            print(f"Residual conflict in {h}, force cleaning...")
            run("git checkout --theirs .", check=False)
            run("git add .", check=False)

        final_subject = s
        if bump_needed:
            beta_counter += 1
            new_beta = f"2.4.26-beta.{beta_counter}"
            print(f"Bumping to {new_beta} for: {h} ({s})")
            update_version_files(new_beta)
            run("git add ldm_core/constants.py pyproject.toml", check=False)
            final_subject = f"{s} [pre-release]"

        run(f'git commit -m "{final_subject}" --allow-empty --no-verify')

        if bump_needed:
            run(f"git tag -f v2.4.26-beta.{beta_counter}")

    print(f"Rebuild complete! Reached beta.{beta_counter}")


if __name__ == "__main__":
    rebuild()
