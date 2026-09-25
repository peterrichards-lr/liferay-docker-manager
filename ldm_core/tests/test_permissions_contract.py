"""The architecture skill's permissions table must match the code (LDM-#1946).

Three regressions in the v2.26.0 cycle came from permission and directory
ownership, and each was diagnosed from scratch because none of it was written
down. `.agents/skills/ldm-architecture/SKILL.md` now carries a table of which
directories exist, who creates them and what reclaims them. A table nobody
checks goes stale -- the man page managed four minor releases out of date
(LDM-#1482) -- so these tests read it and require the code to agree.

The direction that catches a NEW regression is code-first: every host
directory the composer bind-mounts must appear in the table. That is exactly
how LDM-#1941 happened -- LDM-#1918 added the `osgi/marketplace` mount without
adding anything that creates the directory, and a comment claiming otherwise
went unchallenged for months.

Observed against a deliberately emptied table before these were written: the
mount test fails, naming the undocumented key.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetNode
from ldm_core.handlers.composer import ComposerService
from ldm_core.tests.test_composer import MockComposerManager

SKILL = (
    Path(__file__).resolve().parents[2]
    / ".agents"
    / "skills"
    / "ldm-architecture"
    / "SKILL.md"
)

SECTION = "## Permissions, Ownership and Timing"
TABLE_HEADING = "### Who creates what"


def _paths():
    base = Path("/tmp/perm-contract")
    return {
        "root": base,
        "deploy": base / "deploy",
        "files": base / "files",
        "data": base / "data",
        "configs": base / "osgi/configs",
        "modules": base / "osgi/modules",
        "marketplace": base / "osgi/marketplace",
        "cx": base / "osgi/client-extensions",
        "scripts": base / "scripts",
        "state": base / "osgi/state",
        "logs": base / "logs",
        "routes": base / "routes",
        "log4j": base / "osgi/log4j",
        "portal_log4j": base / "osgi/portal-log4j",
        "backups": base / "snapshots",
    }


class TestThePermissionsTableMatchesTheCode(unittest.TestCase):
    def _documented_keys(self):
        """The path keys the table actually lists, not merely words in the doc.

        Scoped to the table on purpose. An earlier version of this test
        searched the whole skill for the key in backticks -- and passed with
        the row deleted, because `routes` and friends appear all over the
        routes section. That is the "passes whenever the text survives" shape
        the Behaviour Coverage Gate warns about; it was caught by probing.
        """
        text = self._documented()
        # Scoped to the one table. Parsing every table in the file also picked
        # up `FAT32`, `Label` and `Liferay` as though they were path keys --
        # harmless here, but a stray match elsewhere could mask a genuinely
        # missing row, which is the only thing this test exists to catch.
        start = text.index(TABLE_HEADING)
        rest = text[start + len(TABLE_HEADING) :]
        end = rest.find("\n### ")
        block = rest if end == -1 else rest[:end]

        keys = set()
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            first = stripped.split("|")[1].strip()
            first = first.replace("*", "").replace("`", "").strip()
            if first and " " not in first and first != "path key":
                keys.add(first)
        self.assertTrue(
            keys, f"No rows parsed under '{TABLE_HEADING}' -- the table is gone."
        )
        return keys

    def _documented(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn(
            SECTION,
            text,
            "The permissions section has been removed from the architecture "
            "skill. It exists because this mechanism was undocumented and "
            "cost three separate diagnoses in one release cycle -- restore it "
            "rather than deleting this guard.",
        )
        return text

    def test_every_bind_mounted_directory_is_in_the_table(self):
        """Code first: an undocumented mount is one nobody knows to create."""
        documented = self._documented_keys()

        mgr = MockComposerManager()
        mgr.workspace.scan_client_extensions = MagicMock(return_value=[])
        paths = _paths()
        by_path = {str(v): k for k, v in paths.items() if k != "root"}

        # MockComposerManager, not a bare MagicMock: the builder compares the
        # Liferay tag against a version tuple, and every MagicMock attribute
        # is itself a mock, so the comparison raises before a volume is built.
        node = TargetNode(name="local", host="localhost", is_default=True)
        with patch("ldm_core.config.get_active_target", return_value=node):
            service = ComposerService(mgr)._build_liferay_service(
                paths,
                {"tag": "2026.q1.7-lts", "container_name": "proj"},
                "localhost",
                "proj",
                False,
                None,
            )

        for volume in service.get("volumes", []):
            source = str(volume).split(":", 1)[0]
            key = by_path.get(source)
            if key is None:
                continue  # a named volume or a literal path, not a path key
            self.assertIn(
                key,
                documented,
                f"The composer bind-mounts `{key}`, which appears nowhere in "
                "the architecture skill's permissions table. Add a row saying "
                "what creates it and what reclaims it -- LDM-#1941 was exactly "
                "this: a mount added with nothing creating the directory, and "
                "a comment that claimed otherwise.",
            )

    def test_the_marketplace_row_names_its_creator(self):
        """LDM-#1917: this row used to read `nothing`, and the test that
        replaced this one pinned that gap so the table could not silently
        outlive it.

        The gap is now closed -- `migrate_layout` is wired back into
        `EnvironmentSetupStage` and creates it -- so the assertion inverts:
        the row must name a creator, and must not claim there is none.
        """
        text = self._documented()
        self.assertNotIn(
            "| `marketplace` | nothing |",
            text,
            "the table still says nothing creates `marketplace`; "
            "`migrate_layout` does (LDM-#1917)",
        )
        self.assertIn(
            "`marketplace` | `migrate_layout`",
            text,
            "the `marketplace` row must name `migrate_layout` as its creator, "
            "so that unwiring it again is visible here (LDM-#1917)",
        )

    def test_the_verification_constraint_is_recorded(self):
        """Losing this is how LDM-#599 and LDM-#1941 both shipped."""
        text = self._documented()
        lowered = text.lower()
        self.assertTrue(
            "cannot verify" in lowered and "native linux" in lowered,
            "The skill no longer records that permission behaviour is "
            "unobservable on macOS and Windows. That omission is why LDM-#599 "
            "shipped and why LDM-#1941 reached a tag with four distros green.",
        )


if __name__ == "__main__":
    unittest.main()
