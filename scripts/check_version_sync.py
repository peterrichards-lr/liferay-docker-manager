#!/usr/bin/env python3
import re
import sys
from pathlib import Path


def get_version_from_pyproject():
    path = Path("pyproject.toml")
    if not path.exists():
        return None, None
    content = path.read_text()

    match = re.search(r'version\s*=\s*"([^"]+)"', content)
    version_val = match.group(1) if match else None

    magic_match = re.search(r"# LDM_MAGIC_VERSION:\s*([^\n]+)", content)
    magic_val = magic_match.group(1).strip() if magic_match else None

    return version_val, magic_val


def get_version_from_constants():
    path = Path("ldm_core/constants.py")
    if not path.exists():
        return None, None
    content = path.read_text()

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
    p = Path(path)
    if not p.exists():
        return None, None
    content = p.read_text()

    version_match = re.search(version_pattern, content)
    version_val = version_match.group(1) if version_match else None

    magic_match = re.search(r"LDM_MAGIC_VERSION:\s*([^\n]+)", content)
    magic_val = magic_match.group(1).strip() if magic_match else None

    return version_val, magic_val


def main():
    v_pyproject, v_pyproject_magic = get_version_from_pyproject()
    v_constants, v_magic = get_version_from_constants()
    v_sh, v_sh_magic = get_script_version(
        "scripts/verify_e2e_refactor.sh", r'SCRIPT_VERSION="([^"]+)"'
    )
    v_ps1, v_ps1_magic = get_script_version(
        "scripts/verify_e2e_refactor.ps1", r'\$SCRIPT_VERSION = "([^"]+)"'
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

    if errors:
        print("❌ Version Synchronization Error(s) detected!")
        for err in errors:
            print(f"   - {err}")
        print("\nPlease synchronize them before committing.")
        sys.exit(1)

    print(f"✅ Versions are in sync (v{v_constants})")
    sys.exit(0)


if __name__ == "__main__":
    main()
