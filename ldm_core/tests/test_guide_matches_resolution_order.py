"""`ldm guide` must describe the precedence LDM actually implements.

LDM-#1824.

## What went wrong

`_print_customizing_defaults()` advertised three levels:

    1. Runtime Flags
    2. Workspace Overrides (.ldm/config.json in project root)
    3. Global User Defaults (~/.ldmrc)

Level 2 names a path that does not exist -- there is no `.ldm/config.json`
cascade level anywhere in the codebase. The project `meta` file, which is the
level that actually freezes a project's settings, was missing entirely. And the
block told the user to run `ldm config set database_mode isolated`, which
`handlers/config.py` refuses for any key in `CONVENTION_DEFAULTS`, because
`ldm config set` writes the root of `~/.ldmrc` where the resolver never looks.

`ldm guide` is step 1 of `docs/tutorials/first_5_minutes.md`, so this was the
first thing a new user saw.

## Why a test rather than just a fix

Every other documentation surface has a guard: `check-cli-drift` for the CLI
reference and the man page, `check-docs-review` for markdown. CLI *output* has
none, which is how this drifted for as long as it did. These assertions tie the
printed text to the implementation, so a change to one without the other fails.

They deliberately assert the *relationships* the resolver guarantees -- order,
and which command is recommended -- rather than matching the block verbatim,
which would fail on any wording change and teach people to update the expected
string without reading it.
"""

import io
import re
import unittest
from contextlib import redirect_stdout

from ldm_core.defaults import CONVENTION_DEFAULTS
from ldm_core.workspace.guide import _print_customizing_defaults


def _rendered():
    buf = io.StringIO()
    with redirect_stdout(buf):
        _print_customizing_defaults()
    return buf.getvalue()


class TheGuideDescribesTheRealCascade(unittest.TestCase):
    def setUp(self):
        self.text = _rendered()

    def test_it_names_every_layer_the_resolver_has(self):
        """`DefaultsManager.get_resolved` layers CONVENTION < /etc/ldmrc <
        ~/.ldmrc, and `resolve_infrastructure_mode` puts CLI above project meta
        above those. Five levels; the guide claimed three."""
        for layer in ("~/.ldmrc", "/etc/ldmrc", "meta", "Convention"):
            self.assertIn(layer, self.text, f"the guide never mentions {layer}")

    def test_it_orders_them_highest_first(self):
        """Order is the whole point of the block -- a list naming the right
        layers in the wrong sequence is still wrong."""
        positions = [
            self.text.index(x)
            for x in ("Runtime Flags", "meta", "~/.ldmrc", "/etc/ldmrc")
        ]

        self.assertEqual(
            positions,
            sorted(positions),
            "layers are not printed highest-precedence first",
        )

    def test_it_does_not_invent_a_config_path(self):
        """The original named `.ldm/config.json`, which does not exist."""
        self.assertNotIn(".ldm/config.json", self.text)

    def test_it_recommends_a_command_that_is_not_refused(self):
        """`handlers/config.py` refuses `ldm config set <convention key>`. The
        guide must not recommend it for one -- this is the assertion that would
        have caught the original bug."""
        for key in ("database_mode",):
            self.assertIn(key, CONVENTION_DEFAULTS, "test premise changed")
            self.assertNotRegex(
                self.text,
                rf"ldm config set\s+{re.escape(key)}",
                f"the guide recommends 'ldm config set {key}', which LDM refuses",
            )
        self.assertIn("ldm defaults", self.text, "no working alternative is offered")

    def test_no_convention_key_is_offered_via_config_set(self):
        """Generalises the above: not one CONVENTION_DEFAULTS key may appear
        after `ldm config set` anywhere in the block."""
        offered = {
            key
            for key in CONVENTION_DEFAULTS
            if re.search(rf"ldm config set\s+{re.escape(key)}\b", self.text)
        }

        self.assertEqual(
            offered, set(), f"refused commands recommended: {sorted(offered)}"
        )


if __name__ == "__main__":
    unittest.main()
