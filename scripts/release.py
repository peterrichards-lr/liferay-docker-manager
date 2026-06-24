#!/usr/bin/env python3
import argparse
import re
import subprocess
import sys
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


def main():
    parser = argparse.ArgumentParser(description="Automated Release Script")
    parser.add_argument(
        "--bump",
        choices=["major", "minor", "patch", "beta"],
        default="patch",
        help="SemVer increment type (default: patch)",
    )
    args = parser.parse_args()

    # 1. Fetch latest changes from master
    print("Fetching latest from master...")
    run_cmd(["git", "fetch", "origin", "master"])

    # 2. Check for uncommitted/untracked files
    status_res = run_cmd(["git", "status", "--porcelain"], capture=True)
    status_lines = status_res.stdout.splitlines()

    allowed_patterns = [
        re.compile(r"^.*\.md$", re.IGNORECASE),
        re.compile(r"^ldm_core/constants\.py$"),
        re.compile(r"^pyproject\.toml$"),
        re.compile(r"^GEMINI\.md$"),
        re.compile(r"^CHANGELOG\.md$"),
        # Allow the script itself to be untracked/modified
        re.compile(r"^scripts/release\.py$"),
    ]

    unallowed_files = []
    for line in status_lines:
        if not line.strip():
            continue
        # Format is 'XY path' or 'XY "path"' (if spaces)
        path_part = line[3:].strip().strip('"')

        # Check if the path is allowed
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

    # 3. Pull latest changes from master and ensure we are on master
    branch_res = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True)
    current_branch = branch_res.stdout.strip()
    print(f"Current branch: {current_branch}")

    if current_branch != "master":
        print("❌ Error: Releases must be initiated from the 'master' branch.")
        sys.exit(1)

    print("Pulling latest from master...")
    run_cmd(["git", "pull", "origin", "master"])

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

    # 6. Prepare release staging on master
    print("Preparing release staging on master...")

    # Quality Gate check: Format and Lint
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
            sys.exit(res.returncode)
        print("✅ Pre-commit quality gate checks passed.")
    else:
        ruff_bin = project_root / ".venv" / "bin" / "ruff"
        if ruff_bin.exists():
            print("Formatting Python files with Ruff...")
            run_cmd([str(ruff_bin), "format", "."])
            print("Linting and fixing Python files with Ruff...")
            run_cmd([str(ruff_bin), "check", ".", "--fix"], check=False)
            # Double check checks pass
            res = run_cmd([str(ruff_bin), "check", "."], check=False, capture=True)
            if res.returncode != 0:
                print(
                    f"\n❌ Error: Ruff lint checks failed. Please resolve lint issues before release:\n{res.stdout}"
                )
                sys.exit(res.returncode)
        else:
            import shutil

            sys_ruff = shutil.which("ruff")
            if sys_ruff:
                run_cmd([sys_ruff, "format", "."])
                run_cmd([sys_ruff, "check", ".", "--fix"], check=False)
                res = run_cmd([sys_ruff, "check", "."], check=False, capture=True)
                if res.returncode != 0:
                    print(
                        f"\n❌ Error: Ruff lint checks failed. Please resolve lint issues before release:\n{res.stdout}"
                    )
                    sys.exit(res.returncode)
            else:
                print(
                    "⚠️ Warning: Ruff formatter/linter not found. Skipping code formatting check."
                )

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

    # Also stage any other modified/untracked .md files
    status_res2 = run_cmd(["git", "status", "--porcelain"], capture=True)
    for line in status_res2.stdout.splitlines():
        if not line.strip():
            continue
        path_part = line[3:].strip().strip('"')
        if path_part.endswith(".md"):
            run_cmd(["git", "add", path_part])

    commit_msg = f"chore(release): bump version to v{new_version} [release]"
    run_cmd(["git", "commit", "-m", commit_msg, "--no-verify"])

    print("Pushing commits directly to master...")
    run_cmd(["git", "push", "origin", "master"])

    tag_name = f"v{new_version}"
    print(f"Creating release tag: {tag_name}...")
    run_cmd(["git", "tag", "-d", tag_name], check=False)
    run_cmd(["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"])

    print("Pushing release tag to remote origin...")
    run_cmd(["git", "push", "origin", tag_name])
    print(
        f"🎉 Release {tag_name} successfully tagged and pushed directly on master! Release run triggered on GitHub."
    )


if __name__ == "__main__":
    main()
