import subprocess
import sys
from pathlib import Path


def test_get_version_script_outputs_correctly():
    """
    Ensures that the scripts/get_version.py outputs exactly the semver
    version without any trailing newlines or extra text. This prevents
    CI scripts from failing when evaluating LDM_VERSION=$(python scripts/get_version.py).
    """
    script_path = (
        Path(__file__).resolve().parent.parent.parent / "scripts" / "get_version.py"
    )

    # Run the script
    result = subprocess.run(
        [sys.executable, str(script_path)], capture_output=True, text=True, check=True
    )

    # Import actual version to compare
    from ldm_core.constants import VERSION

    # The output MUST match the VERSION string exactly with NO trailing newline
    assert result.stdout == VERSION
    assert "\n" not in result.stdout
