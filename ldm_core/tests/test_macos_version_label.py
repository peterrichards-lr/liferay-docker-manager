"""A newer macOS must not be recorded as the newest one we know (LDM-#1737).

`sync_compatibility.py` derived the host OS from the Darwin kernel version with
an open-ended clamp:

    if darwin_v >= 25:
        v_num = 16      # Tahoe

so every macOS after Tahoe was labelled Tahoe. That is not merely cosmetic. The
label becomes the **slug**, so a Golden Gate run canonicalises onto
`verify-apple-silicon-macos-16-tahoe-colima-pass.txt` -- the existing Tahoe
report -- and is ingested as a *re-verification of Tahoe*. One row survives
where there should be two, and both are wrong: the new OS looks verified when
it is not, and the old one's evidence has been overwritten by a different
machine's run.

There was a second defect underneath it. The report took its platform from
`$OSTYPE`, which bash bakes in at COMPILE time. Measured on the macOS 27.0
machine that prompted this: `$OSTYPE` said `darwin26.0` while `uname -r` said
`27.0.0` and `sw_vers` said `27.0`. The report was wrong before the clamp ever
saw it. Reports now carry `macos-<productVersion>`, which the parser matches
ahead of any kernel heuristic, so no guessing is involved.

The Darwin path is kept for reports written before that change.
"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from sync_compatibility import get_report_metadata  # noqa: E402

REPORT = """=== LDM BINARY VERIFICATION REPORT ===
Timestamp: Mon 15 Sep 09:00:00 BST 2026
Platform:     {platform}
LDM Version: 2.22.0-pre.6
Docker Provider: Colima v0.10.1
Docker Engine: 29.2.1

ALL E2E VERIFICATIONS PASSED!
"""


def _host_os(platform: str) -> str:
    """Run the real parser over a report naming `platform`."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "verify-report.txt"
        path.write_text(REPORT.format(platform=platform), encoding="utf-8")
        return get_report_metadata(path)["os"]


class TheProductVersionIsUsedWhenPresent(unittest.TestCase):
    def test_golden_gate_is_named(self):
        """The case that prompted this: macOS 27.0 (26A428)."""
        self.assertEqual(_host_os("macos-27.0"), "macOS 27 Golden Gate")

    def test_it_beats_any_kernel_heuristic(self):
        """A report carrying both must trust the product version."""
        self.assertEqual(_host_os("macos-27.0 darwin26.0"), "macOS 27 Golden Gate")


class NewerKernelsAreNotMistakenForTahoe(unittest.TestCase):
    """The defect, stated as the thing that must not happen again."""

    def test_darwin_27_is_not_tahoe(self):
        self.assertNotIn("Tahoe", _host_os("darwin27"))

    def test_no_future_kernel_silently_becomes_tahoe(self):
        for kernel in (26, 27, 28, 30, 40):
            with self.subTest(darwin=kernel):
                self.assertNotIn("Tahoe", _host_os(f"darwin{kernel}"))

    def test_an_unmapped_kernel_is_labelled_distinctly(self):
        """Honest and odd-looking beats confident and wrong."""
        self.assertEqual(_host_os("darwin27"), "macOS (darwin 27)")

    def test_two_unmapped_kernels_do_not_collide_with_each_other(self):
        self.assertNotEqual(_host_os("darwin27"), _host_os("darwin28"))


