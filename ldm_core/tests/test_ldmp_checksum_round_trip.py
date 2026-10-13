"""A package LDM wrote must import back (LDM-#2159).

`ldm package` writes the checksum in `sha256sum` format --
`"<digest>  <filename>"` (`handlers/snapshot.py`). The local import path
compared that whole line against a bare digest, so it could never match: every
locally produced `.ldmp` was refused as "corrupted or tampered with" while
being bit-for-bit correct.

Measured before the fix, on a real 1.07 GB package:

    Expected: 88d498fb...28e8  rtsrc.ldmp
    Actual:   88d498fb...28e8

The remote path (`workspace/importer.py`) has parsed the identical file format
correctly all along with `.split()[0]`; only this branch was missing it.

Asserted at the seam that failed -- `ImportValidationStage.execute` -- rather
than against the message, because the defect was a comparison and not a string.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.pipelines.import_pipeline import (
    ImportPipelineContext,
    ImportValidationStage,
)
from ldm_core.utils import calculate_sha256

PAYLOAD = b"not really a tarball, but it has a stable digest\n"


def fixture_message(fixture):
    """The refusal message the last validate() produced."""
    return fixture.last_message or ""


class _Fixture:
    """A `.ldmp` beside a `.sha256`, as `ldm package` leaves them."""

    def __init__(self, tmp, *, checksum_body=None, payload=PAYLOAD):
        self.root = Path(tmp)
        self.archive = self.root / "demo.ldmp"
        self.archive.write_bytes(payload)
        self.digest = calculate_sha256(self.archive)
        self.last_message = None
        if checksum_body is not None:
            (self.root / "demo.ldmp.sha256").write_text(
                checksum_body.format(digest=self.digest), encoding="utf-8"
            )

    def validate(self, *, verify=True):
        """Run the stage. Returns the refusal message, or None if it accepted.

        `UI.die` is captured rather than left to `sys.exit`, because the
        message is the assertion for the empty-checksum case: without the
        guard the comparison fails anyway and still exits, so a test that only
        asserted SystemExit passed against the unguarded code. Measured -- the
        neuter probe did exactly that.
        """
        manager = MagicMock()
        manager.args.verify = verify
        manager._check_java_version.return_value = True
        context = ImportPipelineContext(
            manager=manager,
            source_path=str(self.archive),
            project_name="demo",
            is_init_from=False,
            no_run=True,
        )
        died = []

        def _die(msg, *a, **k):
            died.append(str(msg))
            # Recorded on the fixture here, not after execute() returns:
            # execute() raises, so anything after the call never runs.
            self.last_message = str(msg)
            raise SystemExit(1)

        with (
            patch("ldm_core.pipelines.import_pipeline.UI.detail"),
            patch("ldm_core.pipelines.import_pipeline.UI.success"),
            patch("ldm_core.pipelines.import_pipeline.UI.warning"),
            patch("ldm_core.pipelines.import_pipeline.UI.die", side_effect=_die),
        ):
            ImportValidationStage().execute(context)
        return died[0] if died else None


class TestThePackageLdmWroteImportsBack(unittest.TestCase):
    def test_the_sha256sum_format_ldm_package_writes_is_accepted(self):
        """The regression itself: digest, two spaces, filename."""
        with TemporaryDirectory() as tmp:
            _Fixture(tmp, checksum_body="{digest}  demo.ldmp\n").validate()

    def test_a_bare_digest_still_verifies(self):
        """What a hand-written checksum usually looks like. Taking the first
        field has to accept both, or this fix trades one refusal for another."""
        with TemporaryDirectory() as tmp:
            _Fixture(tmp, checksum_body="{digest}\n").validate()

    def test_a_single_space_separator_is_accepted(self):
        """BSD `shasum` and GNU `sha256sum -b` differ in the separator."""
        with TemporaryDirectory() as tmp:
            _Fixture(tmp, checksum_body="{digest} *demo.ldmp\n").validate()


class TestItStillRefusesWhatItShould(unittest.TestCase):
    def test_a_genuinely_wrong_digest_is_still_refused(self):
        """The check must still do its job: this is a security control."""
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp, checksum_body="0" * 64 + "  demo.ldmp\n")
            with self.assertRaises(SystemExit):
                fixture.validate()

    def test_a_tampered_archive_is_still_refused(self):
        """Right filename, right shape, content changed after the checksum."""
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp, checksum_body="{digest}  demo.ldmp\n")
            fixture.archive.write_bytes(PAYLOAD + b"tampered")
            with self.assertRaises(SystemExit):
                fixture.validate()

    def test_an_empty_checksum_file_is_named_as_such(self):
        """Asserted on the MESSAGE, not on the refusal.

        Without the guard the comparison fails anyway and still exits, so
        asserting SystemExit alone passes against unguarded code -- which the
        neuter probe demonstrated. The user-visible difference is being told
        the checksum file is empty rather than that their archive is corrupt.
        """
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp, checksum_body="   \n")
            with self.assertRaises(SystemExit):
                fixture.validate()

        self.assertIn("empty", fixture_message(fixture))

    def test_no_checksum_file_at_all_proceeds_with_a_warning(self):
        """Unchanged behaviour: absence is not a failure, it is unverified."""
        with TemporaryDirectory() as tmp:
            _Fixture(tmp, checksum_body=None).validate()

    def test_no_verify_skips_the_check_entirely(self):
        with TemporaryDirectory() as tmp:
            fixture = _Fixture(tmp, checksum_body="0" * 64 + "  demo.ldmp\n")
            fixture.validate(verify=False)


class TestBothHalvesRunTheRoundTrip(unittest.TestCase):
    """LDM-#2131, and a source-text parity check deliberately (LDM-#1982).

    The round trip needs Docker, an `ldm` binary and minutes of runtime, so
    the behaviour cannot be asserted here. What this catches is the failure
    this repo has actually had: a check living in one half and not the other.

    Both blocks were separately EXECUTED against a stub `ldm` and a fake
    portal when they were written -- success, a failed pack, a missing
    `.sha256`, a refused import and a failed boot -- because a name in a file
    is not a call site that runs (LDM-#2132).
    """

    SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"

    def _halves(self):
        for name in ("verify_e2e_refactor.sh", "verify_e2e_refactor.ps1"):
            yield name, (self.SCRIPTS_DIR / name).read_text(encoding="utf-8")

    def test_both_halves_pack_import_and_boot(self):
        for name, text in self._halves():
            with self.subTest(script=name):
                self.assertIn("-y package", text, f"{name} never packs a project")
                self.assertIn(
                    "--no-run",
                    text,
                    f"{name} must import without booting: the package carries "
                    f"the source project's port and that project is still "
                    f"running on it",
                )

    def test_both_halves_pass_an_explicit_repo(self):
        """`ldm package` builds the snapshot and then REFUSES without --repo
        when the workspace has no git origin, which is the suite's case.

        The sibling assertion -- that `--use-latest` is never passed, since it
        resolves a tag from GitHub releases and would make this a network
        test -- is deliberately NOT made here. A bare substring cannot tell a
        flag on a command line from the comment explaining why it is absent,
        and this test's own first draft failed on exactly that.
        """
        for name, text in self._halves():
            with self.subTest(script=name):
                self.assertIn("--repo ldm-e2e/roundtrip", text)

    def test_both_halves_assert_it_serves(self):
        """Booting is not serving. The assertion is an HTTP answer from the
        portal, independent of what `ldm run` believes."""
        for name, text in self._halves():
            with self.subTest(script=name):
                self.assertIn("/c/portal/layout", text)

    def test_both_halves_remove_the_package_they_create(self):
        """~1 GB per run, on six environments."""
        for name, text in self._halves():
            with self.subTest(script=name):
                self.assertIn("roundtrip-pkg", text)


if __name__ == "__main__":
    unittest.main()
