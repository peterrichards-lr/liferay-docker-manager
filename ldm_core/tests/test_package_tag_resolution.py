"""The packaged tag must be settled before anything can ask for it (LDM-#1514).

`ldm quickstart aica` stopped to ask:

    Release type (lts|u|qr|nightly|master|latest), prefix, or specific tag [...]

despite quickstart carrying a fallback for exactly that case -- warn, default,
pause so the user can cancel. The fallback was dead code: the prompt lives in
ConfigResolutionStage._resolve_tag (pipelines/run.py), which runs inside
cmd_import long before quickstart reaches its own `if not tag:`.

_resolve_tag cannot simply be told not to ask -- every ordinary `ldm run`
shares it, and there the question is the right one. So the fix settles the tag
earlier, and these assert that: what args.tag ends up as, and that the fallback
path is reachable at all.
"""

import unittest
from unittest.mock import MagicMock

from ldm_core.constants import FALLBACK_LIFERAY_TAG
from ldm_core.workspace.importer import _apply_package_tag


class _Manager:
    def __init__(self, tag=None, command="quickstart"):
        self.args = MagicMock()
        self.args.tag = tag
        self.args.command = command


class _Host:
    def __init__(self, manager):
        self.manager = manager


def _apply(manifest, tag=None, is_quickstart=True):
    host = _Host(_Manager(tag=tag))
    _apply_package_tag(host, manifest, is_quickstart)
    return host.manager.args.tag


class TestPackagedTagIsUsed(unittest.TestCase):
    def test_manifest_tag_is_adopted(self):
        self.assertEqual(_apply({"tag": "2026.q1.7-lts"}), "2026.q1.7-lts")

    def test_manifest_tag_is_adopted_for_plain_import_too(self):
        # "If LDM is importing / quickstarting or restoring a project then a
        # tag has already been established" -- this is not quickstart-only.
        self.assertEqual(
            _apply({"tag": "2026.q1.7-lts"}, is_quickstart=False), "2026.q1.7-lts"
        )

    def test_an_explicit_tag_wins_over_the_manifest(self):
        # The CLI flag is the user overriding imported metadata deliberately.
        self.assertEqual(
            _apply({"tag": "2026.q1.7-lts"}, tag="2026.q1.12-lts"), "2026.q1.12-lts"
        )


class TestFallbackIsReachable(unittest.TestCase):
    """The whole point: quickstart's fallback was unreachable code."""

    def test_quickstart_falls_back_when_the_manifest_declares_none(self):
        self.assertEqual(_apply({}), FALLBACK_LIFERAY_TAG)

    def test_plain_import_is_left_to_prompt(self):
        # Removing a question from an interactive import is not something to do
        # silently; only quickstart promises one command with no questions.
        self.assertIsNone(_apply({}, is_quickstart=False))

    def test_explicit_tag_still_wins_with_an_untagged_package(self):
        self.assertEqual(_apply({}, tag="2026.q1.12-lts"), "2026.q1.12-lts")


class TestTheConstantIsSharedNotCopied(unittest.TestCase):
    def test_no_module_spells_the_fallback_tag_literally(self):
        # It was written out in four places, which is three to update and two
        # to forget. Not a source-grep of behaviour -- a check that one value
        # has one home.
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for path in root.rglob("*.py"):
            if "tests" in path.parts or path.name == "constants.py":
                continue
            if f'"{FALLBACK_LIFERAY_TAG}"' in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
