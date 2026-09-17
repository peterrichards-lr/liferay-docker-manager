"""Import `scripts/release.py` so its gates can be driven, not just read.

LDM-#1770. Two release gates were verified only by `assertIn` on this script's
source text, which meant both suites passed against a copy with the gate
disabled. Testing them properly means calling the real functions, and calling
them means importing a file that is not on the import path.

Not a test module -- the name deliberately does not start with `test_`, so
pytest does not collect it.

**Importing executes the module.** That is safe only while `release.py` has no
top-level side effects, which is asserted by
`test_promotion_delta_gate.ImportingReleaseIsSafe` rather than assumed here. If
someone adds top-level work to it, that test fails before this loader can run it
against a developer's real repository.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RELEASE_PY = REPO_ROOT / "scripts" / "release.py"


# Returns `Any`, not `ModuleType`: the module is loaded at runtime, so mypy
# cannot know it has `run_cmd` or `abort_release` -- the very attributes the
# tests stub. Annotating it honestly is better than sprinkling `type: ignore`
# over every stub site.
def load_release(path: Path | None = None, name: str = "ldm_release_under_test") -> Any:
    """Return `release.py` as a module.

    `path` exists so a test can point the loader at a deliberately modified copy
    -- that is how "does this suite notice the gate being disabled?" is answered
    without touching the repository.
    """
    target = path or RELEASE_PY
    spec = importlib.util.spec_from_file_location(name, target)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {target} as a module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
