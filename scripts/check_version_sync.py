#!/usr/bin/env python3
import argparse
import re
import subprocess  # nosec B404 - fixed argv, no shell
import sys
from pathlib import Path

# Set by main() when --ref is given. None means "read the working tree".
_REF = None


def _read(path):
    """File contents from the working tree, or from _REF if one is set.

    LDM-#1498: reading the working tree cannot catch the failure this guard
    exists for. When v2.19.0-pre.2 was tagged, the working tree was entirely
    consistent -- ldm.1 had been rewritten to pre.2 -- and only the COMMIT was
    missing it, because the file was never staged (#1491). A working-tree check
    at any point in that run would have passed, and did.

    So the release path checks the committed tree with `--ref HEAD`: what gets
    tagged, rather than what happens to be lying on disk.

    Returns None when the file does not exist at that ref, matching the
    Path.exists() behaviour the callers already handle.
    """
    if _REF is None:
        p = Path(path)
        return p.read_text() if p.exists() else None
    try:
        return subprocess.run(  # nosec B603 - fixed argv, no shell
            ["git", "show", f"{_REF}:{path}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return None


def get_version_from_pyproject():
    content = _read("pyproject.toml")
    if content is None:
        return None, None

    match = re.search(r'version\s*=\s*"([^"]+)"', content)
    version_val = match.group(1) if match else None

    magic_match = re.search(r"# LDM_MAGIC_VERSION:\s*([^\n]+)", content)
    magic_val = magic_match.group(1).strip() if magic_match else None

    return version_val, magic_val


def get_version_from_constants():
    content = _read("ldm_core/constants.py")
    if content is None:
        return None, None

    # 1. Get variable
    var_match = re.search(r'VERSION\s*=\s*"([^"]+)"', content)
    version_var = var_match.group(1) if var_match else None

    # 2. Get magic comment
    magic_match = re.search(r"# LDM_MAGIC_VERSION:\s*([^\n]+)", content)
    magic_val = magic_match.group(1).strip() if magic_match else None

    return version_var, magic_val


def get_script_version(path, version_pattern):
    """Extracts a SCRIPT_VERSION-style marker from a verify-script file.

    LDM-#1011: verify_e2e_refactor.sh/.ps1 each embed their own SCRIPT_VERSION
    so a locally-held copy can be checked against what actually shipped.
    Returns (version_value, magic_comment_value), either possibly None if the
    file or the corresponding marker doesn't exist.
    """
    content = _read(path)
    if content is None:
        return None, None

    version_match = re.search(version_pattern, content)
    version_val = version_match.group(1) if version_match else None

    magic_match = re.search(r"LDM_MAGIC_VERSION:\s*([^\n]+)", content)
    magic_val = magic_match.group(1).strip() if magic_match else None

    return version_val, magic_val


def main():
    global _REF  # noqa: PLW0603
    parser = argparse.ArgumentParser(
        description="Verify every file carrying the version agrees.",
    )
    parser.add_argument(
        "--ref",
        default=None,
        metavar="REF",
        help=(
            "Check the files as committed at REF (e.g. HEAD) rather than the "
            "working tree. The release path uses this: a working-tree check "
            "cannot see a file rewritten but never staged (LDM-#1498)."
        ),
    )
    args = parser.parse_args()
    _REF = args.ref
    where = f"at {args.ref}" if args.ref else "in the working tree"

    v_pyproject, v_pyproject_magic = get_version_from_pyproject()
    v_constants, v_magic = get_version_from_constants()
    v_sh, v_sh_magic = get_script_version(
        "scripts/verify_e2e_refactor.sh", r'SCRIPT_VERSION="([^"]+)"'
    )
    v_ps1, v_ps1_magic = get_script_version(
        "scripts/verify_e2e_refactor.ps1", r'\$SCRIPT_VERSION = "([^"]+)"'
    )
    # LDM-#1482: the man page carries its own version in the .TH macro. It
    # ships in the binary and is installed into the user's man directory, so a
    # stale stamp is user-facing -- it read 2.15.22 for four minor releases.
    v_man, _ = get_script_version(
        "ldm_core/resources/ldm.1", r'^\.TH LDM 1 "[^"]*" "([^"]+)"'
    )

    if not v_pyproject or not v_constants:
        print(
            "❌ Error: Could not find version in pyproject.toml or ldm_core/constants.py"
        )
        sys.exit(1)

    errors = []

    # Check 1: pyproject vs constants
    if v_pyproject != v_constants:
        errors.append(
            f"Mismatch: pyproject.toml ({v_pyproject}) != ldm_core/constants.py variable ({v_constants})"
        )

    # Check 2: constants variable vs magic comment
    if v_magic and v_magic != v_constants:
        errors.append(
            f"Mismatch: ldm_core/constants.py variable ({v_constants}) != magic comment ({v_magic})"
        )

    # Check 3: pyproject.toml's own magic comment vs its version value
    # (previously unchecked -- this drifted silently for multiple releases)
    if v_pyproject_magic and v_pyproject_magic != v_pyproject:
        errors.append(
            f"Mismatch: pyproject.toml version ({v_pyproject}) != pyproject.toml magic comment ({v_pyproject_magic})"
        )

    # Check 4/5: verify_e2e_refactor.sh/.ps1's embedded SCRIPT_VERSION (LDM-#1011)
    if v_sh and v_sh != v_constants:
        errors.append(
            f"Mismatch: ldm_core/constants.py ({v_constants}) != scripts/verify_e2e_refactor.sh SCRIPT_VERSION ({v_sh})"
        )
    if v_sh_magic and v_sh_magic != v_sh:
        errors.append(
            f"Mismatch: scripts/verify_e2e_refactor.sh SCRIPT_VERSION ({v_sh}) != magic comment ({v_sh_magic})"
        )
    if v_ps1 and v_ps1 != v_constants:
        errors.append(
            f"Mismatch: ldm_core/constants.py ({v_constants}) != scripts/verify_e2e_refactor.ps1 SCRIPT_VERSION ({v_ps1})"
        )
    if v_ps1_magic and v_ps1_magic != v_ps1:
        errors.append(
            f"Mismatch: scripts/verify_e2e_refactor.ps1 SCRIPT_VERSION ({v_ps1}) != magic comment ({v_ps1_magic})"
        )

    # Check 6: man page .TH version (LDM-#1482)
    if v_man and v_man != v_constants:
        errors.append(
            f"Mismatch: ldm_core/constants.py ({v_constants}) != "
            f"ldm_core/resources/ldm.1 .TH version ({v_man})"
        )

    if errors:
        print(f"❌ Version Synchronization Error(s) detected {where}!")
        for err in errors:
            print(f"   - {err}")
        print("\nPlease synchronize them before committing.")
        sys.exit(1)

    print(f"✅ Versions are in sync {where} (v{v_constants})")
    sys.exit(0)


if __name__ == "__main__":
    main()
