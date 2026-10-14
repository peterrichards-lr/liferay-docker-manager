"""The claim line is for someone holding a package (LDM-#2179).

`describe()` tells a reader what the package they are running records. It was
announced for EVERY resolved tag, so a project created from scratch thirty
seconds earlier was told what "the package records" when it had no package, no
publisher, and nobody who could have made a claim.

Lacking any other reading, it lands as "this Liferay version is unsupported".
One user filed three of seven bug reports against this single line and read it
as an upgrade regression, because it was added in the release they upgraded to.
The maintainer independently asked what it meant while reading his own
terminal.

LDM-#1782's reasoning is untouched: silence must not read as success, and for
a package the no-claim case still gets a line of its own. The defect was the
audience, not the wording.

These assert WHICH projects get the line. A test that only checked the string
appears somewhere would pass against the defect, which is how it shipped.
"""

import unittest
from unittest.mock import MagicMock, patch

from ldm_core import compatibility
from ldm_core.pipelines.run import ConfigResolutionStage

TAG = "2026.q3.7"


def announce(project_meta):
    """Run the claim stage and return the UI.info lines it emitted."""
    manager = MagicMock()
    manager.args.ignore_verified_ceiling = False
    with (
        patch("ldm_core.pipelines.run.UI.info") as info,
        patch("ldm_core.pipelines.run.UI.warning"),
    ):
        ConfigResolutionStage._apply_compatibility_claim(
            manager, {"root": "/tmp/x"}, project_meta, TAG
        )
    return [str(call.args[0]) for call in info.call_args_list]


class TestAProjectWithNoPackageIsNotToldAboutOne(unittest.TestCase):
    def test_a_project_created_from_scratch_gets_no_claim_line(self):
        """The defect itself: `ldm init` / `ldm run` with a tag the user typed."""
        said = announce({"tag": TAG, "host_name": "localhost"})
        self.assertEqual(
            said,
            [],
            "a project with no package was told what 'the package records'",
        )

    def test_the_absence_is_decided_by_provenance_not_by_the_claim(self):
        """An empty claim and no package are different situations. Keying on
        the claim alone would also silence a package that declared nothing,
        which is the case LDM-#1782 exists for."""
        self.assertFalse(compatibility.is_packaged({"tag": TAG}))
        self.assertTrue(compatibility.is_packaged({compatibility.PACKAGE_KEY: True}))


class TestAPackagedProjectStillGetsIt(unittest.TestCase):
    def test_a_package_that_declared_nothing_is_still_announced(self):
        """LDM-#1782: silence read as success. Every package published before
        the claim feature has no key, and that absence IS the default -- so
        this is the case the line was written for."""
        said = announce({"tag": TAG, compatibility.PACKAGE_KEY: True})
        self.assertEqual(len(said), 1, said)
        self.assertIn("no compatibility claim", said[0])

    def test_a_declared_verified_tag_is_announced(self):
        said = announce(
            {
                "tag": TAG,
                compatibility.PACKAGE_KEY: True,
                compatibility.CLAIM_KEY: {"verified": [{"tag": TAG}]},
            }
        )
        self.assertEqual(len(said), 1, said)
        self.assertIn("verified", said[0])

    def test_a_project_imported_before_the_marker_existed_keeps_the_line(self):
        """Backward compatibility: a claim in meta is evidence of a package
        even without the newer provenance key."""
        said = announce(
            {"tag": TAG, compatibility.CLAIM_KEY: {"verified": [{"tag": TAG}]}}
        )
        self.assertEqual(len(said), 1, said)


class TestTheCeilingRefusalIsUnaffected(unittest.TestCase):
    """The refusal is a tested fact and must not be gated by this change. It
    is reachable only through a claim, so it is already package-only -- but a
    silenced refusal would be a far worse defect than a noisy info line."""

    def test_a_refuted_tag_above_the_ceiling_still_refuses(self):
        meta = {
            "tag": TAG,
            compatibility.PACKAGE_KEY: True,
            compatibility.CLAIM_KEY: {
                "verified": [{"tag": "2026.q1.1"}],
                "refuted": [{"tag": "2026.q2.0"}],
            },
        }
        manager = MagicMock()
        manager.args.ignore_verified_ceiling = False
        with (
            patch("ldm_core.pipelines.run.UI.info"),
            patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit(1)) as die,
            self.assertRaises(SystemExit),
        ):
            ConfigResolutionStage._apply_compatibility_claim(
                manager, {"root": "/tmp/x"}, meta, TAG
            )
        self.assertTrue(die.called)


class TestTheImportRecordsTheProvenance(unittest.TestCase):
    """The gating above is only half of it. A neuter probe that removed the
    import-side write left every test green, because they all constructed the
    marker by hand -- so nothing proved the import actually records it. That
    is the same shape as the defect being fixed: a verdict that is never read.
    """

    def _carry(self, manifest):
        from ldm_core.pipelines.import_pipeline import ImportPipelineContext  # noqa

        from ldm_core.pipelines import import_pipeline

        meta: dict = {}
        stage = next(
            c
            for c in vars(import_pipeline).values()
            if isinstance(c, type) and hasattr(c, "_carry_compatibility_claim")
        )
        stage._carry_compatibility_claim(manifest, meta)
        return meta

    def test_a_manifest_marks_the_project_as_packaged(self):
        meta = self._carry({"tag": TAG})
        self.assertTrue(
            meta.get(compatibility.PACKAGE_KEY),
            "the import did not record that a package was involved, so the "
            "claim line will never be shown for it",
        )
        self.assertTrue(compatibility.is_packaged(meta))

    def test_a_manifest_with_a_claim_records_both(self):
        claim = {"verified": [{"tag": TAG}]}
        meta = self._carry({"tag": TAG, compatibility.CLAIM_KEY: claim})
        self.assertTrue(meta.get(compatibility.PACKAGE_KEY))
        self.assertEqual(meta.get(compatibility.CLAIM_KEY), claim)

    def test_no_manifest_records_nothing(self):
        """A workspace link or a non-package import has no publisher."""
        self.assertEqual(self._carry(None), {})
        self.assertEqual(self._carry({}), {})


if __name__ == "__main__":
    unittest.main()
