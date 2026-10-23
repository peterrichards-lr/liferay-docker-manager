#!/usr/bin/env python3
"""Write the repo-root .node-power-config.json from a secret, masking its values.

LDM-#2292: the node-power config named the maintainer's own compute nodes,
their EC2 instance ids and regions, and was committed to a public repository.
It now lives in the `NODE_POWER_CONFIG` Actions secret (the full JSON,
unchanged) and is written to disk only for the length of a workflow run.

This repository's Actions logs are public. GitHub masks a secret only where
its exact full value appears, so a node name, instance id, host or region
printed on its own -- which `status` and `wake` do -- would not be. Each of
those values is therefore registered with `::add-mask::` before anything that
could print it runs.

Standard library only: the workflows run it with a bare `python3`.
"""

import json
import os
import sys
from pathlib import Path

_MASKED_FIELDS = ("name", "ec2_instance_id", "host", "region")


def main() -> int:
    raw = os.environ.get("NODE_POWER_CONFIG", "")
    if not raw.strip():
        print("::error::NODE_POWER_CONFIG is not set; no nodes can be managed.")
        return 1
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Never echo the value: a malformed secret is still a secret.
        print(f"::error::NODE_POWER_CONFIG is not valid JSON ({exc.msg}).")
        return 1

    for name, node in (config.get("nodes") or {}).items():
        values = [name] + [str(node.get(field) or "") for field in _MASKED_FIELDS]
        for value in {v for v in values if v}:
            print(f"::add-mask::{value}")

    target = Path(__file__).resolve().parent.parent / ".node-power-config.json"
    target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(config.get('nodes') or {})} node(s) to {target.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
