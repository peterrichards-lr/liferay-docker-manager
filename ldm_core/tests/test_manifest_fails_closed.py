"""An unreadable package manifest must be rejected, not read as an empty one.

LDM-#1522: `read_meta` catches every exception, warns, and returns {}. That is
correct for a project directory, where a missing meta is a normal state. It is
wrong for a signed package manifest, because every check downstream is a
control on an untrusted archive:

    db_type = manifest.get("db_type")
    if db_type and db_type not in [...]:      # vacuous on {}
        UI.die(...)

    github_repo_manifest = manifest.get("github_repository")
    if not github_repo_manifest:              # fails closed, but by ordering
        UI.die("Security Violation: ...")

The origin check does kill an empty manifest, so the path failed closed -- but
by accident of which check runs first, not because anything distinguished
"this manifest says nothing" from "this manifest could not be read".

The corruption reproduced here is the one seen in a real .ldmp: valid JSON with
extra lines after the closing brace. Every field was present in the file and
all of it was discarded.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ldm_core.utils import MetaReadError, read_meta

CORRUPT = (
    json.dumps(
        {
            "github_repository": "peterrichards-lr/liferay-ai-commerce-accelerator",
            "tag": "2026.q1.7-lts",
            "db_type": "postgresql",
        }
    )
    + "\nstray trailing line\n"
)


class TestStrictReadDistinguishesUnreadableFromAbsent(unittest.TestCase):
    def test_corrupt_manifest_raises_instead_of_degrading(self):
        with tempfile.TemporaryDirectory() as d:
            meta = Path(d) / "meta"
            meta.write_text(CORRUPT, encoding="utf-8")

            # Non-strict keeps the existing, deliberate behaviour.
            self.assertEqual(read_meta(meta), {})

            # Strict refuses to pretend the file said nothing.
            with self.assertRaises(MetaReadError) as ctx:
                read_meta(meta, strict=True)
            self.assertIn("Could not read metadata", str(ctx.exception))

    def test_missing_manifest_raises_under_strict(self):
        with tempfile.TemporaryDirectory() as d:
            absent = Path(d) / "meta"
            self.assertEqual(read_meta(absent), {})
            with self.assertRaises(MetaReadError):
                read_meta(absent, strict=True)

    def test_valid_manifest_is_unaffected(self):
        # The guard must not change the reading of a good manifest.
        with tempfile.TemporaryDirectory() as d:
            meta = Path(d) / "meta"
            meta.write_text(
                json.dumps({"tag": "2026.q1.7-lts", "db_type": "postgresql"}),
                encoding="utf-8",
            )
            for kwargs in ({}, {"strict": True}):
                parsed = read_meta(meta, **kwargs)
                self.assertEqual(parsed.get("tag"), "2026.q1.7-lts")
                self.assertEqual(parsed.get("db_type"), "postgresql")


class TestVacuousDbTypeCheck(unittest.TestCase):
    """The reason the strict read matters, stated as an executable fact."""

    def test_empty_manifest_satisfies_the_db_type_check(self):
        # This is what {} bought an unparseable manifest: the engine check is
        # written `if db_type and ...`, so it cannot reject anything when the
        # key is gone. Asserting it here so the fix has a reason on record.
        manifest: dict = {}
        db_type = manifest.get("db_type")
        rejected = bool(
            db_type and db_type not in ["postgresql", "mysql", "mariadb", "hypersonic"]
        )
        self.assertFalse(
            rejected,
            "an empty manifest passes the db_type check -- which is why the "
            "parse failure must be rejected before this check is reached",
        )


if __name__ == "__main__":
    unittest.main()
