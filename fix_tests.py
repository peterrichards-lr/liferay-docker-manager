import os
import re
from pathlib import Path

tests_dir = Path("ldm_core/tests")

replacements = {
    # get_compose_cmd was mostly called by orchestration
    r"ldm_core\.handlers\.runtime\.get_compose_cmd": r"ldm_core.runtime.orchestration.get_compose_cmd",
    # UI is used all over, but UI methods are usually mocked globally or at their usage site.
    # If they are mocked at usage site in runtime, it's either in orchestration or readiness.
    # Actually, the safest is to mock `ldm_core.ui.UI` globally, or the specific module importing it.
    # Let's replace it with `ldm_core.ui.UI` which works as long as the module doesn't import UI as a local reference (from ldm_core.ui import UI is used, so patching ldm_core.ui.UI might not work if they already imported it, but actually `mock.patch("ldm_core.ui.UI")` is standard in Python 3 if the target imports it before patch. Wait, if a module does `from ldm_core.ui import UI`, you MUST patch it where it is used, e.g. `ldm_core.runtime.orchestration.UI`).
    # Let's just use `ldm_core.runtime.orchestration.UI` for test_downgrade and test_upgrade.
    r"ldm_core\.handlers\.runtime\.UI": r"ldm_core.runtime.orchestration.UI",
    # shutil and datetime
    r"ldm_core\.handlers\.runtime\.shutil": r"ldm_core.runtime.orchestration.shutil",
    r"ldm_core\.handlers\.runtime\.datetime": r"ldm_core.runtime.readiness.datetime",
}

for root, _, files in os.walk(tests_dir):
    for file in files:
        if file.endswith(".py"):
            filepath = Path(root) / file
            content = filepath.read_text()
            original_content = content
            for old, new in replacements.items():
                content = re.sub(old, new, content)

            # Special cases where UI might be used in snapshot/upgrade which isn't orchestration:
            # test_snapshot.py uses snapshot module, so it should patch ldm_core.handlers.snapshot.UI ?
            # No, wait, test_snapshot.py patched ldm_core.handlers.runtime.UI because it was testing `cmd_run` or something?
            # test_sidecar.py patched get_compose_cmd...

            if content != original_content:
                filepath.write_text(content)
                print(f"Updated {filepath}")
