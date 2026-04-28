#!/usr/bin/env python3
import re
import sys
from pathlib import Path


def get_version_from_pyproject():
    path = Path("pyproject.toml")
    if not path.exists():
        return None
    content = path.read_text()
    match = re.search(r'version\s*=\s*"([^"]+)"', content)
    return match.group(1) if match else None


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


def main():
    v_pyproject = get_version_from_pyproject()
    v_constants, v_magic = get_version_from_constants()

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
