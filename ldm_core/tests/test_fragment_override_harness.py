"""The LDM-#1618 harness builds a fixture Liferay and LDM both accept.

`scripts/verify_fragment_override.py` needs a live Liferay, so it is not part
of the default gate. Its *fixture builder* needs nothing, and it is the half
that silently rots: a fragment zip missing its marker is ignored by LDM without
a word, and a `fragment-overrides.json` in the wrong shape is rejected by a
validator these tests can call directly.

So this pins the two contracts the harness depends on, against the real
consumers:

* `_sync_fragments` (`workspace/hydration.py:48`) only treats a zip as a
  fragment bundle when it contains `liferay-deploy-fragments.json`.
* `_validate_fragment_overrides` (`runtime/fragments.py`) defines the overrides
  schema, and is asserted against here rather than restated.

The fragment shape itself is taken from a real collection rather than invented
-- `fragmentEntryKey` in `fragment.json` is what LDM matches on, and
`index.json` carries the configuration the override replaces.
"""

import importlib.util
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

_HARNESS = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "verify_fragment_override.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("verify_fragment_override", _HARNESS)
    # Asserted rather than assumed: `spec_from_file_location` returns None for
    # an unreadable path, and mypy narrows on these.
    assert spec is not None, f"could not load {_HARNESS}"
    assert spec.loader is not None, f"no loader for {_HARNESS}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TheHarnessExists(unittest.TestCase):
    def test_the_script_is_present_and_importable(self):
        self.assertTrue(_HARNESS.is_file(), f"{_HARNESS} is missing")
        self.assertTrue(_load().FRAGMENT_KEY)


class TheFragmentCollection(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.collection = self.mod.build_fragment_collection(Path(self._tmp.name))

    def test_the_collection_is_declared(self):
        data = json.loads((self.collection / "collection.json").read_text())
        self.assertEqual(data["name"], self.mod.COLLECTION_NAME)

    def test_the_fragment_declares_the_key_ldm_matches_on(self):
        fragment = self.collection / self.mod.FRAGMENT_NAME / "fragment.json"
        data = json.loads(fragment.read_text())
        self.assertEqual(
            data["fragmentEntryKey"],
            self.mod.FRAGMENT_KEY,
            "LDM matches overrides against fragmentEntryKey; a mismatch here "
            "makes the harness silently patch nothing",
        )

    def test_the_configuration_field_has_a_known_default(self):
        """The assertion is 'it changed FROM this', so the default must be real."""
        index = self.collection / self.mod.FRAGMENT_NAME / "index.json"
        fields = json.loads(index.read_text())["fieldSets"][0]["fields"]
        field = next(f for f in fields if f["name"] == self.mod.FIELD_NAME)

        self.assertEqual(field["defaultValue"], self.mod.DEFAULT_VALUE)
        self.assertNotEqual(
            self.mod.DEFAULT_VALUE,
            self.mod.OVERRIDE_VALUE,
            "default and override must differ or the test cannot fail",
        )

    def test_the_renderable_files_exist(self):
        for name in ("index.html", "index.css", "index.js"):
            self.assertTrue(
                (self.collection / self.mod.FRAGMENT_NAME / name).is_file(), name
            )


class TheFragmentZip(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        collection = self.mod.build_fragment_collection(base)
        self.zip_path = self.mod.package_fragment_zip(collection, base / "frag.zip")

    def test_it_carries_the_marker_ldm_requires(self):
        """Without this, `_sync_fragments` ignores the zip and says nothing."""
        with zipfile.ZipFile(self.zip_path) as archive:
            names = archive.namelist()

        self.assertIn(
            "liferay-deploy-fragments.json",
            names,
            "workspace/hydration.py:48 keys on this filename; a zip without it "
            "is skipped silently",
        )

    def test_it_contains_the_collection(self):
        with zipfile.ZipFile(self.zip_path) as archive:
            names = archive.namelist()

        self.assertTrue(
            any(n.endswith("fragment.json") for n in names), "no fragment.json"
        )
        self.assertTrue(
            any(n.endswith("collection.json") for n in names), "no collection.json"
        )


class TheOverridesFile(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = self.mod.build_overrides(
            Path(self._tmp.name) / ".ldm" / "fragment-overrides.json"
        )

    def test_it_passes_ldms_own_validator(self):
        """Asserted against the real validator, not a restatement of the schema."""
        from ldm_core.runtime.fragments import FragmentsService

        data = json.loads(self.path.read_text())
        errors = FragmentsService._validate_fragment_overrides(data, self.path)

        self.assertEqual(errors, [], f"the harness writes an invalid file: {errors}")

    def test_it_targets_the_fragment_the_harness_builds(self):
        data = json.loads(self.path.read_text())

        self.assertIn(self.mod.FRAGMENT_KEY, data)
        self.assertEqual(
            data[self.mod.FRAGMENT_KEY][self.mod.FIELD_NAME],
            self.mod.OVERRIDE_VALUE,
        )


class TheMeasurement(unittest.TestCase):
    """`find_fragment_element` is what answers LDM-#1618; it must not lie."""

    def setUp(self):
        self.mod = _load()

    def test_a_numeric_id_is_reported_as_numeric(self):
        tree = {
            "id": "12345",
            "fragmentEntryLinkId": 12345,
            "key": self.mod.FRAGMENT_KEY,
        }

        found = self.mod.find_fragment_element(tree, [])

        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["id_is_numeric"])
        self.assertTrue(found[0]["has_fragmentEntryLinkId"])

    def test_a_non_numeric_id_is_reported_as_such(self):
        """The outcome that would resolve #1618 by removing the rung."""
        tree = {"id": "abc-def", "key": self.mod.FRAGMENT_KEY}

        found = self.mod.find_fragment_element(tree, [])

        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["id_is_numeric"])
        self.assertFalse(found[0]["has_fragmentEntryLinkId"])

    def test_it_finds_elements_nested_in_lists_and_dicts(self):
        tree = {
            "items": [
                {"pageElements": [{"id": "9", "key": self.mod.FRAGMENT_KEY}]},
            ]
        }

        found = self.mod.find_fragment_element(tree, [])

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["id"], "9")

    def test_an_unrelated_tree_yields_nothing(self):
        found = self.mod.find_fragment_element({"id": "1", "key": "somethingElse"}, [])

        self.assertEqual(found, [])


class ItStaysOutOfTheDefaultGate(unittest.TestCase):
    """LDM-#1444's principle: no assertion on a dependency the suite cannot control.

    The harness needs a Liferay boot and content it creates over HTTP. Wiring
    it into the verification scripts would make the whole suite depend on both.
    """

    def test_the_verify_scripts_do_not_invoke_it(self):
        root = Path(__file__).resolve().parent.parent.parent
        for name in ("verify_e2e_refactor.sh", "verify_e2e_refactor.ps1"):
            body = (root / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn(
                "verify_fragment_override",
                body,
                f"{name} calls the live harness -- it needs a Liferay boot and "
                "content it creates itself, which the suite does not own",
            )


if __name__ == "__main__":
    unittest.main()
