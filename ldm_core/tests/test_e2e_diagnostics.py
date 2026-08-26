import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar


class TestE2EDiagnostics(unittest.TestCase):
    """LDM-#1349: `ldm prune` end-to-end, inside a disposable LDM_HOME.

    This test used to run `ldm prune --seeds --samples --clean-hosts` as a real
    subprocess against the *developer's own* `~/.ldm`. `--seeds` bypasses the
    confirmation prompt entirely (`prune.py`: `elif prune_seeds or (not
    non_interactive and UI.confirm(...))`), so the piped "n" answers below never
    protected anything -- every suite run deleted the real seed cache, up to
    ~1GB per entry, and the sample cache with it. Being a subprocess, no
    `unittest.mock.patch` could reach it, and `HOME` does not help because
    `get_actual_home()` rebuilds `/Users/<user>` on macOS.

    `LDM_HOME` (added with this fix) is what makes it isolatable.
    """

    LDM: ClassVar[list[str]] = [
        sys.executable,
        str(Path(__file__).parent.parent.parent / "liferay_docker.py"),
    ]

    # Orphaned containers, search snapshots, temp files, SSL certs, seeds cache,
    # samples cache, hosts -- one "n" each, plus slack.
    DECLINE_ALL = "n\n" * 8

    def _prune(self, ldm_home, *args):
        env = os.environ.copy()
        env["LDM_IGNORE_DOCKER"] = "true"
        env["LDM_HOME"] = str(ldm_home)
        return subprocess.run(
            [*self.LDM, "prune", *args],
            input=self.DECLINE_ALL,
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            env=env,
            check=False,
        )

    def _seeded_home(self, tmp):
        """Builds an LDM_HOME carrying a seed and a sample, and returns both paths."""
        ldm_home = Path(tmp)
        seed = (
            ldm_home
            / ".ldm"
            / "seeds"
            / "seeded-2026.q1.12-lts-postgresql-shared-v2.tar.gz"
        )
        seed.parent.mkdir(parents=True)
        seed.write_bytes(b"pretend seed archive")

        sample = ldm_home / ".ldm" / "references" / "samples" / "sample.zip"
        sample.parent.mkdir(parents=True)
        sample.write_bytes(b"pretend sample")
        return seed, sample

    def test_interactive_prune_piped_input(self):
        """Piped input navigates the prompts without hanging."""
        with tempfile.TemporaryDirectory() as tmp:
            self._seeded_home(tmp)
            res = self._prune(tmp, "--seeds", "--samples", "--clean-hosts")

        self.assertEqual(
            0, res.returncode, f"Prune command failed. Stderr: {res.stderr}"
        )
        self.assertIn("LDM Global Maintenance", res.stdout)

    def test_prune_seeds_clears_the_cache_under_ldm_home(self):
        """The behaviour the old test destroyed a real cache without ever asserting."""
        with tempfile.TemporaryDirectory() as tmp:
            seed, sample = self._seeded_home(tmp)
            res = self._prune(tmp, "--seeds", "--samples")

            self.assertEqual(0, res.returncode, res.stderr)
            self.assertFalse(seed.exists(), "--seeds did not clear the seed cache")
            self.assertFalse(
                sample.exists(), "--samples did not clear the sample cache"
            )

    def test_prune_leaves_the_caches_alone_without_the_flags(self):
        """Declining the prompts must keep both caches -- the guarantee the old test implied."""
        with tempfile.TemporaryDirectory() as tmp:
            seed, sample = self._seeded_home(tmp)
            res = self._prune(tmp)

            self.assertEqual(0, res.returncode, res.stderr)
            self.assertTrue(seed.exists(), "seed cache removed without --seeds")
            self.assertTrue(sample.exists(), "sample cache removed without --samples")

    def test_prune_never_touches_the_real_ldm_home(self):
        """Guard: the regression this file caused must not be reintroduced.

        Writes a canary into the *real* `~/.ldm/seeds` and asserts a fully
        flagged prune, pointed at a temp LDM_HOME, leaves it untouched.
        """
        real_seeds = Path.home() / ".ldm" / "seeds"
        created_dir = not real_seeds.exists()
        # Must end in .tar.gz: prune only reaches its delete branch when
        # `seeds_cache.glob("*.tar.gz")` is non-empty, so a canary named
        # anything else makes this guard silently vacuous.
        canary = real_seeds / "ldm-1349-canary.tar.gz"
        real_seeds.mkdir(parents=True, exist_ok=True)
        canary.write_text("canary")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self._seeded_home(tmp)
                res = self._prune(tmp, "--seeds", "--samples", "--clean-hosts")

            self.assertEqual(0, res.returncode, res.stderr)
            self.assertTrue(
                canary.exists(),
                "Regression: prune reached the real ~/.ldm despite LDM_HOME",
            )
            self.assertEqual("canary", canary.read_text())
        finally:
            canary.unlink(missing_ok=True)
            if created_dir:
                with contextlib.suppress(OSError):
                    real_seeds.rmdir()


if __name__ == "__main__":
    unittest.main()
