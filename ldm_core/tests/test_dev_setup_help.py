import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent / "liferay_docker.py"


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_dev_setup_help_prints_help_and_bootstraps_nothing(tmp_path, flag):
    """LDM-#2477: `dev-setup --help` ran the bootstrap -- it created `.venv`
    if absent and ran pip in it -- instead of printing help.

    `.venv` is a plain file here, so the bootstrap finds no interpreter in it
    and exits before any install. Against the unfixed interceptor this test
    therefore fails without installing anything.
    """
    (tmp_path / ".venv").write_text("canary")
    env = {**os.environ, "LDM_HOME": str(tmp_path / "ldm-home")}

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "dev-setup", flag],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout.lower()
    assert "Bootstrap" not in result.stdout
    assert (tmp_path / ".venv").read_text() == "canary"
