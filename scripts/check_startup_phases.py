#!/usr/bin/env python3
"""Assert LDM recorded the phases of the boot it just performed (LDM-#2169).

The defect this guards is not a formatting one. Four of the five startup
milestones LDM tracked matched NOTHING in a real 2026.q3.7 log, and the fifth
matched the container entrypoint's shell banner 41 seconds before the portal
began starting. The unit tests for the fix assert the matcher and the renderer
against a captured log; nothing asserted that a REAL boot on a REAL Liferay
produces a usable breakdown, which is the only claim a user cares about.

That gap is the same shape as LDM-#2154: the suite walked `routes/default/dxp`
three times without reading a byte of it. Here the suite booted Liferay many
times without ever reading what LDM recorded about the boot.

Reads `<project>/.liferay-docker/startup-status.json`, which LDM writes on
EVERY boot whether or not `--benchmark` was passed. That is deliberate --
asserting the printed breakdown would need the run to have been invoked with
the flag, and an assertion that can only run under a flag is one more thing
that silently does not run (LDM-#2135).

Usage:
    check_startup_phases.py <project_dir>

It also asserts the boot's recorded OUTCOME (LDM-#2187). `"starting"` used to
be the only status ever written, and only from inside the poll loop -- so a
boot that succeeded left a file indistinguishable from one that stalled. The
suite always boots, so this costs nothing and catches a state the dashboard and
the MCP tools would otherwise read as "still starting" forever.

Exit codes:
    0  LDM recorded a usable set of phases, and a terminal outcome, for this boot
    1  it did not -- the message names what was recorded
    3  nothing to evaluate (no status file); the CALLER must announce this as
       SKIPPED, never as a pass

    Anything else means this check could not run. 3 is deliberately not 2:
    `python` itself exits 2 when it cannot open the script, and a missing
    checker reading as a benign skip is the failure mode this file exists to
    remove (LDM-#2154).
"""

import json
import sys
from pathlib import Path

#: Reaching Tomcat is the one milestone every successful boot must pass
#: through, on every Liferay line and every search mode. If the recorded set
#: does not contain it, the markers did not track this boot.
REQUIRED = "Tomcat Server Ready"

#: Statuses that mean the boot ENDED. `starting` is written while the readiness
#: loop runs; seeing it after the suite has booted means no exit path recorded
#: a verdict (LDM-#2187).
TERMINAL_STATUSES = ("ready", "timeout", "failed")

#: Below this, the breakdown cannot answer "where did the time go" -- which is
#: the whole point. Two means a single gap and no interior detail. Chosen
#: against a real capture that reached eight of ten.
MIN_PHASES = 3


def read_status(project_dir: Path):
    """The startup status LDM wrote for the last boot, or None."""
    path = project_dir / ".liferay-docker" / "startup-status.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def judge(status: dict) -> tuple[int, list[str]]:
    """The verdict: 0 when the recorded phases could answer a slow boot."""
    phases = status.get("milestone_seconds")
    if not isinstance(phases, dict) or not phases:
        return 1, [
            "ERROR: LDM recorded no per-milestone timings for this boot "
            "(LDM-#2169). 'milestone_seconds' is absent or empty, so a slow "
            "boot on this platform cannot be decomposed at all.",
            f"     recorded keys: {sorted(status)}",
        ]

    lines = ["   phases LDM recorded for this boot:"]
    for name, at in sorted(phases.items(), key=lambda kv: kv[1]):
        lines.append(f"     {name} = {at}s")

    if REQUIRED not in phases:
        return 1, [
            *lines,
            f"ERROR: '{REQUIRED}' was not recorded, yet the portal booted "
            f"(LDM-#2169). Every successful boot passes through it, so a "
            f"marker that misses it is not tracking this Liferay line.",
        ]

    if len(phases) < MIN_PHASES:
        return 1, [
            *lines,
            f"ERROR: only {len(phases)} milestone(s) recorded, fewer than the "
            f"{MIN_PHASES} needed to locate where a boot spent its time "
            f"(LDM-#2169). Before the fix, four of five markers matched "
            f"nothing on a real log and this is what that looks like.",
        ]

    # LDM-#2181: the span before the readiness wait. The headline figure has
    # never included it -- asset sync, compose render and container start --
    # so a report could not reconcile with the wall clock a user experienced.
    # Asserted only on a `ready` boot: a timeout or a failure may end before
    # the span is known.
    pre = status.get("pre_readiness_seconds")
    if status.get("status") == "ready" and not isinstance(pre, (int, float)):
        return 1, [
            *lines,
            "ERROR: LDM recorded no 'pre_readiness_seconds' for a boot that "
            "reached ready (LDM-#2181). Without it the published readiness "
            "figure cannot be reconciled with how long the command actually "
            "took, which is the gap that made the headline misleading.",
        ]

    recorded = status.get("status")
    if recorded not in TERMINAL_STATUSES:
        return 1, [
            *lines,
            f"ERROR: the boot finished, and LDM recorded its status as "
            f"'{recorded}' (LDM-#2187). One of {', '.join(TERMINAL_STATUSES)} "
            f"is expected once the readiness loop ends. A file left saying "
            f"'starting' is indistinguishable from a boot that stalled, and "
            f"'ldm dashboard' and the MCP tools read this file to answer what "
            f"the project is doing.",
        ]

    return 0, [
        *lines,
        f"ASSERTED: LDM recorded {len(phases)} startup phases including "
        f"'{REQUIRED}', and the boot's outcome as '{recorded}', so a slow boot "
        f"on this platform can be decomposed and a finished one can be told "
        f"from a stalled one.",
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_startup_phases.py <project_dir>")
        # 2, not 3: bad usage means the check did not run at all, which the
        # caller must treat as an error rather than as a skip.
        return 2

    status = read_status(Path(argv[1]))
    if status is None:
        print(
            "SKIPPED (not run): no '.liferay-docker/startup-status.json' for "
            "this project, so there is nothing to assert about the boot. LDM "
            "writes it while waiting for readiness."
        )
        return 3

    code, lines = judge(status)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
