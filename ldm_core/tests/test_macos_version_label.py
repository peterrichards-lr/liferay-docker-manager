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

    def test_darwin_25_is_still_tahoe(self):
        self.assertEqual(_host_os("darwin25"), "macOS 16 Tahoe")

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
