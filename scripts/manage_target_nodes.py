#!/usr/bin/env python3
"""Standalone Target Node Power & Cost Control Utility -- repository entry point.

LDM-#2291: the implementation moved to `ldm_core/node_power.py` so that
`ldm node power` works in a packaged build, which never included this
`scripts/` directory. This file stays because the node-power workflows and the
docs invoke it by this path.

In the repository the config and state live at the repo root, so they are
pointed back there; an installed ldm keeps them under ~/.ldm instead.

Imported as a module (the tests do), it replaces itself with the real one, so
patching `manage_target_nodes.<name>` patches the code that actually runs.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from ldm_core import node_power  # noqa: E402

node_power.CONFIG_FILE = _REPO_ROOT / ".node-power-config.json"
node_power.STATE_FILE = _REPO_ROOT / ".node-power-state.json"

if __name__ == "__main__":
    node_power.main()
else:
    sys.modules[__name__] = node_power
