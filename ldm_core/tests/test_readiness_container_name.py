"""Readiness must poll the container compose actually created (LDM-#1564).

For a non-ASCII project, compose emits

    service["container_name"] = sanitize_id(
        meta.get("liferay_container_name") or sanitize_id(original_name)
    )

so Docker holds `test-naming-Zolc`. Readiness resolved a bare
`meta.get("liferay_container_name") or meta.get("container_name")` with no
transcoding, so it polled `test-naming-Żółć` -- a name that does not exist.
Liferay booted fine and LDM watched the wrong container until the 900s
timeout expired: `ldm run` looked like a 15-minute hang on a healthy stack.

Same class as LDM-#1512. LDM-#1524 transcoded the volume paths and missed
this site, which is the only one whose symptom is a slow timeout rather than
an error -- which is why it survived.

These assert the two halves AGREE. Testing either alone would miss it: each
side is individually reasonable, and the bug is the disagreement.
"""

import unittest

from ldm_core.handlers.composer import ComposerService
from ldm_core.utils import liferay_container_of, sanitize_id

NON_ASCII = [
    "test-naming-Żółć",
    "test-naming-Käsespätzle",
    "test-naming-Được",
]


def _compose_container_name(meta):
    """What compose emits as the Liferay service's container_name."""
    original = meta.get("container_name")
    project_name = sanitize_id(original)
    return sanitize_id(meta.get("liferay_container_name") or project_name)


class TestReadinessPollsTheContainerComposeCreated(unittest.TestCase):
    def test_they_agree_for_non_ascii_names(self):
        for raw in NON_ASCII:
            with self.subTest(project=raw):
                meta = {"container_name": raw, "project_name": raw}
                self.assertEqual(
                    liferay_container_of(meta),
                    _compose_container_name(meta),
                    "readiness would poll a container Docker does not have, and "
                    "wait out the full timeout on a healthy stack (LDM-#1564)",
                )

    def test_the_resolved_name_is_actually_transcoded(self):
        # Guards against both halves agreeing on the WRONG (verbatim) name.
        meta = {"container_name": "test-naming-Żółć"}
        self.assertEqual(liferay_container_of(meta), "test-naming-Zolc")

    def test_an_explicit_liferay_container_name_still_wins(self):
        meta = {
            "container_name": "outer",
            "liferay_container_name": "test-naming-Käsespätzle",
        }
        self.assertEqual(liferay_container_of(meta), "test-naming-Kaesespaetzle")
        self.assertEqual(liferay_container_of(meta), _compose_container_name(meta))

    def test_ascii_names_are_untouched(self):
        meta = {"container_name": "plain-project"}
        self.assertEqual(liferay_container_of(meta), "plain-project")

    def test_readiness_uses_the_resolver(self):
        """The wiring, not just the helper -- the helper alone fixes nothing."""
        import inspect

        from ldm_core.runtime.readiness import ReadinessService

        src = inspect.getsource(ReadinessService)
        self.assertNotIn(
            'container_name = project_meta.get("container_name")',
            src,
            "readiness resolved the verbatim name directly; that is the bug",
        )
        self.assertIn("liferay_container_of", src)


class TestComposerAndReadinessCannotDrift(unittest.TestCase):
    """A drift detector, and labelled as one.

    Driving _build_liferay_service end to end needs substantial scaffolding, so
    _compose_container_name above MIRRORS composer's rule rather than executing
    it. That mirror is only trustworthy while composer still applies sanitize_id
    at that point -- so this asserts exactly that, and fails if composer changes
    its rule without this file being updated.

    This is a source assertion, deliberately: it is checking that two FILES
    agree, which is not a behaviour either one has on its own. The behavioural
    half is covered above.
    """

    def test_composer_still_transcodes_the_liferay_container_name(self):
        import inspect

        src = inspect.getsource(ComposerService)
        self.assertIn(
            'service["container_name"] = liferay_container',
            src,
            "composer no longer assigns the container name this way -- "
            "_compose_container_name in this file must be re-checked",
        )
        self.assertIn(
            "liferay_container = sanitize_id(",
            src,
            "composer stopped transcoding the Liferay container name; if that "
            "is deliberate, readiness must stop transcoding too (LDM-#1564)",
        )


if __name__ == "__main__":
    unittest.main()
