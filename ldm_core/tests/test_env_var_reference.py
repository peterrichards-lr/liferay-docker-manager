"""LDM-#2025: the environment variables LDM reads must be findable.

`docs/reference/configuration.md` is where a reader looks for them. Three
variables shipped in the v2.26.0 cycle -- `LDM_DOCKER_TUNNEL`,
`LDM_PROXY_LOG_LEVEL` and `LDM_PROXY_LOG_FORMAT` -- were documented in two
other files and not there. A downstream team wired two of the three into their
CI, in a change whose stated subject was consuming what LDM ships, and missed
the third because no single page listed them.

A hand-maintained table would drift exactly as those three did, so the list is
**derived from the code** and this test fails when the two disagree.

Three read mechanisms, and a naive grep for `os.environ.get("LDM_...` sees
only the first -- it misses precisely the three variables that caused the
issue:

1. a literal: ``os.environ.get("LDM_HOME")``
2. a module constant: ``TUNNEL_ENV_VAR = "LDM_DOCKER_TUNNEL"`` then
   ``os.environ.get(TUNNEL_ENV_VAR)``
3. a shipped Compose template: ``--log.level=${LDM_PROXY_LOG_LEVEL:-ERROR}``,
   read by Docker Compose rather than by Python at all

The AST walk below covers 1 and 2; the resource scan covers 3.

**Variables LDM WRITES are deliberately excluded.** `LDM_HOST_NAME`,
`LDM_PROJECT_ID`, `LDM_BASE_URL` and friends are values LDM injects into
containers, not knobs a user sets, and listing them would invite people to set
things that are overwritten. That is why this derives from *reads* and not
from every `LDM_`-shaped string in the tree.
"""

import ast
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CORE = PROJECT_ROOT / "ldm_core"
REFERENCE = PROJECT_ROOT / "docs" / "reference" / "configuration.md"

_READERS = {("os", "environ", "get"), ("os", "getenv"), ("environ", "get")}

# Populated by LDM itself before invoking Compose (handlers/infra.py). It
# appears in a shipped template but setting it by hand achieves nothing, so it
# is excluded rather than documented as a knob.
INTERNAL_ONLY = frozenset({"LDM_CERTS_DIR"})


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return tuple(reversed(parts))


def _module_constants(tree):
    """Module-level `NAME = "LDM_..."` assignments, for mechanism 2."""
    consts = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        if not value.value.startswith("LDM_"):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                consts[target.id] = value.value
    return consts


def _read_name(call, consts):
    """The `LDM_*` name a single environ-read call resolves to, or None."""
    if not call.args or _dotted(call.func) not in _READERS:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        name = arg.value
    elif isinstance(arg, ast.Name):
        name = consts.get(arg.id)
    else:
        return None
    return name if name and name.startswith("LDM_") else None


def _reads_in_python(found):
    """Mechanisms 1 and 2: a literal, or a module constant."""
    for path in CORE.rglob("*.py"):
        if "/tests/" in path.as_posix():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        consts = _module_constants(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _read_name(node, consts)
                if name:
                    found.setdefault(name, set()).add(path.as_posix())


def _reads_in_resources(found):
    """Mechanism 3: `${LDM_*}` in a shipped template, read by Compose."""
    for res in (CORE / "resources").rglob("*"):
        if not res.is_file():
            continue
        try:
            text = res.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in re.finditer(r"\$\{(LDM_[A-Z0-9_]+)", text):
            found.setdefault(match.group(1), set()).add(res.as_posix())


def env_vars_read():
    """Every `LDM_*` variable LDM reads, mapped to where it reads it."""
    found: dict[str, set[str]] = {}
    _reads_in_python(found)
    _reads_in_resources(found)
    for name in INTERNAL_ONLY:
        found.pop(name, None)
    return found


def documented_vars():
    """Every `LDM_*` named in the reference, from its table or its headings."""
    text = REFERENCE.read_text(encoding="utf-8")
    return set(re.findall(r"`(LDM_[A-Z0-9_]+)`", text))


class TestEveryEnvVarIsInTheReference(unittest.TestCase):
    def test_nothing_the_code_reads_is_missing_from_the_reference(self):
        read = env_vars_read()
        missing = sorted(set(read) - documented_vars())
        self.assertEqual(
            missing,
            [],
            "these are read by ldm_core but absent from "
            "docs/reference/configuration.md, so a user cannot find them "
            "(LDM-#2025):\n  "
            + "\n  ".join(f"{v}  (read in {sorted(read[v])[0]})" for v in missing),
        )

    def test_the_reference_does_not_invent_variables(self):
        """The other direction: a documented variable nothing reads is a lie
        that outlives the feature it described."""
        read = set(env_vars_read()) | INTERNAL_ONLY
        # Variables LDM injects into containers are described in the
        # passthrough section and are not read-knobs; allow any that the code
        # genuinely writes.
        written = set()
        for path in CORE.rglob("*.py"):
            if "/tests/" in path.as_posix():
                continue
            written |= set(
                re.findall(
                    r'(?:env|environ|expansion_env)\[\s*"(LDM_[A-Z0-9_]+)"\s*\]\s*=',
                    path.read_text(encoding="utf-8"),
                )
            )
        invented = sorted(documented_vars() - read - written)
        self.assertEqual(
            invented,
            [],
            "documented but neither read nor written by ldm_core "
            f"(LDM-#2025): {invented}",
        )

    def test_the_derivation_sees_all_three_read_mechanisms(self):
        """Guard the guard.

        A grep for `os.environ.get("LDM_` finds mechanism 1 only, and would
        have missed every variable this issue was raised about. Pin all three
        so a future simplification cannot quietly regress to that.
        """
        read = env_vars_read()
        self.assertIn("LDM_HOME", read, "mechanism 1: a literal")
        self.assertIn("LDM_DOCKER_TUNNEL", read, "mechanism 2: a module constant")
        self.assertIn("LDM_PROXY_LOG_LEVEL", read, "mechanism 3: a Compose template")


if __name__ == "__main__":
    unittest.main()
