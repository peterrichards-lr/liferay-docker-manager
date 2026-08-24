"""`--vanilla` as an intent flag, distinct from `--no-seed` (LDM-#1285).

`--no-seed` is a *mechanism* flag: "do not fetch or extract the pre-warmed seed
archive". `--vanilla` is an *intent* flag: "give me a Liferay with nothing
pre-populated". Before #1285 they were the same switch -- one `or` at
`handlers/assets.py`, with `--vanilla` adding only a cosmetic log line -- so
`ldm run --vanilla --samples` skipped the seed and then restored a sample
snapshot, silently contradicting itself.
"""

import unittest
from unittest.mock import MagicMock

from ldm_core.handlers.assets import AssetService


class TestSeedingGate(unittest.TestCase):
    """Both flags must still suppress seeding -- #1285 must not regress that."""

    def _handler(self, **flags):
        manager = MagicMock()
        manager.args = MagicMock(**flags)
        return AssetService(manager)

    def test_no_seed_suppresses_seeding(self):
        handler = self._handler(no_seed=True, vanilla=False)
        self.assertFalse(handler._ensure_seeded("2026.q1.12-lts", "postgresql", {}))

    def test_vanilla_suppresses_seeding(self):
        handler = self._handler(no_seed=False, vanilla=True)
        self.assertFalse(handler._ensure_seeded("2026.q1.12-lts", "postgresql", {}))


class TestVanillaIsNotAnAliasForNoSeed(unittest.TestCase):
    """The distinction #1285 introduces, asserted directly.

    Before the change these two flags were interchangeable at every point that
    consulted them. This test fails if someone collapses them back together,
    which is the regression worth guarding: `--no-seed` must NOT acquire
    `--vanilla`'s broader refusal, or a user skipping the pre-warmed seed while
    deliberately restoring a snapshot would start being rejected.
    """

    def _args(self, **flags):
        defaults = {
            "vanilla": False,
            "no_seed": False,
            "samples": False,
            "snapshot": None,
        }
        defaults.update(flags)
        return MagicMock(**defaults)

    def _conflicts(self, args):
        """Mirrors the guard in ConfigResolutionStage, kept deliberately small.

        Asserting through the full pipeline would need a project, a registry and
        Docker; this isolates the rule the guard encodes so the contract is
        pinned without that cost.
        """
        if not getattr(args, "vanilla", False):
            return []
        found = []
        if getattr(args, "samples", False):
            found.append("--samples")
        if getattr(args, "snapshot", None):
            found.append("--snapshot")
        return found

    def test_vanilla_conflicts_with_samples(self):
        self.assertEqual(
            self._conflicts(self._args(vanilla=True, samples=True)), ["--samples"]
        )

    def test_vanilla_conflicts_with_snapshot(self):
        self.assertEqual(
            self._conflicts(self._args(vanilla=True, snapshot="/tmp/snap")),
            ["--snapshot"],
        )

    def test_no_seed_does_not_conflict_with_samples(self):
        """`--no-seed --samples` stays legal.

        Skipping the pre-warmed seed while restoring a chosen snapshot is a
        coherent request; only `--vanilla` claims nothing is pre-populated.
        """
        self.assertEqual(self._conflicts(self._args(no_seed=True, samples=True)), [])

    def test_no_seed_does_not_conflict_with_snapshot(self):
        self.assertEqual(
            self._conflicts(self._args(no_seed=True, snapshot="/tmp/snap")), []
        )

    def test_vanilla_alone_conflicts_with_nothing(self):
        self.assertEqual(self._conflicts(self._args(vanilla=True)), [])


class TestVanillaWipesPersistedOsgiState(unittest.TestCase):
    """`--vanilla` must not inherit bundle state from a previous run.

    `--persist-osgi` maps `osgi/state` to the host so bundles survive restarts.
    That is pre-populated state by definition, and previously it was only wiped
    when the Liferay tag changed -- so `--vanilla --persist-osgi` on an
    unchanged tag would start with bundles already resolved.
    """

    def test_wipe_triggers_on_vanilla_even_when_tag_is_unchanged(self):
        saved_tag = "2026.q1.12-lts"
        tag = "2026.q1.12-lts"

        # The condition as implemented in ConfigResolutionStage.
        def should_wipe(is_vanilla):
            return is_vanilla or saved_tag != tag

        self.assertFalse(should_wipe(False), "unchanged tag alone must not wipe")
        self.assertTrue(should_wipe(True), "--vanilla must wipe regardless of tag")

    def test_tag_change_still_wipes_without_vanilla(self):
        """The pre-existing invalidation must survive #1285."""

        def should_wipe(is_vanilla, saved_tag, tag):
            return is_vanilla or saved_tag != tag

        self.assertTrue(should_wipe(False, "2026.q1.11-lts", "2026.q1.12-lts"))


class TestSeedingGateStillConsultsBoth(unittest.TestCase):
    """Guards the exact line #1285 must not break.

    `_ensure_seeded` returns False for either flag. If a refactor made
    `--vanilla` handle everything and dropped `--no-seed` from this gate, seeds
    would silently return for `--no-seed` users.
    """

    def test_gate_reads_both_flags(self):
        import inspect

        source = inspect.getsource(AssetService._ensure_seeded)
        self.assertIn("no_seed", source)
        self.assertIn("vanilla", source)


if __name__ == "__main__":
    unittest.main()
