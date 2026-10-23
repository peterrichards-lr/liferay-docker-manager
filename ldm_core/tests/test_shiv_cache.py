"""Old shiv extractions are pruned on the first run of a new version (LDM-#2296).

Every LDM build unpacks into its own `~/.shiv/ldm_<build id>/` and nothing
removed the old ones: 152 extractions, 12 GB, measured on one machine.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core import shiv_cache


def _id(c: str) -> str:
    return c * 64


class _ShivRoot(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name) / ".shiv"
        self.current = self.root / f"ldm_{_id('a')}"
        self.module = self.current / "site-packages" / "ldm_core" / "shiv_cache.py"
        self.module.parent.mkdir(parents=True)
        self.module.write_text("")
        for c in "bc":
            old = self.root / f"ldm_{_id(c)}" / "site-packages"
            old.mkdir(parents=True)
            (old / "blob").write_bytes(b"x" * 1024)
            (self.root / f".ldm_{_id(c)}_lock").write_text("")
        (self.root / f".ldm_{_id('a')}_lock").write_text("")
        # Another shiv application sharing the root: never ours to remove.
        (self.root / f"otherapp_{_id('d')}").mkdir()
        (self.root / "ldm_not-a-build-id").mkdir()

    def tearDown(self):
        self._td.cleanup()


class StaleExtractionsArePruned(_ShivRoot):
    def test_only_other_ldm_extractions_and_their_locks_go(self):
        removed, freed = shiv_cache.prune_stale_extractions(str(self.module))
        self.assertEqual(removed, 2)
        self.assertGreaterEqual(freed, 2048)
        self.assertEqual(
            sorted(p.name for p in self.root.iterdir()),
            sorted(
                [
                    f"ldm_{_id('a')}",
                    f".ldm_{_id('a')}_lock",
                    f"otherapp_{_id('d')}",
                    "ldm_not-a-build-id",
                ]
            ),
        )

    def test_the_running_extraction_is_never_removed(self):
        shiv_cache.prune_stale_extractions(str(self.module))
        self.assertTrue(self.module.exists())

    def test_a_build_that_is_not_a_shiv_extraction_does_nothing(self):
        outside = Path(self._td.name) / "src" / "ldm_core" / "shiv_cache.py"
        self.assertEqual(shiv_cache.prune_stale_extractions(str(outside)), (0, 0))
        self.assertTrue((self.root / f"ldm_{_id('b')}").exists())


class TheUpgradeBannerPrunes(unittest.TestCase):
    """The call site, not only the helper: a new version's first run prunes."""

    def _banner(self, last_version):
        from ldm_core import cli
        from ldm_core.constants import VERSION

        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("sys.argv", ["ldm"]),
            patch("sys.stdout.isatty", return_value=True),
            patch(
                "ldm_core.utils.load_global_config_safe",
                return_value={"last_run_version": last_version or VERSION},
            ),
            patch("ldm_core.utils.save_global_config_safe"),
            patch(
                "ldm_core.shiv_cache.prune_stale_extractions", return_value=(0, 0)
            ) as prune,
        ):
            cli.check_and_display_upgrade_banner()
        return prune

    def test_a_new_version_prunes(self):
        self.assertEqual(self._banner("0.0.1").call_count, 1)

    def test_the_same_version_does_not(self):
        self.assertEqual(self._banner(None).call_count, 0)


if __name__ == "__main__":
    unittest.main()
