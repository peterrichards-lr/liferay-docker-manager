"""The LDM-#1745 harness builds a fixture Liferay and LDM both accept.

`scripts/fragment_override_harness.py` needs a live Liferay, so it is not part
of the default gate. Its *fixture builder* needs nothing, and it is the half
that silently rots: a client-extension zip missing its header is ignored by
Liferay without a word, and a `fragment-overrides.json` in the wrong shape is
rejected by a validator these tests can call directly.

So this pins the contracts the harness depends on, against their real
consumers:

* `_sync_client_extensions` (`workspace/hydration.py:17`) moves
  `client-extensions/*.zip` into the project's `osgi/client-extensions/`,
  which is the only wiring the fixture needs.
* `_validate_fragment_overrides` (`runtime/fragments.py`) defines the overrides
  schema, and is asserted against here rather than restated.

The site-initializer shape itself is taken from a real, deployed one --
`ldm-cx-samples/client-extensions/ecopulse-site-initializer`, whose built
artifact was read back out of a running bundle's `tomcat/temp` -- rather than
invented. Guessing that schema is what produced the 400 in LDM-#1729, so these
tests exist mostly to stop it drifting back into a guess.

None of this proves the override works. That needs a Liferay boot and is what
the harness itself does; see `.github/workflows/fragment-override.yml`.
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
    / "fragment_override_harness.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("fragment_override_harness", _HARNESS)
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


class TheSiteInitializerContent(unittest.TestCase):
    """The tree Liferay's Site Initializer reads."""

    def setUp(self):
        self.mod = _load()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self.mod.build_site_initializer(Path(self._tmp.name))
        self.fragment = (
            self.root
            / "fragments"
            / "group"
            / self.mod.COLLECTION_KEY
            / self.mod.FRAGMENT_NAME
        )

    def _json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_collection_declares_the_key_the_page_references(self):
        data = self._json(
            self.root
            / "fragments"
            / "group"
            / self.mod.COLLECTION_KEY
            / "collection.json"
        )

        self.assertEqual(data["externalReferenceCode"], self.mod.COLLECTION_KEY)

    def test_the_fragment_declares_the_key_the_page_references(self):
        """`externalReferenceCode` is what the page's fragment key resolves to.

        Taken from the ecopulse reference, where `eco-hero` and `grid-monitor`
        are the fragments' `externalReferenceCode` values and the page names
        exactly those. LDM-#1729 measured the cost of getting this wrong: the
        harness matched on a key that never existed in the database.
        """
        data = self._json(self.fragment / "fragment.json")

        self.assertEqual(data["externalReferenceCode"], self.mod.FRAGMENT_KEY)
        self.assertEqual(data["htmlPath"], "index.html")

    def test_the_fragment_names_its_configuration_file(self):
        """Without `configurationPath` the configuration is never loaded.

        Measured on DXP 2026.q3.0 (LDM-#1745): `fragmententry.configuration`
        comes back empty, every `fragmententrylink.editablevalues` is written
        as `{}`, and LDM's database fallback -- whose WHERE clause is
        `editablevalues LIKE '%"<field>":%'` -- then matches zero rows. The
        ecopulse reference omits this property, so copying it was not enough.
        """
        data = self._json(self.fragment / "fragment.json")

        self.assertEqual(data["configurationPath"], "configuration.json")
        self.assertTrue((self.fragment / "configuration.json").is_file())

    def test_the_configuration_field_has_a_known_default(self):
        """The assertion is 'it changed FROM this', so the default must be real."""
        fields = self._json(self.fragment / "configuration.json")["fieldSets"][0][
            "fields"
        ]
        field = next(f for f in fields if f["name"] == self.mod.FIELD_NAME)

        self.assertEqual(field["defaultValue"], self.mod.DEFAULT_VALUE)
        self.assertEqual(
            field["type"],
            "text",
            "only text/select/length configuration fields are stored as a "
            "plain string in fragmententrylink.editablevalues; a checkbox "
            "becomes a boolean and LDM's regex fallback would not match it",
        )
        self.assertNotEqual(
            self.mod.DEFAULT_VALUE,
            self.mod.OVERRIDE_VALUE,
            "default and override must differ or the test cannot fail",
        )

    def test_the_field_name_is_distinctive(self):
        """docs/how-to/runtime_overrides.md: the database fallback's WHERE
        clause matches on the key name alone, so a generic key is rewritten in
        every fragment carrying it across the whole instance."""
        self.assertNotIn(
            self.mod.FIELD_NAME.lower(),
            {"url", "endpoint", "title", "name", "value"},
        )

    def test_the_html_renders_the_configuration_value_safely(self):
        """Both halves of this are measured, not stylistic (LDM-#1745).

        A bare `${configuration.<field>}` aborts the whole Site Initializer
        import with `FragmentEntryContentException: FreeMarker syntax is
        invalid` -- the importer renders the HTML once while computing default
        editable values, and `configuration` is not bound yet.

        The `[configuration.<field>]` form the ecopulse reference uses survives
        the import but is never substituted: it reaches the browser as literal
        text. FreeMarker's default operator is the only form that does both.
        """
        html = (self.fragment / "index.html").read_text(encoding="utf-8")

        self.assertIn(f"${{(configuration.{self.mod.FIELD_NAME})!", html)
        self.assertNotIn(f"${{configuration.{self.mod.FIELD_NAME}}}", html)
        self.assertNotIn(f"[configuration.{self.mod.FIELD_NAME}]", html)
        self.assertIn(self.mod.RENDER_MARKER, html)

    def test_the_page_definition_places_the_fragment(self):
        """The whole point of the fixture, and the thing Headless cannot do.

        The content goes in `page-definition.json`, NOT in an inline
        `pageDefinition` inside `page.json`. The inline form is what the
        ecopulse reference carries and it is never read -- measured on DXP
        2026.q3.0 (LDM-#1745): the site, the collection, the fragment entry and
        the layout are all created, `addOrUpdateLayoutsContent` reports `0 ms`,
        `fragmententrylink` has no row, and nothing warns.

        The schema here is the headless `PageElement` DTO, so the types are
        capitalised and the fragment is addressed as
        `{"fragment": {"key": ...}}`.
        """
        page_def = self._json(self.root / "layouts" / "1_home" / "page-definition.json")
        root = page_def["pageElement"]

        self.assertEqual(root["type"], "Root")
        element = root["pageElements"][0]
        self.assertEqual(element["type"], "Fragment")
        self.assertEqual(
            element["definition"]["fragment"]["key"], self.mod.FRAGMENT_KEY
        )

    def test_the_page_metadata_does_not_carry_a_dead_inline_definition(self):
        """Keeping the ignored inline form would read as working coverage."""
        page = self._json(self.root / "layouts" / "1_home" / "page.json")

        self.assertNotIn("pageDefinition", page)

    def test_the_page_has_a_friendly_url_with_a_leading_slash(self):
        """The rendered-page fetch is built from it; `home` would 404."""
        page = self._json(self.root / "layouts" / "1_home" / "page.json")

        self.assertTrue(page["friendlyURL"].startswith("/"))
        self.assertEqual(page["friendlyURL"], self.mod.PAGE_FRIENDLY_URL)


