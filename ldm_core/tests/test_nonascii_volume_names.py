"""Docker identifiers must be transcoded, not taken verbatim (LDM-#1512).

A project named `Saarbrücken` seeded, warned twice, and then timed out:

    ⚠️  Failed to sync volume Saarbrücken-data: Command execution returned error status.
    ⚠️  Failed to sync volume Saarbrücken-state: ...
    ✅  Project bootstrapped from seed (including OSGi state).
    ❌  Timed out waiting for Liferay to become healthy.

LDM-#1307/#1308 keep metadata VERBATIM and transcode only for Docker, so:

    meta['container_name'] = 'Saarbrücken'
    docker volume ls        Saarbruecken-data, Saarbruecken-state

Those are different volumes. Using the metadata value as a Docker identifier
created an empty one, the copy into it failed, and the seed never reached the
volume Liferay mounts -- so the warning was the cause and the timeout was the
symptom.

`snapshot/archive.py:427` already did this correctly; `snapshot/volumes.py` and
`workspace/utils.py` did not.
"""

import re
import unittest
from pathlib import Path

from ldm_core.utils import sanitize_id

_ROOT = Path(__file__).resolve().parent.parent

# Files that turn a metadata name into something the Docker daemon must resolve.
DOCKER_IDENTIFIER_SITES = [
    "snapshot/volumes.py",
    "workspace/utils.py",
]


class TestTranscodingMatchesDocker(unittest.TestCase):
    def test_umlaut_expands_the_german_way(self):
        """Docker holds Saarbruecken; NFKD alone would give Saarbrucken."""
        self.assertEqual("Saarbruecken", sanitize_id("Saarbrücken"))

    def test_volume_suffix_matches_what_compose_creates(self):
        self.assertEqual("Saarbruecken-data", f"{sanitize_id('Saarbrücken')}-data")


class TestMetadataNamesAreSanitizedBeforeUse(unittest.TestCase):
    """A raw meta name must never be handed to the daemon."""

    def test_no_unsanitized_container_name_reaches_docker(self):
        offenders = []
        for rel in DOCKER_IDENTIFIER_SITES:
            src = (_ROOT / rel).read_text(encoding="utf-8")
            for match in re.finditer(
                r'^\s*c_name = (?!sanitize_id)meta\.get\("container_name"\)',
                src,
                re.MULTILINE,
            ):
                line = src[: match.start()].count("\n") + 1
                offenders.append(f"{rel}:{line}")
        self.assertEqual(
            [],
            offenders,
            "metadata name used as a Docker identifier without sanitize_id -- "
            f"non-ASCII projects address volumes that do not exist: {offenders}",
        )

    def test_both_volume_sites_are_covered(self):
        src = (_ROOT / "snapshot/volumes.py").read_text(encoding="utf-8")
        self.assertEqual(
            2,
            src.count('sanitize_id(meta.get("container_name")'),
            "both the hydrate and dehydrate paths must sanitize",
        )


if __name__ == "__main__":
    unittest.main()