class TahoesRealProductVersionIsTwentySix(unittest.TestCase):
    """`16` was never a product version (LDM-#1750).

    It is what the old `darwin - 9` arithmetic produced for Tahoe, and it was
    left in the table when `27: "Golden Gate"` was added on the assumption that
    both numbers meant the same thing. They do not. A Tahoe machine reporting
    its real version says `macos-26.6.2`, so it fell through the table unnamed
    and would have been filed as a codename-less "macOS 26" -- breaking the
    convention that a release is named by its project name wherever one exists.

    Surfaced by a real verification report from a second Mac, not by review:
    the number in the filename said Tahoe while the `Platform:` line said 26.

    Both mappings are kept. `16` serves reports that predate the
    product-version change; `26` serves every machine reporting honestly.
    """

    def test_a_real_tahoe_machine_is_named(self):
        self.assertEqual(_host_os("macos-26.6.2"), "macOS 26 Tahoe")

    def test_a_darwin_derived_tahoe_report_is_relabelled(self):
        """The existing reports say `darwin25`, and darwin 25 IS macOS 26.

        Re-labelling them is the honest outcome: 16 was produced by a bug, so
        preserving it would preserve the bug. The reports are untouched -- they
        still record `darwin25` -- and the corrected mapping derives the right
        answer from them on the next sync, which also renames the canonical
        files.
        """
        self.assertEqual(_host_os("darwin25"), "macOS 26 Tahoe")

    def test_the_sequence_is_not_continuous(self):
        """Sequoia is 15 on darwin 24, then Apple jumped to 26 for Tahoe.

        `darwin - 9` assumed a continuous sequence, which is the root of this
        whole family of defects.
        """
        self.assertEqual(_host_os("darwin24"), "macOS 15 Sequoia")
        self.assertEqual(_host_os("darwin25"), "macOS 26 Tahoe")

    def test_a_literal_sixteen_is_still_named(self):
        """Nothing produces 16 any more, but a stray report naming it should
        not become anonymous."""
        self.assertEqual(_host_os("macos-16"), "macOS 16 Tahoe")

    def test_both_tahoe_machines_land_on_the_same_row(self):
        """The point of the re-label: one OS, one row.

        Before it, a darwin-derived report and a product-version report from
        the same OS produced two parallel rows.
        """
        self.assertEqual(_host_os("darwin25"), _host_os("macos-26.6.2"))

    def test_tahoe_and_golden_gate_remain_distinct(self):
        """Adjacent product versions, so a fumbled table would merge them."""
        self.assertNotEqual(_host_os("macos-26.6.2"), _host_os("macos-27.0"))

    def test_the_two_tables_agree(self):
        """`sync_compatibility.py` names the matrix row; `info.py` names the
        report file. They disagreeing is how LDM-#1744 happened."""
        from unittest.mock import patch

        from ldm_core.diagnostics.info import resolve_macos_host_os

        for product in ("26.6.2", "27.0", "15.4"):
            with self.subTest(product=product):
                with patch(
                    "platform.mac_ver", return_value=(product, ("", "", ""), "arm64")
                ):
                    binary_side = resolve_macos_host_os("darwin-x-arm64")
                self.assertEqual(binary_side, _host_os(f"macos-{product}"))


class TheSlugCannotOverwriteAnExistingEnvironment(unittest.TestCase):
    """The reason this is a bug and not a typo.

    The label feeds the report filename, so a wrong label does not merely
    mislabel a row -- it makes a different machine's run overwrite an existing
    verification record.
    """

    def test_a_newer_macos_does_not_canonicalise_onto_tahoe(self):
        tahoe = _host_os("darwin25")
        golden_gate = _host_os("macos-27.0")

        self.assertNotEqual(
            tahoe.lower().replace(" ", "-"),
            golden_gate.lower().replace(" ", "-"),
            "the two OSes produce the same slug, so one overwrites the other's "
            "verification report",
        )

    def test_an_unmapped_kernel_does_not_canonicalise_onto_tahoe(self):
        self.assertNotEqual(_host_os("darwin25"), _host_os("darwin27"))


class ExistingReportsKeepTheirLabels(unittest.TestCase):
    """The matrix already holds rows derived the old way; re-syncing them must
    not silently rewrite history."""

    def test_darwin_25_is_tahoe_at_its_real_version(self):
        """LDM-#1750 corrected this from 16, which no macOS ever was."""
        self.assertEqual(_host_os("darwin25"), "macOS 26 Tahoe")

    def test_darwin_24_is_still_sequoia(self):
        self.assertEqual(_host_os("darwin24"), "macOS 15 Sequoia")

    def test_an_explicit_macos_16_is_still_tahoe(self):
        self.assertEqual(_host_os("macos-16"), "macOS 16 Tahoe")


class TheArchitectureSurvivesTheChange(unittest.TestCase):
    """The Architecture column is derived from the same string."""

    def _arch(self, platform):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "verify-report.txt"
            path.write_text(REPORT.format(platform=platform), encoding="utf-8")
            return get_report_metadata(path)["arch"]

    def test_apple_silicon_is_still_detected_in_the_new_form(self):
        self.assertEqual(self._arch("macos-27.0 arm64"), "Apple Silicon")

    def test_intel_is_still_detected_in_the_new_form(self):
        self.assertEqual(self._arch("macos-27.0 x86_64"), "Apple Intel")

    def test_the_legacy_darwin25_hint_still_works(self):
        self.assertEqual(self._arch("darwin25"), "Apple Silicon")


class TheNamingConventionIsRecorded(unittest.TestCase):
    """Name a macOS release by its code name wherever one exists.

    A row reading "macOS 27" is a version nobody recognises; "Golden Gate" is
    what people say and what the release notes use. The convention lives beside
    the table it governs, because that is the only place someone adding the
    next version will be looking.
    """

    def _src(self):
        return (SCRIPTS / "sync_compatibility.py").read_text(encoding="utf-8")

    def test_the_convention_is_stated_where_versions_are_added(self):
        src = self._src()
        at_convention = src.index("CONVENTION")
        at_table = src.index('11: "Big Sur"')

        self.assertLess(
            at_convention,
            at_table,
            "the convention must precede the table it governs",
        )

    def test_every_named_release_has_a_code_name(self):
        """A bare number in the table would contradict the rule."""
        src = self._src()
        block = src[src.index("real_names = {") : src.index('11: "Big Sur"') + 400]

        for entry in ("Big Sur", "Monterey", "Ventura", "Sonoma", "Sequoia", "Tahoe"):
            self.assertIn(entry, block)


