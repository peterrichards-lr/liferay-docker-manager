"""LDM-#1944: the per-extension routes directory is named from `projectName`.

Liferay does **not** use the `LCP.json` id. The chain, from the portal source:

* the CX build emits `<name>.client-extension-config.json` carrying BOTH
  `projectId` (equal to the `LCP.json` id) and `projectName` (equal to the
  extension directory)
* `BaseConfigurationFactory` reads `ext.lxc.liferay.com.projectName` and
  publishes it as the label `ext.lxc.liferay.com/projectName`
* `RoutesPortalK8sConfigMapModifier` resolves the path from that label and
  calls `Files.createDirectories` on it

So the tree is `routes/default/<projectName>`.

The two identifiers differ for essentially every extension -- the id drops the
hyphens the directory keeps. Across `ldm-cx-samples`, all 16 differ.

**A wrong name here does not fail loudly**, which is what made it expensive:
`_scaffold_routes_tree` creates whatever the compose declares, so the extension
reads a real, empty, readable directory while Liferay fills a different one
beside it. It was diagnosed as a permissions problem four times before anyone
compared the two strings.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ldm_core.handlers.composer import ComposerService
from ldm_core.workspace.metadata import parse_cx_project_name

# A real extension's shape: the id has no hyphens, the directory does.
DECLARED_ID = "ecopulseheadlessauth"
PROJECT_NAME = "ecopulse-headless-auth"
CONFIG_KEY = (
    "com.liferay.oauth2.provider.configuration."
    "OAuth2ProviderApplicationHeadlessServerConfiguration~" + PROJECT_NAME
)


def _config(**overrides):
    body = {"projectId": DECLARED_ID, "projectName": PROJECT_NAME}
    body.update(overrides)
    return json.dumps({CONFIG_KEY: body})


def _ext_subtree(ext):
    """The host directory the extension's config-tree mount binds."""
    mounts = ComposerService(MagicMock())._extension_routes_mounts(
        ext, ext.get("id"), {"root": Path("/proj")}
    )
    ext_mounts = [m for m in mounts if "dxp-metadata" not in m.split(":")[1]]
    return ext_mounts[0].split(":")[0].rsplit("/", 1)[-1] if ext_mounts else None


class TestTheProjectNameIsParsed(unittest.TestCase):
    def test_project_name_is_read_from_the_config(self):
        self.assertEqual(PROJECT_NAME, parse_cx_project_name(_config()))

    def test_the_prefixed_key_is_honoured(self):
        """Liferay reads `ext.lxc.liferay.com.projectName` first."""
        cfg = _config(**{"ext.lxc.liferay.com.projectName": "prefixed-name"})
        self.assertEqual("prefixed-name", parse_cx_project_name(cfg))

    def test_the_project_id_is_never_used_as_a_fallback(self):
        """The id is the wrong string. Falling back to it would reintroduce
        exactly the bug, silently."""
        cfg = json.dumps({CONFIG_KEY: {"projectId": DECLARED_ID}})
        self.assertNotEqual(DECLARED_ID, parse_cx_project_name(cfg))

    def test_the_configuration_key_suffix_is_the_fallback(self):
        """The key is suffixed with the same name, so it still answers when
        the body declares nothing."""
        self.assertEqual(
            PROJECT_NAME, parse_cx_project_name(json.dumps({CONFIG_KEY: {}}))
        )

    def test_malformed_content_is_not_fatal(self):
        for junk in ("", "not json", "[]", "null"):
            with self.subTest(junk=junk):
                self.assertIsNone(parse_cx_project_name(junk))


class TestTheMountUsesTheProjectName(unittest.TestCase):
    def test_the_subtree_is_the_project_name_not_the_id(self):
        got = _ext_subtree({"id": DECLARED_ID, "project_name": PROJECT_NAME, "env": {}})
        self.assertEqual(
            PROJECT_NAME,
            got,
            "Liferay publishes routes/default/<projectName>; mounting the "
            "LCP.json id binds a directory it never writes to, and the "
            "extension reads it successfully and finds nothing (LDM-#1944)",
        )

    def test_the_id_remains_the_fallback(self):
        """An extension whose config declares no projectName is no worse off
        than before this change."""
        self.assertEqual(DECLARED_ID, _ext_subtree({"id": DECLARED_ID, "env": {}}))

    def test_the_dxp_subtree_is_unaffected(self):
        """Only the per-extension tree is named this way. `dxp` is shared and
        literal."""
        mounts = ComposerService(MagicMock())._extension_routes_mounts(
            {"id": DECLARED_ID, "project_name": PROJECT_NAME, "env": {}},
            DECLARED_ID,
            {"root": Path("/proj")},
        )
        self.assertTrue(
            [m for m in mounts if m.split(":")[0].endswith("/routes/default/dxp")]
        )


class TestTheTwoIdentifiersAreNotInterchangeable(unittest.TestCase):
    """A guard against anyone 'simplifying' these back into one value.

    They are different strings by construction: the id has the hyphens
    stripped. If a future change makes the mount use the id again, the test
    above fails -- this one states why, so the failure is readable.
    """

    def test_stripping_hyphens_turns_the_name_into_the_id(self):
        self.assertEqual(DECLARED_ID, PROJECT_NAME.replace("-", ""))
        self.assertNotEqual(DECLARED_ID, PROJECT_NAME)


if __name__ == "__main__":
    unittest.main()
