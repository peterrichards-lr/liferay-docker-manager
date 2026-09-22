"""Every field the client-extension parser produces must have a consumer.

LDM-#1918: seven working behaviours were dropped in the stack.py -> composer.py
refactor. Four of them left the same fingerprint -- `workspace/metadata.py`
carried on parsing a field into the extension's info while the code that read
it was deleted:

    readinessProbe / livenessProbe   the healthcheck mapper was removed
    cpu / memory                     the resource limits were removed
    oauth_erc                        parsed in four places, read nowhere

None of that was detectable by any test. Each was found by reading the old and
new code side by side, months later, after an external consumer reported that
their extension could not find Liferay.

This test makes the fingerprint mechanical. It does not know what any field
means or which consumer is correct -- only that a field nobody reads is a
consumer that went missing. That is a weaker claim than "the behaviour works",
and deliberately so: it needs no foresight about *which* behaviour will be
dropped next, which is exactly what the tests that existed did not have.

A field that genuinely has no consumer belongs in UNCONSUMED below, with the
reason. Silence is what this exists to prevent.

**What it does not catch, measured rather than assumed.** Deleting the
healthcheck consumer makes this fail on `readinessProbe` and `livenessProbe`.
Deleting the resource-limit consumer does NOT make it fail on `memory`, because
`"memory"` also appears in the infra service definitions (`"memory": "50M"`) and
the loose match finds that instead.

That is the cost of matching loosely, and it is the right trade: a stricter
match invents false positives, and a guard that cries wolf gets deleted -- which
would leave nothing. A field with a distinctive name is protected here; a field
with a common one needs a behavioural test, which is what
`test_refactor_regressions.py` is for.

So this catches *unanticipated* drops of distinctively-named fields. It is one
layer, not the whole defence.
"""

import ast
import unittest
from pathlib import Path

LDM_CORE = Path(__file__).parent.parent
PARSER = LDM_CORE / "workspace" / "metadata.py"

#: Fields with no consumer, deliberately. Each needs a reason, and a reader
#: should be able to disagree with it.
#:
#: `kind` was listed here when this test was written, on the reasoning that
#: it is only read inside the parser. `test_the_unconsumed_list_does_not_rot`
#: rejected that immediately -- `handlers/validation.py:98` reads it. The
#: exemption list is the weakest part of this guard, which is why it is
#: itself guarded.
UNCONSUMED = {
    "oauth_erc": (
        "Parsed for a consumer that does not exist yet -- Liferay generates the "
        "OAuth2 credentials and publishes them to the routes tree, so LDM has "
        "never needed the ERC. Tracked as LDM-#1915; remove it there or wire it "
        "up, but do not let it sit here unexplained."
    ),
}


def _parsed_fields():
    """The keys the parser seeds its extension-info dicts with.

    Read from the source rather than by calling the parser: the two dict
    literals are the declaration of what an extension *has*, and a field added
    there without a consumer is precisely what this guards.
    """
    tree = ast.parse(PARSER.read_text(encoding="utf-8"))
    fields: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [
            k.value
            for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]
        # The info dicts are identifiable by their anchor fields.
        if {"readinessProbe", "livenessProbe", "has_load_balancer"} <= set(keys):
            fields.update(keys)
    return fields


def _consumer_files():
    """Production modules that could legitimately read an extension's info."""
    return [
        p
        for p in LDM_CORE.rglob("*.py")
        if "/tests/" not in p.as_posix()
        and p != PARSER
        and "__pycache__" not in p.as_posix()
    ]


class TestNoFieldIsParsedAndForgotten(unittest.TestCase):
    def test_the_parser_still_declares_its_fields_in_one_place(self):
        """If this fails the extraction below is lying, not the code."""
        fields = _parsed_fields()
        self.assertIn("readinessProbe", fields)
        self.assertIn("memory", fields)
        self.assertGreater(len(fields), 8, f"only found {fields}")

    def test_every_parsed_field_is_read_somewhere(self):
        sources = {p: p.read_text(encoding="utf-8") for p in _consumer_files()}
        orphans: list[str] = []
        for field in sorted(_parsed_fields()):
            if field in UNCONSUMED:
                continue
            # Deliberately loose: `ext.get("ports", [])` and `ext["ports"]` and
            # `ext_info.get("ports")` must all count. A loose match can only
            # cause this test to MISS an orphan, never to invent one -- the
            # safe direction for a guard nobody is watching.
            needle = f'"{field}"'
            if not any(needle in src for src in sources.values()):
                orphans.append(field)

        self.assertEqual(
            [],
            orphans,
            "These fields are parsed from a client extension and read by "
            f"nothing: {orphans}.\n"
            "A consumer was probably deleted -- that is how LDM-#1918's "
            "healthcheck, resource-limit and OAuth-ERC regressions happened.\n"
            "If the field is genuinely unused, add it to UNCONSUMED with a "
            "reason.",
        )

    def test_the_unconsumed_list_does_not_rot(self):
        """An entry that has since gained a consumer should leave the list, or
        it silently exempts a field that is now load-bearing."""
        sources = {p: p.read_text(encoding="utf-8") for p in _consumer_files()}
        stale = [
            f for f in UNCONSUMED if any(f'"{f}"' in src for src in sources.values())
        ]
        self.assertEqual(
            [],
            stale,
            f"These are listed as unconsumed but now have consumers: {stale}. "
            "Remove them from UNCONSUMED so the guard covers them again.",
        )


if __name__ == "__main__":
    unittest.main()
