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
from unittest.mock import patch

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


class ThePageIsCreated(unittest.TestCase):
    """LDM-#1719: the harness must not need a human to place the fragment.

    It used to walk existing pages and, finding none, ask the operator to add
    the fragment by hand -- while its docstring claimed it built the page
    itself. Both halves are fixed: it creates the page, and when it cannot it
    says so rather than reporting the indistinguishable "no fragment found".
    """

    def setUp(self):
        self.mod = _load()

    class _Api:
        def __init__(self, create_response, existing=None):
            self.create_response = create_response
            self.existing = existing or {}
            self.calls = []

        def request(self, method, path, payload=None):
            self.calls.append((method, path, payload))
            if method == "POST":
                return self.create_response
            return self.existing

    def _sites(self):
        return {"items": [{"externalReferenceCode": "SITE-1"}]}

    def test_it_posts_a_page_carrying_the_fragment_key(self):
        api = self._Api({"friendlyUrlPath": "/ldm-verify"})

        result = self.mod.ensure_page_with_fragment(api, self._sites())

        self.assertTrue(result["ok"])
        method, path, payload = api.calls[0]
        self.assertEqual(method, "POST")
        self.assertIn("site-pages", path)
        self.assertIn(self.mod.FRAGMENT_KEY, str(payload))

    def test_the_page_starts_at_the_default_value(self):
        """The override has to change something, so it must start unchanged."""
        api = self._Api({"friendlyUrlPath": "/ldm-verify"})

        self.mod.ensure_page_with_fragment(api, self._sites())

        _m, _p, payload = api.calls[0]
        self.assertIn(self.mod.DEFAULT_VALUE, str(payload))

    def test_an_existing_page_is_accepted(self):
        """A second run must not fail because the page is already there."""
        api = self._Api(
            {"_error": 409},
            existing={"items": [{"id": "77", "key": self.mod.FRAGMENT_KEY}]},
        )

        result = self.mod.ensure_page_with_fragment(api, self._sites())

        self.assertTrue(result["ok"])
        self.assertIn("already", result["summary"])

    def test_a_failure_reports_the_api_response_verbatim(self):
        """Silent fallback would be indistinguishable from a broken fragment."""
        api = self._Api({"_error": 400, "_reason": "bad schema"}, existing={})

        result = self.mod.ensure_page_with_fragment(api, self._sites())

        self.assertFalse(result["ok"])
        self.assertIn("400", result["summary"])
        self.assertIn("bad schema", result["summary"])

    def test_no_site_is_reported_rather_than_crashing(self):
        api = self._Api({})

        result = self.mod.ensure_page_with_fragment(api, {"items": []})

        self.assertFalse(result["ok"])
        self.assertEqual(api.calls, [])


class TheDocstringDoesNotOverclaim(unittest.TestCase):
    """It said the harness created the page while it did not (LDM-#1719)."""

    def test_it_describes_the_failure_path_too(self):
        import ast

        # The module docstring via the AST, not string arithmetic: the file
        # opens with a shebang, so slicing on the first `"""` lands in the
        # wrong place and the assertion passes or fails for the wrong reason.
        head = ast.get_docstring(ast.parse(_HARNESS.read_text(encoding="utf-8")))
        # assertIsNotNone does not narrow for mypy; self.fail is NoReturn.
        if head is None:
            self.fail("the harness lost its module docstring")

        self.assertIn("through the Headless API", head)
        self.assertIn(
            "If the page cannot be created",
            head,
            "the docstring must state what happens when creation fails, or it "
            "overclaims again",
        )


class TheModuleJar(unittest.TestCase):
    """LDM-#1719: `--require-module` must not need a manual download.

    The jar lives in another repository and is published **per DXP line**. Its
    `bnd.bnd` hardcodes `Import-Package` ranges, so one built for another line
    does not resolve -- and the failure is an unresolved bundle in the OSGi log,
    nowhere near this script. Refusing up front, by name, is the whole point.

    Fetched rather than vendored into LDM's release: a third-party binary pinned
    to one DXP line inside our artifact would go stale independently of us.
    `--module-jar` covers the offline case.

    No network here -- the fetch is mocked. The live behaviour was verified by
    hand against the module repository: `2026.q3.0` fetched
    `com.liferay.custom.fragment.override-3.3.0-dxp-2026.q3.0.jar`, and
    `2026.q1.12-lts` was refused with the available names listed.
    """

    def setUp(self):
        self.mod = _load()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dest = Path(self._tmp.name) / "deploy"
        self.dest.mkdir(parents=True)

    def test_a_local_jar_is_used_without_fetching(self):
        local = Path(self._tmp.name) / "mine.jar"
        local.write_bytes(b"jar")

        jar, note = self.mod.resolve_module_jar("2026.q3.0", local, self.dest)

        self.assertIsNotNone(jar)
        self.assertEqual(jar.parent, self.dest)
        self.assertIn("local jar", note)

    def test_a_missing_local_jar_is_reported_not_fetched(self):
        missing = Path(self._tmp.name) / "nope.jar"

        jar, note = self.mod.resolve_module_jar("2026.q3.0", missing, self.dest)

        self.assertIsNone(jar)
        self.assertIn("does not exist", note)

    def _release(self, *names):
        import io
        import json as _json

        payload = _json.dumps(
            {
                "tag_name": "v9.9.9",
                "assets": [{"name": n, "browser_download_url": "x"} for n in names],
            }
        ).encode()

        class _Res(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Res(payload)

    def test_a_line_with_no_jar_is_refused_by_name(self):
        """The alternative is an unresolved bundle nobody connects to this."""
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value = self._release(
                "com.liferay.custom.fragment.override-3.3.0-dxp-2026.q3.0.jar"
            )
            jar, note = self.mod.resolve_module_jar("2026.q1.12-lts", None, self.dest)

        self.assertIsNone(jar)
        self.assertIn("dxp-2026.q1.12-lts", note)
        self.assertIn("2026.q3.0", note, "the note must list what IS available")

    def test_an_unreachable_repository_is_reported(self):
        with patch("urllib.request.urlopen", side_effect=OSError("no network")):
            jar, note = self.mod.resolve_module_jar("2026.q3.0", None, self.dest)

        self.assertIsNone(jar)
        self.assertIn("could not reach", note)

    def test_the_checksum_asset_is_not_mistaken_for_the_jar(self):
        """Both start with the module prefix; only one is a bundle."""
        with patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = [
                self._release(
                    "com.liferay.custom.fragment.override-3.3.0-dxp-2026.q3.0.jar.sha256",
                    "com.liferay.custom.fragment.override-3.3.0-dxp-2026.q3.0.jar",
                ),
                self._release(),
            ]
            jar, _note = self.mod.resolve_module_jar("2026.q3.0", None, self.dest)

        self.assertIsNotNone(jar)
        self.assertTrue(jar.name.endswith(".jar"))
        self.assertFalse(jar.name.endswith(".sha256"))


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
