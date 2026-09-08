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

LDM-#1629 closes the other half of the same hole. That corruption starts with
`{`, so `json.loads` raised and the strict read worked. Content that is not
JSON at all fell through to the legacy flat parser, which skipped every line
without an `=` and returned what it had -- `{}` -- so `strict=True` raised
nothing. `TestContentInNeitherFormat` below is that case; `TestSilentIsNotUnparseable`
holds the line on the state that must stay benign.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

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


class TestContentInNeitherFormat(unittest.TestCase):
    """LDM-#1629: the legacy branch had no way to fail.

    The JSON branch raises the moment `json.loads` objects. The flat branch
    *skipped* every line without an `=` and returned whatever it had, which
    for content in neither format is `{}` -- so `strict=True` did not raise.
    Measured on `master` at f0c85cdf, before this change:

        html 404 page          -> RETURNED {}   <-- no MetaReadError
        garbage, no equals     -> RETURNED {}   <-- no MetaReadError
        valid JSON, not object -> RETURNED {}   <-- no MetaReadError

    It was worse than a wrong return value. The flat branch ends by
    auto-upgrading the file it just parsed to JSON, so reading a 404 page
    *overwrote it with `{}`* -- LDM destroyed the one piece of evidence that
    would have explained the failure.
    """

    # The case that matters: a download that 404s and is saved without a
    # status check. Both formats' detectors miss it, so it used to parse
    # clean and empty.
    NOT_A_MANIFEST: ClassVar[dict[str, str]] = {
        "an html error page": "<html>404</html>",
        "prose with no equals sign": "this is not a manifest at all",
        "valid JSON that is not an object": "[1, 2, 3]",
        "a bare JSON string": '"hello"',
    }

    def setUp(self):
        # read_meta returns early from an in-memory VFS when LDM_DRY_RUN is
        # set, which would make every assertion below vacuous.
        self._saved_dry_run = os.environ.pop("LDM_DRY_RUN", None)

    def tearDown(self):
        if self._saved_dry_run is not None:
            os.environ["LDM_DRY_RUN"] = self._saved_dry_run

    def test_strict_refuses_content_in_neither_format(self):
        for label, content in self.NOT_A_MANIFEST.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as d:
                meta = Path(d) / "meta"
                meta.write_text(content, encoding="utf-8")
                with self.assertRaises(MetaReadError) as ctx:
                    read_meta(meta, strict=True)
                self.assertIn("Could not read metadata", str(ctx.exception))

    def test_non_strict_still_returns_empty_but_says_so(self):
        """The return value is unchanged; the silence is not.

        Every non-strict caller keeps getting `{}`, so no control flow moves.
        What changes is that the broken file is now reported -- exactly as a
        malformed *JSON* meta has always been reported on this same path.
        """
        for label, content in self.NOT_A_MANIFEST.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as d:
                meta = Path(d) / "meta"
                meta.write_text(content, encoding="utf-8")
                warnings: list[str] = []
                with patch("ldm_core.ui.UI.warning", side_effect=warnings.append):
                    self.assertEqual(read_meta(meta), {})
                self.assertTrue(
                    any("Could not read metadata" in w for w in warnings),
                    f"an unreadable meta was read in silence: {warnings}",
                )

    def test_unparseable_content_is_not_overwritten(self):
        """The auto-upgrade must not clobber what LDM could not parse."""
        with tempfile.TemporaryDirectory() as d:
            meta = Path(d) / "meta"
            meta.write_text("<html>404</html>", encoding="utf-8")
            with patch("ldm_core.ui.UI.warning"):
                read_meta(meta)
            self.assertEqual(
                meta.read_text(encoding="utf-8"),
                "<html>404</html>",
                "read_meta destroyed the content it could not parse",
            )

    def test_a_flat_file_with_one_good_line_still_parses(self):
        """The refusal is "yielded nothing", not "contained a bad line".

        The flat parser has always tolerated junk around its keys, and a
        manifest that gave up a key is not the failure this guards against.
        """
        with tempfile.TemporaryDirectory() as d:
            meta = Path(d) / "meta"
            meta.write_text("<html>\ntag=2026.q1.7-lts\n</html>", encoding="utf-8")
            for kwargs in ({}, {"strict": True}):
                meta.write_text("<html>\ntag=2026.q1.7-lts\n</html>", encoding="utf-8")
                self.assertEqual(read_meta(meta, **kwargs).get("tag"), "2026.q1.7-lts")


class TestSilentIsNotUnparseable(unittest.TestCase):
    """A meta file with nothing *to* parse keeps returning `{}` (LDM-#1629).

    This is the line the fix had to draw carefully. "Present but empty" is a
    real state: `snapshot/archive.py` reads `bool(read_meta(root))` as "does
    this project carry metadata", and blank lines and `#` comments are part of
    the flat format's own grammar rather than evidence of corruption. Raising
    here would have turned a benign state into an abort, so it does not.
    """

    SAYS_NOTHING: ClassVar[dict[str, str]] = {
        "a zero-byte file": "",
        "whitespace only": "   \n\t\n",
        "comments and blanks only": "# written by hand\n\n# nothing set yet\n",
    }

    def setUp(self):
        self._saved_dry_run = os.environ.pop("LDM_DRY_RUN", None)

    def tearDown(self):
        if self._saved_dry_run is not None:
            os.environ["LDM_DRY_RUN"] = self._saved_dry_run

    def test_an_empty_meta_file_is_empty_not_an_error(self):
        for label, content in self.SAYS_NOTHING.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as d:
                meta = Path(d) / "meta"
                warnings: list[str] = []
                for kwargs in ({}, {"strict": True}):
                    meta.write_text(content, encoding="utf-8")
                    with patch("ldm_core.ui.UI.warning", side_effect=warnings.append):
                        self.assertEqual(read_meta(meta, **kwargs), {})
                self.assertEqual(warnings, [], f"an empty meta file warned: {warnings}")

    def test_a_comment_only_file_keeps_its_comments(self):
        """It used to be rewritten to `{}`, losing what the user had written."""
        with tempfile.TemporaryDirectory() as d:
            meta = Path(d) / "meta"
            original = "# written by hand\n\n# nothing set yet\n"
            meta.write_text(original, encoding="utf-8")
            read_meta(meta)
            self.assertEqual(meta.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
