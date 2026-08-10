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
    res = subprocess.run(
        cmd, cwd=str(cwd), capture_output=capture, text=True, check=False
    )
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
                check=False,
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
            ["gh", "pr", "view", "--json", "number"],
            capture_output=True,
            text=True,
            check=False,
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
            check=False,
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
            check=False,
        )
        if res.returncode == 0:
            return json.loads(res.stdout).get("state")
    except Exception:
        pass
    return api_get_pr_state(pr_number)


def run_pre_commit_checks(branch_name, delete_branch_on_failure=True):
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
            if delete_branch_on_failure:
                # Only safe to delete when we're about to create a brand-new,
                # single-commit branch. An existing release/* branch being
                # continued may already have an open PR and prior history --
                # deleting the *local* copy there just means re-fetching it,
                # but never do it silently for a branch we didn't just create.
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


def main():  # noqa: C901, PLR0912, PLR0915
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

        # Regenerate the compatibility table now that VERSION is the new
        # stable release. sync_compatibility.py checks each report's own
        # recorded pre-release version against HEAD (see
        # get_promotable_stable_version() there) and only displays it under
        # the new stable version if nothing outside docs/version-metadata
        # changed since that report's tag -- so a promote that happens to
        # carry real code changes can't silently overstate what was
        # verified. Not gated behind any state passed from here: the same
        # check runs identically (and reaches the same answer) on any later,
        # unrelated re-sync too.
        print("Regenerating compatibility table for the promoted version...")
        run_cmd(
            [sys.executable, str(project_root / "scripts" / "sync_compatibility.py")],
            check=False,
        )

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
                # LDM-#1011: _apply_version_update() (ldm_core/handlers/dev.py)
                # also bumps these two files' embedded SCRIPT_VERSION on every
                # version change -- omitting them here left that bump
                # perpetually uncommitted/unstaged after every promote run,
                # silently drifting out of sync with the version it just
                # promoted to (caught before it ever bit a real promote).
                "scripts/verify_e2e_refactor.sh",
                "scripts/verify_e2e_refactor.ps1",
                # Compatibility table regenerated above.
                "docs/reference/compatibility.md",
                "docs/TESTING.md",
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

    # LDM-#983: a bump can either start a brand-new release cycle (from
    # master) or continue an already-open pre-release cycle (from its
    # existing release/vX.Y.Z-pre.N branch). Continuing on the existing
    # branch -- instead of always cutting a fresh release/vX.Y.Z-pre.{N+1}
    # branch and PR -- is what keeps every beta increment off master: only
    # `--promote` (below) is ever allowed to merge into master.
    is_continuing_release = current_branch.startswith("release/")

    if current_branch != "master" and not is_continuing_release:
        print(
            "❌ Error: Releases must be initiated from the 'master' branch (to start "
            "a new cycle) or from an existing 'release/*' pre-release branch (to "
            "continue one)."
        )
        sys.exit(1)

    if is_continuing_release and args.bump not in ("beta", "pre"):
        print(
            f"❌ Error: --bump {args.bump} is not valid while continuing an existing "
            f"release branch ('{current_branch}'). Continuing a cycle only accepts "
            "--bump beta; use --promote to finish it, or switch to 'master' to start "
            f"a fresh --bump {args.bump} cycle."
        )
        sys.exit(1)

    if is_continuing_release:
        print(f"Continuing existing pre-release cycle on '{current_branch}'...")
        run_cmd(["git", "pull", "origin", current_branch])
    else:
        print("Pulling latest from master...")
        run_cmd(["git", "pull", "origin", "master"])
    # 3. Quality Gate check: Format and Lint
    run_pre_commit_checks(
        current_branch, delete_branch_on_failure=not is_continuing_release
    )

    # 4. Bump the version using ldm system version
    # Retrieve current version before bump
    ver_res_before = run_cmd(
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
    current_version = ver_res_before.stdout.strip()
    parts = current_version.split("-", 1)
    base_version = parts[0]

    tag_check = run_cmd(["git", "tag", "-l", f"v{base_version}"], capture=True)
    if tag_check.stdout.strip():
        if args.bump in ["beta", "pre"]:
            base_parts = list(map(int, base_version.split(".")))
            while len(base_parts) < 3:
                base_parts.append(0)
            major, minor, patch = base_parts
            next_version = f"{major}.{minor}.{patch + 1}-pre.1"
            print(
                f"⚠️  Warning: Stable tag v{base_version} already exists. Auto-starting next cycle: {next_version}"
            )
            run_cmd(
                [
                    sys.executable,
                    str(project_root / "liferay_docker.py"),
                    "system",
                    "version",
                    "--set",
                    next_version,
                    "-y",
                ]
            )
        elif args.bump == "patch":
            print(
                f"❌ Error: Tag v{base_version} already exists. Cannot release duplicate stable version."
            )
            sys.exit(1)
        else:
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
    else:
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

    # 6. Create the release branch for a new cycle, or stay on the existing
    # one when continuing an already-open pre-release cycle (LDM-#983: this
    # is what lets multiple beta increments land without ever touching
    # master -- only `--promote` merges).
    if is_continuing_release:
        release_branch = current_branch
        print(f"Reusing existing release branch: {release_branch}")
    else:
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
            # LDM-#1011: kept in sync with the --promote branch's git-add list
            # above -- see the comment there.
            "scripts/verify_e2e_refactor.sh",
            "scripts/verify_e2e_refactor.ps1",
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

    # 8. Reuse the existing open tracking PR when continuing a cycle already
    # in progress, otherwise create a new one via gh CLI (with API fallback).
    pr_base = "master"
    pr_head = release_branch

    if is_continuing_release:
        existing_pr_num = get_pr_number(release_branch)
        if not existing_pr_num:
            print(
                f"❌ Error: Could not find an open tracking PR for branch {release_branch}. "
                "It may have been merged or closed already -- if this cycle is done, "
                "there's nothing to continue; start a fresh one from master instead."
            )
            sys.exit(1)
        pr_url = f"https://github.com/peterrichards-lr/liferay-docker-manager/pull/{existing_pr_num}"
        print(f"Reusing existing tracking PR: {pr_url}")
    else:
        print("Creating pull request...")
        pr_body = (
            f"Automated release bump to v{new_version}."
            if "-" not in new_version
            else (
                f"Pre-release tracking PR for v{new_version}.\n\n"
                "**Do not merge this PR manually.** It stays open and keeps "
                "collecting commits (further `--bump beta` cycles, fixes, "
                "verification results) for the duration of this pre-release "
                "cycle. Merging it as-is would land an unpromoted pre-release "
                "version directly on master.\n\n"
                "When testing confirms this pre-release is ready to ship, run "
                "`python3 scripts/release.py --promote` from the "
                f"`{release_branch}` branch instead -- it bumps the version to "
                "stable, commits that, and *then* merges this PR automatically."
            )
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

    if not is_continuing_release:
        # LDM-#1005: release/promote PRs aren't tied to a tracked issue --
        # label them so the mandatory issue-link-check (see #987) doesn't
        # fail on every single release cycle, requiring manual intervention.
        try:
            run_cmd(
                ["gh", "pr", "edit", str(pr_num), "--add-label", "no-issue-needed"],
                check=False,
            )
        except Exception as e:
            print(f"⚠️  Could not label PR #{pr_num} as no-issue-needed: {e}")

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
