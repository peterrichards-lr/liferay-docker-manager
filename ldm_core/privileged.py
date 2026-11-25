"""The "privileged step required" contract (LDM-#2517).

When LDM needs root and cannot ask for a password -- `-y`/`--non-interactive`,
where it only ever tries `sudo -n` -- it does not hang and it does not bury the
remedy in prose. It names the exact command for a person to run, and exits
with ``PRIVILEGED_STEP_EXIT_CODE``.

Two shapes, one per kind of call site:

- ``require()`` -- the step BLOCKS the command (the stack cannot start until a
  hostname resolves; the upgrade cannot finish until the binary is replaced).
  It reports and exits at once.
- ``defer()`` -- the step is cleanup or an optional extra (removing old hosts
  entries during `ldm rm`). The command carries on, and exits with the code
  when it would otherwise have succeeded. Exiting mid-teardown to report a
  stale hosts line would leave the containers it was about to remove running.

The command is always an argv list, so it is shell-quoted exactly once, by
``shlex.join``, for the human form -- and copied verbatim into the JSON. LDM
never reads, accepts or stores a password on this path.

Machine-readable form: with ``--json`` on the command, or ``LDM_JSON=1`` in the
environment for commands that have no ``--json`` of their own, each step is
also printed to stdout as one JSON object on its own line::

    {"privileged_step": {"reason": "...", "command": ["sudo", ...], "resume": "ldm run x"}}

See docs/reference/exit_codes.md.
"""

import json
import os
import shlex
import sys

from ldm_core.ui import UI

# The next free code in LDM's own 0-5 contract. Not 2 (authentication):
# automation reads 2 as "supply a credential and retry", which is exactly what
# must NOT happen here -- the step needs a human with a password, not a token.
PRIVILEGED_STEP_EXIT_CODE = 6

JSON_ENV_VAR = "LDM_JSON"

# One process runs one command, so this is per-command state. Mutable
# containers rather than rebound globals; `reset()` clears both.
_state = {"json": False}
_deferred: list[dict] = []


def set_json_mode(enabled):
    """Record that the running command was asked for `--json`."""
    _state["json"] = bool(enabled)


def json_requested():
    """True when the step should also be printed as JSON on stdout."""
    if _state["json"]:
        return True
    return os.environ.get(JSON_ENV_VAR, "").strip().lower() in ("1", "true", "yes")


def build_payload(reason, command, resume=None):
    """The documented payload. ``command`` must be an argv list."""
    if isinstance(command, str) or not isinstance(command, (list, tuple)):
        raise TypeError("command must be an argv list, not a shell string")
    return {
        "privileged_step": {
            "reason": str(reason),
            "command": [str(part) for part in command],
            "resume": resume,
        }
    }


def _report(payload, alternative=None):
    step = payload["privileged_step"]
    UI.error(f"A privileged step is required: {step['reason']}")
    UI._print(
        "   LDM will not ask for a password here. Run this yourself, in a "
        "terminal where sudo can prompt:",
        file=sys.stderr,
    )
    # Plain print, not UI._print: that strips the indent and redacts, and the
    # whole point of this line is that it can be copied and run unchanged.
    print(f"       {shlex.join(step['command'])}", file=sys.stderr, flush=True)
    if alternative:
        print(
            f"   Or let LDM ask for it, from an interactive terminal: {alternative}",
            file=sys.stderr,
            flush=True,
        )
    if step["resume"]:
        print(f"   Then continue with: {step['resume']}", file=sys.stderr, flush=True)
    if json_requested():
        print(json.dumps(payload), flush=True)


def require(reason, command, resume=None, alternative=None):
    """Report a blocking privileged step and exit with the dedicated code.

    ``alternative`` is an optional human-only hint (e.g. the `ldm` command that
    does the same thing interactively); it is not part of the JSON payload.
    """
    _report(build_payload(reason, command, resume), alternative)
    sys.exit(PRIVILEGED_STEP_EXIT_CODE)


def defer(reason, command, resume=None, alternative=None):
    """Report a non-blocking privileged step now; exit with the code later."""
    payload = build_payload(reason, command, resume)
    _report(payload, alternative)
    _deferred.append(payload)
    return payload


def has_deferred():
    return bool(_deferred)


def reset():
    """Clear module state (used by tests and between in-process commands)."""
    _state["json"] = False
    _deferred.clear()


def sudo_sh(script):
    """``["sudo", "sh", "-c", script]`` -- one argv for a multi-step remedy.

    Lets a remedy that needs a redirect or ``&&`` still travel as an argv list,
    so ``shlex.join`` quotes it correctly and the JSON carries the same thing.
    """
    return ["sudo", "sh", "-c", script]
