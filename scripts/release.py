#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


def run_cmd(cmd, cwd=project_root, check=True, capture=False):
    """Helper to run a shell command."""
    res = subprocess.run(cmd, cwd=str(cwd), capture_output=capture, text=True)
    if check and res.returncode != 0:
        print(f"Error executing command: {' '.join(cmd)}")
        if capture:
            print(res.stderr)
        sys.exit(res.returncode)
    return res


def call_github_api(url_path, method="GET", data=None):
    token = os.environ.get("GITHUB_PAT")
    if not token:
        # Try to read token from osxkeychain if not in env
        try:
            res = subprocess.run(
                ["git", "credential", "fill"],
                input="protocol=https\nhost=github.com\n",
                capture_output=True,
                text=True,
            )
            for line in res.stdout.splitlines():
                if line.startswith("password="):
                    token = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass

    if not token:
        print("Error: GITHUB_PAT or keychain credential not found.")
        sys.exit(1)

    url = f"https://api.github.com/repos/peterrichards-lr/liferay-docker-manager{url_path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data else None,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Antigravity-Release-Script",
        },
        method=method,
    )
    if data:
        req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def api_get_pr_number(branch_name):
    try:
        res = call_github_api(f"/pulls?head=peterrichards-lr:{branch_name}&state=open")
        if res:
            return res[0]["number"]
    except Exception as e:
        print(f"API Error finding PR for branch {branch_name}: {e}")
    return None


def api_merge_pr(pr_number):
    try:
        call_github_api(
            f"/pulls/{pr_number}/merge",
            method="PUT",
            data={"merge_method": "squash"},
        )
        return True
    except Exception as e:
        print(f"API Error merging PR #{pr_number}: {e}")
    return False


def api_delete_branch(branch_name):
    try:
        call_github_api(f"/git/refs/heads/{branch_name}", method="DELETE")
        return True
    except Exception as e:
        print(f"API Error deleting branch {branch_name}: {e}")
    return False


def api_get_pr_state(pr_number):
    try:
        res = call_github_api(f"/pulls/{pr_number}")
        return res.get("state")
    except Exception as e:
        print(f"API Error getting PR state: {e}")
    return "UNKNOWN"


def get_pr_number(branch_name):
    try:
        res = subprocess.run(
            ["gh", "pr", "view", "--json", "number"], capture_output=True, text=True
        )
        if res.returncode == 0:
            return json.loads(res.stdout).get("number")
    except Exception:
        pass
    return api_get_pr_number(branch_name)


def merge_pr(pr_number, branch_name):
    try:
        res = subprocess.run(
            [
                "gh",
                "pr",
                "merge",
                str(pr_number),
                "--auto",
                "--squash",
                "--delete-branch",
            ],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            print("🎉 Release PR successfully merged / set to auto-merge via gh CLI.")
            return True
    except Exception:
        pass
    print("Falling back to GitHub API for merging...")
    if api_merge_pr(pr_number):
        print(f"PR #{pr_number} squash merged via API.")
        api_delete_branch(branch_name)
        return True
    return False


def get_pr_state(pr_number):
    try:
        res = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "state"],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            return json.loads(res.stdout).get("state")
    except Exception:
        pass
    return api_get_pr_state(pr_number)


def run_pre_commit_checks(branch_name):
    print("Running code formatting and lint checks...")
    pre_commit_bin = project_root / ".venv" / "bin" / "pre-commit"
    if pre_commit_bin.exists():
        print("Running pre-commit quality gate checks...")
        res = run_cmd(
            [str(pre_commit_bin), "run", "--all-files"], check=False, capture=True
        )
        if res.returncode != 0:
            print(
                f"\n❌ Error: Pre-commit quality gate checks failed. Please resolve lint issues before release:\n{res.stdout}\n{res.stderr or ''}"
            )
            run_cmd(["git", "checkout", "master"])
            run_cmd(["git", "branch", "-D", branch_name])
            sys.exit(res.returncode)
        print("✅ Pre-commit quality gate checks passed.")


def poll_pr_merge(pr_num):
    print("Waiting for PR checks to pass and merge to complete...")
    import time

    while True:
        state = get_pr_state(pr_num)
        if state == "MERGED":
            print(f"\n🎉 PR #{pr_num} successfully merged!")
            break
        if state == "CLOSED":
            print(f"\n❌ PR #{pr_num} was closed without merging!")
            sys.exit(1)
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(15)