class TheVerifyScriptRecordsTheRealVersion(unittest.TestCase):
    """`$OSTYPE` is compile-time, so the script must ask the OS itself."""

    def _sh(self):
        return (SCRIPTS / "verify_e2e_refactor.sh").read_text(encoding="utf-8")

    def test_it_asks_sw_vers_on_darwin(self):
        self.assertIn("sw_vers -productVersion", self._sh())

    def test_it_emits_the_macos_prefixed_form(self):
        """`macos-<v>` is what the parser matches ahead of the kernel guess."""
        self.assertIn('PLATFORM_INFO="macos-${MACOS_VERSION}', self._sh())

    def test_it_keeps_the_architecture_in_the_string(self):
        """Nearly shipped as a regression, and caught by a test rather than review.

        sync_compatibility.py fills the Architecture column by looking for
        `arm64`/`aarch64` in the platform string, with a hardcoded
        `"darwin25" in p_low` as its only other hint. Replacing the OSTYPE
        token without adding `uname -m` therefore relabels every Apple Silicon
        run as Apple Intel -- a different wrong row, in place of the wrong row
        this issue set out to fix.
        """
        self.assertIn("$(uname -m)", self._sh())

    def test_it_does_not_overwrite_the_linux_branch(self):
        """Linux still reports PRETTY_NAME; the darwin branch is additional."""
        self.assertIn("PRETTY_NAME", self._sh())


if __name__ == "__main__":
    unittest.main()


class TheDoctorSlugNamesTheReleaseCorrectly(unittest.TestCase):
    """`ldm system doctor --slug` NAMES the verification report (LDM-#1744).

    `verify_e2e_refactor.sh` builds the report filename from this slug, so a
    wrong answer here misfiles the compatibility record no matter what the rest
    of the pipeline does. It is a third derivation site, independent of
    `sync_compatibility.py` and of the verify script's own platform line -- and
    it was missed when the other two were fixed.

    Observed on macOS 27.0:

        verify-apple-silicon-macos-18-18-colima-20260916-130714-fail.txt

    Two faults compounded. `platform.release()` is the DARWIN kernel (27.0.0)
    and `v_num - 9` was applied to it unconditionally, giving 18; then
    `names.get(v, str(v))` fell back to the number as its own codename, giving
    "macOS 18 18". The table even carried `17: "17"` -- the same symptom
    patched once before, rather than the cause.

    `platform.mac_ver()` reports the product version from the OS, so the fix
    removes the arithmetic rather than correcting it.
    """

    def _host_os(self, product, release="27.0.0"):
        """Drives the pure helper, not `_get_env_info`.

        `_get_env_info` reaches a real Docker daemon to identify the provider,
        which the suite's LDM-#1409 guard rightly refuses. Naming a release is
        a pure computation and is now separable from that.
        """
        from unittest.mock import patch

        from ldm_core.diagnostics.info import resolve_macos_host_os

        with patch("platform.mac_ver", return_value=(product, ("", "", ""), "arm64")):
            return resolve_macos_host_os(f"darwin-{release}-arm64")

    def test_golden_gate_is_named(self):
        self.assertEqual(self._host_os("27.0"), "macOS 27 Golden Gate")

    def test_the_kernel_version_is_not_subtracted_from(self):
        """27 - 9 = 18 was the arithmetic that produced `macos-18-18`."""
        self.assertNotIn("18", self._host_os("27.0"))

    def test_a_number_is_never_used_as_its_own_codename(self):
        """ "macOS 18 18" is what that fallback produces."""
        host_os = self._host_os("31.0")

        self.assertEqual(host_os, "macOS 31")
        self.assertNotEqual(host_os, "macOS 31 31")

    def test_known_releases_are_unchanged(self):
        self.assertEqual(self._host_os("15.4", "24.4.0"), "macOS 15 Sequoia")
        self.assertEqual(self._host_os("16.1", "25.1.0"), "macOS 16 Tahoe")

    def test_the_kernel_fallback_still_works_without_a_product_version(self):
        """mac_ver() can return an empty string; the offset holds historically."""
        self.assertEqual(self._host_os("", "24.0.0"), "macOS 15 Sequoia")

    def test_the_slug_is_filename_safe_and_distinct(self):
        """It becomes part of the report filename, and must not collide with
        the Tahoe row."""
        golden = self._host_os("27.0").lower().replace(" ", "-")
        tahoe = self._host_os("16.1", "25.1.0").lower().replace(" ", "-")

        self.assertEqual(golden, "macos-27-golden-gate")
        self.assertNotEqual(golden, tahoe)
