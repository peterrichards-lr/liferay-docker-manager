"""`--force` must be off unless the user passes it.

LDM-#1835.

## The defect

`base_sub_parent` declares `-f, --force` once, and `parents=` copies action
*references* -- so all 103 subparsers built from it held the same object. A
`conflict_handler="resolve"` child that redeclares `-f` or `--force` (e.g.
`logs`' `-f/--follow`, `cloud deploy --force`) emptied `option_strings` on the
object every one of them shared.

argparse treats an action with no option strings as a **positional**, and a
positional with `nargs=0` always matches: it consumes nothing and fires. So
`force` was set `True` on every invocation of 54 commands, with no flag passed:

    ldm run demo    -> force=True
    ldm status      -> force=True
    ldm down demo   -> force=True

That silently disabled every guard reading `args.force`:

    diagnostics/upgrade.py:145   "Downgrade aborted: --force is required..."
    handlers/infra.py:229,243    "Aborted due to verification failure..."
    handlers/base.py:2332        critical-change warnings
    snapshot/archive.py:315      operating on a git repo with no LDM meta

It went unnoticed because each of those fires only on an unusual path -- a
downgrade, a failed verification, a git directory without metadata -- so the
bypass was invisible until the moment a guard should have stopped something.

## The second-order defect

On `system upgrade`, with `--force` not a valid option string, argparse fell
back to **prefix matching** and resolved `--force` to the only option beginning
that way: `--force-downgrade`. So `ldm system upgrade --force` performed a
forced *downgrade* -- a more dangerous operation than the one requested.

## What must not regress

The short-flag resolutions are deliberate and must survive: `run -f` is
`--follow`, `logs -n` is `--tail`, `snapshot/restore/quickstart -n` is `--name`,
`db query -f` is `--format`. Those worked only because the shared object had
already been mutated by whichever parser was built first -- build order was
load-bearing. They now work because each of those parsers owns its own copy.
"""

import unittest

from ldm_core import cli


def _parser():
    res = cli.get_parser()
    return (
        res
        if hasattr(res, "_actions")
        else next(x for x in res if hasattr(x, "_actions"))
    )


class ForceIsOffUnlessAsked(unittest.TestCase):
    """The defect itself, on the commands that read `args.force`."""

    def setUp(self):
        self.p = _parser()

    def test_force_is_false_when_not_passed(self):
        for argv in (
            ["run", "demo"],
            ["status"],
            ["stop", "demo"],
            ["down", "demo"],
            ["logs", "demo"],
            ["up", "demo"],
            ["init", "demo"],
            ["deploy", "demo"],
            ["snapshot", "demo"],
            ["doctor"],
            ["system", "upgrade"],
        ):
            with self.subTest(argv=argv):
                ns, _ = self.p.parse_known_args(argv)
                self.assertFalse(
                    getattr(ns, "force", False),
                    f"`ldm {' '.join(argv)}` set force without --force",
                )

    def test_force_is_true_when_passed(self):
        """The guard must keep working -- turning it permanently off would be
        the same defect mirrored."""
        for argv in (
            ["run", "demo", "--force"],
            ["status", "--force"],
            ["down", "demo", "--force"],
            ["system", "upgrade", "--force"],
            ["cloud", "deploy", "--force"],
        ):
            with self.subTest(argv=argv):
                ns, _ = self.p.parse_known_args(argv)
                self.assertTrue(
                    getattr(ns, "force", False), f"--force ignored on {argv}"
                )

    def test_force_on_upgrade_does_not_become_force_downgrade(self):
        """With `--force` unusable, argparse prefix-matched it to
        `--force-downgrade` -- silently performing a downgrade."""
        ns, _ = self.p.parse_known_args(["system", "upgrade", "--force"])

        self.assertTrue(getattr(ns, "force", False))
        self.assertFalse(
            getattr(ns, "force_downgrade", False),
            "--force was resolved to --force-downgrade by prefix matching",
        )


class NoFlagDegradesToAPositional(unittest.TestCase):
    """The class of bug, not just this instance.

    An action that loses its option strings becomes a positional and fires on
    every parse. Asserting `force` alone would let the next shared-parent
    mutation through silently.
    """

    def test_no_store_const_action_has_lost_its_option_strings(self):
        degraded = []

        def walk(parser, path):
            for a in parser._actions:
                if not a.option_strings and a.__class__.__name__ in (
                    "_StoreTrueAction",
                    "_StoreFalseAction",
                    "_StoreConstAction",
                ):
                    degraded.append(f"{path or '<root>'}: {a.dest}")
                if hasattr(a, "choices") and isinstance(a.choices, dict):
                    for name, sub in a.choices.items():
                        walk(sub, f"{path}/{name}")

        walk(_parser(), "")

        self.assertEqual(
            degraded,
            [],
            "these flags degraded to positionals and now fire on every parse:\n  "
            + "\n  ".join(degraded[:20]),
        )


class TheDeliberateShortFlagResolutionsSurvive(unittest.TestCase):
    """These depended on the shared mutation. They must not be collateral."""

    def setUp(self):
        self.p = _parser()

    def test_short_flags_still_mean_what_they_meant(self):
        cases = [
            (["run", "demo", "-f"], "follow", True),
            (["logs", "demo", "-n", "50"], "tail", "50"),
            (["logs", "demo", "-f"], "follow", True),
            (["snapshot", "demo", "-n", "s1"], "name", "s1"),
            (["restore", "demo", "-n", "s1"], "name", "s1"),
            (["quickstart", "aica", "-n", "proj"], "name", "proj"),
            (["db", "query", "demo", "-f", "json"], "format", "json"),
            (["run", "demo", "--target", "aws-1"], "target", "aws-1"),
            (["run", "demo", "--node", "aws-1"], "target", "aws-1"),
            (["run", "demo", "--nightly"], "nightly", True),
        ]
        for argv, attr, expected in cases:
            with self.subTest(argv=argv):
                ns, _ = self.p.parse_known_args(argv)
                self.assertEqual(getattr(ns, attr, None), expected)


if __name__ == "__main__":
    unittest.main()
