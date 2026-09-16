"""The verification bundle must contain what the suite actually needs (LDM-#1718).

`docs/TESTING.md` told a verifier to fetch one file:

    curl -fsSL ".../${LDM_REF}/scripts/verify_e2e_refactor.sh" -o verify_e2e_refactor.sh

The suite also needs `common/`, which carries the DXP activation key and the
Elasticsearch configuration. Without it LDM emits a **warning**, not a failure
(`handlers/config.py`), so the run completed, exited 0 and reported success --
having applied neither. Every assertion genuinely passed; they tested a smaller
system than the report claimed.

That is the family of defect LDM-#1662 fixed, one layer quieter: there a
*failed* run was reported as passed, here a *degraded* one is.

The bundle also fixes a second hazard. `LDM_REF` is derived from whichever
binary is on PATH, so the script could come from a different release than the
binary under test. Published per tag, the pairing is structural.

The tests below are mostly about **refusing to build** rather than about
building. A bundle that quietly omits `common/` would recreate the original
hole while looking like the fix for it, so absence is a hard error and that is
what most of this file pins.
"""

import importlib.util
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

_BUILDER = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "build_verification_bundle.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("build_verification_bundle", _BUILDER)
    assert spec is not None and spec.loader is not None, f"cannot load {_BUILDER}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_repo(root: Path, *, with_common=True, with_ps1=True, with_harness=True):
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "verify_e2e_refactor.sh").write_text("#!/bin/bash\n")
    if with_ps1:
        (root / "scripts" / "verify_e2e_refactor.ps1").write_text("# ps1\n")
    if with_harness:
        (root / "scripts" / "fragment_override_harness.py").write_text("# harness\n")
    if with_common:
        common = root / "common"
        common.mkdir(exist_ok=True)
        (common / "activation-key-dxpdevelopment.xml").write_text("<key/>\n")
        (common / "elasticsearch8.config").write_text("a=b\n")
    return root


class BundleTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.repo = _fake_repo(self.base / "repo")
        self.out = self.base / "out"

    def _build(self, tag="v9.9.9"):
        return self.mod.build(tag, self.out, root=self.repo)

    def _names(self, archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            return set(archive.namelist())


class WhatItContains(BundleTestCase):
    def test_common_is_in_the_bundle(self):
        """The whole point: the folder the single-file curl never fetched."""
        names = self._names(self._build())

        self.assertTrue(
            any(n.startswith("common/") for n in names),
            "common/ is absent -- the bundle reproduces the hole it exists to close",
        )

    def test_the_activation_key_travels_when_there_is_one(self):
        """Only ever true locally -- see the honesty test below."""
        names = self._names(self._build())

        self.assertTrue(any("activation-key" in n for n in names))

    def test_an_absent_activation_key_is_declared_not_glossed_over(self):
        """LDM-#1733: the manifest used to claim the key was in the bundle.

        It cannot be. `.gitignore` excludes `common/activation-key-*.xml` --
        correctly, it is licensed -- so no CI checkout has one to package, and
        every published bundle lacks it. The manifest asserted otherwise while
        listing its own contents, which showed no key, directly underneath.

        This passed before the fix because `collect()` walks the filesystem
        rather than git, and a developer's checkout HAS the key sitting there
        untracked. The test verified the developer's machine, not the artifact
        users download -- the precise shape the Behaviour Coverage Gate exists
        to catch.
        """
        repo = _fake_repo(self.base / "nokey")
        for stray in (repo / "common").glob("activation-key-*"):
            stray.unlink()

        with zipfile.ZipFile(self.mod.build("v9.9.9", self.out, root=repo)) as archive:
            text = archive.read("MANIFEST.txt").decode()

        self.assertIn("NOT INCLUDED", text)
        self.assertIn("activation-key", text)
        self.assertIn(
            "cp ",
            text,
            "saying it is missing is only half of it -- say how to supply one",
        )

    def test_both_script_halves_travel_together(self):
        """One bundle serves either platform, so the pair cannot drift."""
        names = self._names(self._build())

        self.assertIn("verify_e2e_refactor.sh", names)
        self.assertIn("verify_e2e_refactor.ps1", names)

    def test_the_scripts_are_at_the_top_level_beside_common(self):
        """LDM looks for `common/` beside the script, not under `scripts/`."""
        names = self._names(self._build())

        self.assertNotIn("scripts/verify_e2e_refactor.sh", names)
        self.assertIn("verify_e2e_refactor.sh", names)

    def test_the_manifest_names_the_tag(self):
        with zipfile.ZipFile(self._build("v2.22.0-pre.3")) as archive:
            manifest = archive.read("MANIFEST.txt").decode()

        self.assertIn("v2.22.0-pre.3", manifest)
        self.assertIn("common/", manifest)

    def test_checksums_cover_every_member(self):
        archive_path = self._build()
        with zipfile.ZipFile(archive_path) as archive:
            sums = archive.read("SHA256SUMS").decode()
            members = [
                n for n in archive.namelist() if n not in ("MANIFEST.txt", "SHA256SUMS")
            ]

        for member in members:
            self.assertIn(member, sums, f"{member} is unchecksummed")


class TabCompletionPicksTheSuite(BundleTestCase):
    """`verify_<TAB>` should reach the runnable suite (LDM-#1748).

    The bundle is unpacked into a directory the operator then types in, and
    while three files began `verify_` the completion stopped dead at that
    prefix -- one extra listing, every single time, before any useful keystroke.

    The Python harness was renamed out of the namespace rather than the suite
    renamed into a longer one: the harness is opt-in and currently cannot
    complete a run at all (LDM-#1729), so it is the one that should cost the
    keystrokes.

    This asserts the PROPERTY rather than the filenames, so adding
    `verify_something_else.sh` to the bundle fails here and the trade-off gets
    made deliberately instead of eroding.
    """

    def _verify_prefixed(self):
        names = self._names(self._build())
        return sorted(n for n in names if n.startswith("verify_") and "/" not in n)

    def test_completion_reaches_the_suite_name(self):
        names = self._verify_prefixed()
        self.assertTrue(names, "no verify_* entries at all")

        prefix = names[0]
        for name in names[1:]:
            while not name.startswith(prefix):
                prefix = prefix[:-1]

        self.assertEqual(
            prefix,
            "verify_e2e_refactor.",
            f"`verify_<TAB>` stops at {prefix!r}; only the platform halves of "
            "the suite should share that namespace",
        )

    def test_only_the_two_platform_halves_use_the_prefix(self):
        self.assertEqual(
            self._verify_prefixed(),
            ["verify_e2e_refactor.ps1", "verify_e2e_refactor.sh"],
        )

    def test_the_harness_is_still_shipped_under_its_new_name(self):
        """Renaming it must not quietly drop it from the bundle."""
        self.assertIn("fragment_override_harness.py", self._names(self._build()))


class WhatItRefusesToBuild(BundleTestCase):
    """Absence must be loud. A quiet omission is the original defect."""

    def test_a_missing_common_is_a_hard_error(self):
        repo = _fake_repo(self.base / "nocommon", with_common=False)

        with self.assertRaises(FileNotFoundError) as ctx:
            self.mod.build("v9.9.9", self.out, root=repo)

        self.assertIn("common/", str(ctx.exception))

    def test_an_empty_common_is_also_an_error(self):
        """Present-but-empty is the same outcome for the verifier."""
        repo = _fake_repo(self.base / "emptycommon", with_common=False)
        (repo / "common").mkdir()

        with self.assertRaises(FileNotFoundError):
            self.mod.build("v9.9.9", self.out, root=repo)

    def test_a_missing_script_half_is_an_error(self):
        """A bundle serving only one platform is worse than no bundle."""
        repo = _fake_repo(self.base / "nops1", with_ps1=False)

        with self.assertRaises(FileNotFoundError) as ctx:
            self.mod.build("v9.9.9", self.out, root=repo)

        self.assertIn("verify_e2e_refactor.ps1", str(ctx.exception))

    def test_the_optional_harness_may_be_absent(self):
        """Optional means optional -- its absence must not block a release."""
        repo = _fake_repo(self.base / "noharness", with_harness=False)

        names = self._names(self.mod.build("v9.9.9", self.out, root=repo))

        self.assertNotIn("fragment_override_harness.py", names)
        self.assertIn("verify_e2e_refactor.sh", names)


class TheRealRepositoryBuilds(unittest.TestCase):
    """Against the actual tree, not a fixture -- the fixture could drift."""

    def test_it_builds_and_carries_the_real_common(self):
        mod = _load()
        with TemporaryDirectory() as tmp:
            archive_path = mod.build("v0.0.0-test", Path(tmp))
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()

        self.assertTrue(any(n.startswith("common/") for n in names))
        self.assertIn("verify_e2e_refactor.sh", names)
        self.assertIn("verify_e2e_refactor.ps1", names)

    def test_the_manifest_never_claims_a_key_it_does_not_carry(self):
        """Against the real tree, whichever way this machine happens to be set
        up: the manifest and the contents must agree."""
        mod = _load()
        with TemporaryDirectory() as tmp:
            with zipfile.ZipFile(mod.build("v0.0.0-test", Path(tmp))) as archive:
                names = archive.namelist()
                text = archive.read("MANIFEST.txt").decode()

        has_key = any("activation-key" in n for n in names)
        self.assertEqual(
            has_key,
            "NOT INCLUDED: the DXP activation key" not in text,
            "the manifest and the bundle contents disagree about the key",
        )


class TheReleaseWorkflowPublishesIt(unittest.TestCase):
    """A bundle nobody ships is the same as no bundle."""

    def _ci(self):
        root = Path(__file__).resolve().parent.parent.parent
        return (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    def test_the_release_job_builds_it(self):
        self.assertIn("build_verification_bundle.py", self._ci())

    def test_the_release_job_publishes_it(self):
        self.assertIn("verification-bundle.zip", self._ci())

    def test_it_is_built_before_the_checksums_that_cover_it(self):
        """Built after, and checksums.txt would not include it."""
        text = self._ci()
        build_at = text.index("build_verification_bundle.py")
        sums_at = text.index("sha256sum ldm-linux")

        self.assertLess(
            build_at,
            sums_at,
            "the bundle is built after the checksums, so it is unchecksummed",
        )
        # LDM-#1735: this used to assert the exact adjacency
        # `verification-bundle.zip ../compatibility.json`, which broke the
        # moment another asset was added between them -- pinning the argument
        # ORDER rather than the property that matters. What matters is that
        # the bundle is on the checksum line at all.
        sums_line = next(
            line for line in text.splitlines() if "sha256sum ldm-linux" in line
        )
        self.assertIn("verification-bundle.zip", sums_line)


if __name__ == "__main__":
    unittest.main()