class TheClientExtensionArtifact(unittest.TestCase):
    """The zip LDM hands Liferay, and Liferay's rule for recognising it."""

    def setUp(self):
        self.mod = _load()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        content = self.mod.build_site_initializer(base / "fixture")
        self.zip_path = self.mod.package_site_initializer(content, base / "cx.zip")

    def test_it_declares_itself_a_site_initializer(self):
        """`SiteInitializerClientExtension` tracks bundles on this header alone.

        A zip without it deploys, logs nothing unusual, and initialises no
        site -- which looks identical to a site initializer that ran and found
        nothing to do.
        """
        with zipfile.ZipFile(self.zip_path) as archive:
            props = archive.read("WEB-INF/liferay-plugin-package.properties").decode()

        self.assertIn(
            "Liferay-Client-Extension-Site-Initializer=site-initializer/", props
        )
        self.assertIn("Bundle-SymbolicName=", props)

    def test_it_names_the_site_to_create(self):
        """The portal creates the site from this, keyed on the reference code."""
        with zipfile.ZipFile(self.zip_path) as archive:
            site = json.loads(
                archive.read("site-initializer/site-initializer.json").decode()
            )

        self.assertEqual(site["externalReferenceCode"], self.mod.SITE_ERC)
        self.assertEqual(site["name"], self.mod.SITE_NAME)

    def test_the_content_is_nested_as_a_zip_rooted_at_site_initializer(self):
        """Read back out of a running bundle's tomcat/temp, not guessed."""
        with zipfile.ZipFile(self.zip_path) as archive:
            self.assertIn("site-initializer/site-initializer.zip", archive.namelist())
            inner = archive.read("site-initializer/site-initializer.zip")

        (Path(self._tmp.name) / "inner.zip").write_bytes(inner)
        with zipfile.ZipFile(Path(self._tmp.name) / "inner.zip") as archive:
            names = archive.namelist()

        self.assertTrue(
            all(n.startswith("site-initializer/") for n in names),
            f"content zip is not rooted at site-initializer/: {names}",
        )
        self.assertIn("site-initializer/layouts/1_home/page.json", names)