def create_and_push_tag(version):
    print("\nChecking out master locally and pulling latest changes...")
    run_cmd(["git", "checkout", "master"])
    run_cmd(["git", "pull", "origin", "master"])

    tag_name = f"v{version}"
    print(f"Creating release tag: {tag_name}...")
    run_cmd(["git", "tag", "-d", tag_name], check=False)
    run_cmd(["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"])

    print("Pushing release tag to remote origin...")
    run_cmd(["git", "push", "origin", tag_name])
    print(
        f"🎉 Release {tag_name} successfully tagged and pushed! Release run triggered on GitHub."
    )


def main():
    parser = argparse.ArgumentParser(description="Automated Release Script")
    parser.add_argument(
        "--bump",
        choices=["major", "minor", "patch", "beta"],
        default="patch",
        help="SemVer increment type (default: patch)",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Promote the current pre-release/beta branch to a stable release",
    )
    args = parser.parse_args()

    # 1. Fetch latest changes from master
    print("Fetching latest from master...")
    run_cmd(["git", "fetch", "origin", "master"])

    # Resolve current branch
    branch_res = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True)
    current_branch = branch_res.stdout.strip()
    print(f"Current branch: {current_branch}")

    if args.promote:
        # Handle promotion logic
        if not current_branch.startswith("release/"):
            print("❌ Error: Promotion must be run from a 'release/' branch.")
            sys.exit(1)

        # Get current version from constants.py to verify it is a pre-release
        ver_res = run_cmd(
            [
                sys.executable,
                str(project_root / "liferay_docker.py"),
                "system",
                "version",
                "--print",
                "-y",
            ],
            capture=True,
        )
        current_version = ver_res.stdout.strip()
        if "-" not in current_version:
            print(
                f"❌ Error: Current version '{current_version}' is already stable. Cannot promote."
            )
            sys.exit(1)

        print(
            f"Promoting pre-release branch {current_branch} (version {current_version}) to stable..."
        )

        # Bump the version using ldm system version --promote
        run_cmd(
            [
                sys.executable,
                str(project_root / "liferay_docker.py"),
                "system",
                "version",
                "--promote",
                "-y",
            ]
        )

        # Get the promoted version
        ver_res2 = run_cmd(
            [
                sys.executable,
                str(project_root / "liferay_docker.py"),
                "system",
                "version",
                "--print",
                "-y",
            ],
            capture=True,
        )
        new_version = ver_res2.stdout.strip()
        print(f"Promoted to stable version: {new_version}")

        # Add, commit, and push
        print("Staging and committing promoted version...")
        run_cmd(
            [
                "git",
                "add",
                "GEMINI.md",
                "CHANGELOG.md",
                "ldm_core/constants.py",
                "pyproject.toml",
                "scripts/release.py",
            ]
        )
        commit_msg = f"chore(release): promote version to v{new_version} [release]"
        run_cmd(["git", "commit", "-m", commit_msg, "--no-verify"])

        print("Pushing promoted version to origin...")
        run_cmd(["git", "push", "origin", "HEAD"])

        # Merge the PR
        pr_num = get_pr_number(current_branch)
        if not pr_num:
            print(
                f"❌ Error: Could not find open Pull Request for branch {current_branch}."
            )
            sys.exit(1)

        print(f"Merging PR #{pr_num}...")
        if not merge_pr(pr_num, current_branch):
            print("❌ Error: Failed to merge the PR.")
            sys.exit(1)

        # Poll for merge completion
        poll_pr_merge(pr_num)

        # Create tag on master
        create_and_push_tag(new_version)
        return

    # Standard Release (non-promote)
    # 2. Check for uncommitted/untracked files
    status_res = run_cmd(["git", "status", "--porcelain"], capture=True)
    status_lines = status_res.stdout.splitlines()

    allowed_patterns = [
        re.compile(r"^.*\.md$", re.IGNORECASE),
        re.compile(r"^ldm_core/constants\.py$"),
        re.compile(r"^pyproject\.toml$"),
        re.compile(r"^GEMINI\.md$"),
        re.compile(r"^CHANGELOG\.md$"),
        re.compile(r"^scripts/release\.py$"),
    ]

    unallowed_files = []
    for line in status_lines:
        if not line.strip():
            continue
        path_part = line[3:].strip().strip('"')
        allowed = False
        for pattern in allowed_patterns:
            if pattern.match(path_part) or path_part.endswith(".md"):
                allowed = True
                break
        if not allowed:
            unallowed_files.append(path_part)

    if unallowed_files:
        print(
            "\n❌ Error: Uncommitted changes detected in non-version/non-documentation files:"
        )
        for f in unallowed_files:
            print(f"  - {f}")
        print("\nAbort release. Please stash or commit these changes first.")
        sys.exit(1)

    print("✅ Workspace contains only documentation and version configuration files.")

    if current_branch != "master":
        print("❌ Error: Releases must be initiated from the 'master' branch.")
        sys.exit(1)

    print("Pulling latest from master...")
    run_cmd(["git", "pull", "origin", "master"])
    # 3. Quality Gate check: Format and Lint
    run_pre_commit_checks(current_branch)

    # 4. Bump the version using ldm system version
    print(f"Bumping version with logic: {args.bump}...")
    run_cmd(
        [
            sys.executable,
            str(project_root / "liferay_docker.py"),
            "system",
            "version",
            "--bump",
            args.bump,
            "-y",
        ]
    )

    # 5. Get the bumped version
    ver_res = run_cmd(
        [
            sys.executable,
            str(project_root / "liferay_docker.py"),
            "system",
            "version",
            "--print",
            "-y",
        ],
        capture=True,
    )
    new_version = ver_res.stdout.strip()
    print(f"Bumped to new version: {new_version}")

    # 6. Create a release branch for the PR
    release_branch = f"release/v{new_version}"
    print(f"Creating release branch: {release_branch}...")
    run_cmd(["git", "checkout", "-b", release_branch])

    # 7. Add, commit, and push
    print("Staging and committing files...")
    run_cmd(
        [
            "git",
            "add",
            "GEMINI.md",
            "CHANGELOG.md",
            "ldm_core/constants.py",
            "pyproject.toml",
            "scripts/release.py",
        ]
    )

    status_res2 = run_cmd(["git", "status", "--porcelain"], capture=True)
    for line in status_res2.stdout.splitlines():
        if not line.strip():
            continue
        path_part = line[3:].strip().strip('"')
        if path_part.endswith(".md"):
            run_cmd(["git", "add", path_part])

    commit_msg = f"chore(release): bump version to v{new_version} [release]"
    run_cmd(["git", "commit", "-m", commit_msg, "--no-verify"])

    print("Pushing to origin...")
    run_cmd(["git", "push", "origin", "HEAD"])

    # 8. Create the PR using gh CLI (with fallback)
    print("Creating pull request...")
    pr_base = "master"
    pr_head = release_branch
    pr_body = (
        f"Automated release bump to v{new_version}."
        if "-" not in new_version
        else f"Pre-release tracking PR for v{new_version}. Merging this PR will promote the changes to master, after which a stable release can be tagged."
    )

    # Try gh first
    pr_url = None
    try:
        pr_cmd = [
            "gh",
            "pr",
            "create",
            "--base",
            pr_base,
            "--head",
            pr_head,
            "--title",
            commit_msg,
            "--body",
            pr_body,
        ]
        pr_res = run_cmd(pr_cmd, capture=True)
        pr_url = pr_res.stdout.strip()
    except Exception:
        pass

    if not pr_url:
        print("Falling back to GitHub API for PR creation...")
        try:
            res_data = call_github_api(
                "/pulls",
                method="POST",
                data={
                    "title": commit_msg,
                    "head": pr_head,
                    "base": pr_base,
                    "body": pr_body,
                },
            )
            pr_url = res_data["html_url"]
        except Exception as e:
            print("❌ Error creating PR via API:", e)
            sys.exit(1)

    print(f"PR Created: {pr_url}")
    pr_num = pr_url.split("/")[-1]

    # If pre-release, tag directly on the branch and exit
    if "-" in new_version:
        tag_name = f"v{new_version}"
        print(
            f"Pre-release detected. Tagging directly on the release branch: {tag_name}..."
        )
        run_cmd(["git", "tag", "-d", tag_name], check=False)
        run_cmd(["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"])
        print("Pushing release tag to remote origin...")
        run_cmd(["git", "push", "origin", tag_name])
        print(
            f"🎉 Pre-release {tag_name} successfully tagged and pushed on branch '{release_branch}'!\n"
            f"The tracking PR is open at {pr_url} for testing. Close/merge it to promote when ready."
        )
        return

    # 9. Auto-merge the PR
    print(f"Merging PR #{pr_num}...")
    if not merge_pr(pr_num, release_branch):
        print("❌ Error: Failed to merge PR.")
        sys.exit(1)

    # 10. Poll for PR to be merged
    poll_pr_merge(pr_num)

    # 11. Tag on master
    create_and_push_tag(new_version)


if __name__ == "__main__":
    main()
