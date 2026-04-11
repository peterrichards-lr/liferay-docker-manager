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
        return None
    content = path.read_text()
    match = re.search(r'VERSION\s*=\s*"([^"]+)"', content)
    return match.group(1) if match else None


def main():
    v1 = get_version_from_pyproject()
    v2 = get_version_from_constants()

    if not v1 or not v2:
        print(
            "❌ Error: Could not find version in pyproject.toml or ldm_core/constants.py"
        )
        sys.exit(1)

    if v1 != v2:
        print("❌ Version Mismatch detected!")
        print(f"   pyproject.toml: {v1}")
        print(f"   ldm_core/constants.py: {v2}")
        print("   Please synchronize them before committing.")
        sys.exit(1)

    print(f"✅ Versions are in sync (v{v1})")
    sys.exit(0)


if __name__ == "__main__":
    main()