class TheWorkspace(unittest.TestCase):
    """What `ldm import` consumes."""

    def setUp(self):
        self.mod = _load()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source, self.artifact = self.mod.build_workspace(
            Path(self._tmp.name), "2026.q3.0"
        )

    def test_the_artifact_is_kept_out_of_the_imported_workspace(self):
        """It must NOT reach `osgi/client-extensions/` before the first boot.

        `_sync_client_extensions` (hydration.py:17-21) would put it there, and
        on a first boot the extender dies in
        `SiteInitializerClientExtension.addingBundle` with an NPE from
        `PortalImpl.getCanonicalURL` -- the tracker opens before the built-in
        site initializers have created the Guest site's layouts. The bundle
        logs STARTED, no site is created, and the only sign is a stack trace
        among thousands of startup lines. Measured on DXP 2026.q3.0
        (LDM-#1745); the same artifact deployed into the running portal
        initialises in ~100 ms.
        """
        self.assertEqual(sorted(self.source.glob("client-extensions/*.zip")), [])
        self.assertTrue(self.artifact.is_file())
        self.assertNotIn(self.source, self.artifact.parents)

    def test_the_product_pin_matches_the_tag_that_will_be_booted(self):
        """LDM warns and offers the pinned tag when they disagree, which would
        stall an unattended run at a prompt."""
        props = (self.source / "gradle.properties").read_text(encoding="utf-8")

        self.assertIn("liferay.workspace.product=dxp-2026.q3.0", props)


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
        """The outcome LDM-#1618 measured: a UUID, so the module rung is dead."""
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


class TheWait(unittest.TestCase):
    """LDM-#1728: every poll in this repository must be bounded and must say so."""

    def setUp(self):
        self.mod = _load()

    def test_it_returns_the_first_truthy_value(self):
        answers = iter([None, None, "found"])

        value, _seconds = self.mod.wait_for(
            "a thing", lambda: next(answers), timeout=30, interval=0
        )

        self.assertEqual(value, "found")

    def test_it_gives_up_rather_than_spinning_forever(self):
        value, _seconds = self.mod.wait_for(
            "a thing that never arrives", lambda: None, timeout=0, interval=0
        )

        self.assertIsNone(value)


class TheSitePageUrl(unittest.TestCase):
    """Derived from the portal, never hardcoded: Liferay builds the site's
    friendly URL from its name, and a guess fails quietly on the next line
    that changes the rule."""

    def setUp(self):
        self.mod = _load()

    class _Api:
        def __init__(self, response):
            self.response = response

        def request(self, method, path, payload=None):
            return self.response

    def test_it_reads_the_friendly_url_off_the_initializers_site(self):
        api = self._Api(
            {
                "items": [
                    {"externalReferenceCode": "L_GLOBAL", "friendlyUrlPath": "/global"},
                    {
                        "externalReferenceCode": self.mod.SITE_ERC,
                        "friendlyUrlPath": "/ldm-verify",
                    },
                ]
            }
        )

        self.assertEqual(
            self.mod.resolve_site_page_url(api),
            f"/web/ldm-verify{self.mod.PAGE_FRIENDLY_URL}",
        )

    def test_an_absent_site_is_reported_rather_than_guessed(self):
        self.assertIsNone(self.mod.resolve_site_page_url(self._Api({"items": []})))


class TheDocstringDoesNotOverclaim(unittest.TestCase):
    """It once said the harness created its own page while it could not do so
    (LDM-#1719/#1729). Now it can -- through a site initializer -- and the
    docstring has to be just as explicit about what that costs."""

    def test_it_names_what_the_harness_proves_and_what_it_needs(self):
        import ast

        # The module docstring via the AST, not string arithmetic: the file
        # opens with a shebang, so slicing on the first `"""` lands in the
        # wrong place and the assertion passes or fails for the wrong reason.
        head = ast.get_docstring(ast.parse(_HARNESS.read_text(encoding="utf-8")))
        # assertIsNotNone does not narrow for mypy; self.fail is NoReturn.
        if head is None:
            self.fail("the harness lost its module docstring")

        self.assertIn(
            "rendered page",
            head,
            "the claim the harness exists to make is about the rendered page, "
            "not about a database row",
        )
        self.assertIn(
            "LICENSED",
            head,
            "an unlicensed portal serves the DXP Activation page instead of "
            "the site, so this requirement must be impossible to miss",
        )
        self.assertIn(
            "Never commit an activation key",
            head,
            "the harness takes a licence file as an argument; the warning has "
            "to sit next to it",
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
    """LDM-#1745's own conclusion, and LDM-#1444's principle.

    Fragment override is platform-independent, so running it across three
    operating systems would cost three Liferay boots for one bit of
    information. It also needs a Liferay boot and Site Initializer population
    -- durations the verification suite does not own.
    """

    _ROOT = Path(__file__).resolve().parent.parent.parent

    def test_the_verify_scripts_do_not_invoke_it(self):
        for name in ("verify_e2e_refactor.sh", "verify_e2e_refactor.ps1"):
            body = (self._ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn(
                "fragment_override_harness",
                body,
                f"{name} calls the live harness -- it needs a Liferay boot and "
                "a Site Initializer run, which the suite does not own",
            )

    def test_it_has_a_ci_workflow_instead(self):
        """The deferral has to land somewhere, or it evaporates (LDM-#1745)."""
        workflow = self._ROOT / ".github" / "workflows" / "fragment-override.yml"

        self.assertTrue(workflow.is_file(), f"{workflow} is missing")
        body = workflow.read_text(encoding="utf-8")
        self.assertIn("fragment_override_harness.py", body)
        self.assertIn("LIFERAY_ACTIVATION_KEY_XML", body)


if __name__ == "__main__":
    unittest.main()
