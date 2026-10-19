"""LDM-#2243: every volume LDM creates must carry its ownership labels.

`snapshot/volumes.py` created volumes with a bare `docker volume create <name>`
-- two call sites, no labels. **Docker applies volume labels only at
creation**, so declaring them later in compose does not retrofit an existing
volume (verified: `docker volume inspect` still reports `map[]`). Every volume
the snapshot, restore and import paths produced was therefore invisible to the
label-driven sweep in `ldm prune` for the rest of its life.

Observed on a verification machine:

    $ docker volume inspect ldmnodedel-data
    [ { "CreatedAt": "2026-10-16T18:16:46+01:00", "Labels": null, ... } ]

Created that afternoon by current code, carrying nothing. `ldm prune --all`
could not remove it; only `--legacy-volumes` could, and that path tells the
user the volume "predates ownership labelling" -- false for a volume created
minutes earlier.

The architecture mandate already said to build a volume definition in exactly
one place, "because a second construction site is how the labels get silently
dropped". These calls were that second site. `TheLabelSetHasOneDefinition`
below is what stops a third appearing.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from ldm_core.snapshot.volumes import VolumesSnapshotService
from ldm_core.utils import (
    LDM_VOLUME_LABEL_MANAGED,
    LDM_VOLUME_LABEL_PROJECT,
    LDM_VOLUME_LABEL_ROLE,
    LDM_VOLUME_LABEL_UUID,
    volume_label_flags,
    volume_ownership_labels,
    volume_project_from_name,
)

LDM_CORE = Path(__file__).resolve().parent.parent


class TheOwningProjectIsRecoverableFromAVolumeName(unittest.TestCase):
    def test_the_data_suffix_is_stripped(self):
        self.assertEqual(volume_project_from_name("myproj-data"), "myproj")

    def test_the_state_suffix_is_stripped(self):
        self.assertEqual(volume_project_from_name("myproj-state"), "myproj")

    def test_the_doubled_database_suffix_is_stripped_whole(self):
        # `<project>-db-db-data` also ends in `-data`. Matching the shorter
        # suffix first would attribute it to a project called "myproj-db".
        self.assertEqual(volume_project_from_name("myproj-db-db-data"), "myproj")

    def test_an_unrecognised_name_is_returned_unchanged(self):
        self.assertEqual(volume_project_from_name("something-else"), "something-else")


class EveryVolumeCarriesItsOwnership(unittest.TestCase):
    def test_the_four_labels_are_present(self):
        labels = volume_ownership_labels("myproj-data", "myproj", "uuid-1")
        self.assertEqual(labels[LDM_VOLUME_LABEL_PROJECT], "myproj")
        self.assertEqual(labels[LDM_VOLUME_LABEL_MANAGED], "true")
        self.assertEqual(labels[LDM_VOLUME_LABEL_ROLE], "data")
        self.assertEqual(labels[LDM_VOLUME_LABEL_UUID], "uuid-1")

    def test_the_uuid_label_is_omitted_when_unknown(self):
        labels = volume_ownership_labels("myproj-state", "myproj")
        self.assertNotIn(LDM_VOLUME_LABEL_UUID, labels)
        self.assertEqual(labels[LDM_VOLUME_LABEL_ROLE], "state")

    def test_a_missing_project_never_becomes_the_literal_none(self):
        # Worse than no label: it would satisfy the managed-label filter in
        # `ldm prune` while attributing the volume to a project that does not
        # exist, so the sweep would look for an owner it can never find.
        labels = volume_ownership_labels("myproj-data", None)
        self.assertEqual(labels[LDM_VOLUME_LABEL_PROJECT], "myproj")
        self.assertNotIn("None", labels.values())

    def test_the_flags_are_label_key_value_pairs(self):
        flags = volume_label_flags("myproj-data", "myproj")
        self.assertEqual(flags.count("--label"), 3)
        self.assertIn(f"{LDM_VOLUME_LABEL_MANAGED}=true", flags)


class SyncVolumeCreatesALabelledVolume(unittest.TestCase):
    """Drives the real method and inspects the real command line.

    Asserting that a helper was called would pass against a call that built the
    flags and then dropped them; the labels reaching `docker volume create` is
    the behaviour that makes the volume reclaimable.
    """

    def setUp(self):
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self.tmp_dir_obj.name)
        facade = MagicMock()
        facade.manager.target = None
        facade.manager.args.node = None
        self.service = VolumesSnapshotService(facade)
        self.service.manager.run_command = MagicMock(return_value="")

    def tearDown(self):
        self.tmp_dir_obj.cleanup()

    def _create_command(self):
        for call in self.service.manager.run_command.call_args_list:
            if not call.args:
                continue
            cmd = [str(c) for c in call.args[0]]
            if "volume" in cmd and "create" in cmd:
                return cmd
        return []

    def test_the_create_carries_the_ownership_labels(self):
        self.service._sync_volume(
            self.tmp_dir, "myproj-data", project="myproj", project_uuid="uuid-1"
        )
        cmd = self._create_command()
        self.assertTrue(cmd, "no `docker volume create` was issued at all")
        self.assertIn(f"{LDM_VOLUME_LABEL_PROJECT}=myproj", cmd)
        self.assertIn(f"{LDM_VOLUME_LABEL_MANAGED}=true", cmd)
        self.assertIn(f"{LDM_VOLUME_LABEL_ROLE}=data", cmd)
        self.assertIn(f"{LDM_VOLUME_LABEL_UUID}=uuid-1", cmd)

    def test_the_volume_name_is_still_the_final_argument(self):
        # `--label k=v` before the positional; getting this wrong makes Docker
        # read the name as a label value and the volume is never created.
        self.service._sync_volume(self.tmp_dir, "myproj-state", project="myproj")
        self.assertEqual(self._create_command()[-1], "myproj-state")


class TheLabelSetHasOneDefinition(unittest.TestCase):
    """No third construction site. The mandate in ldm-architecture, enforced.

    Source-scanning rather than behavioural on purpose: the failure this guards
    against is a NEW call site added somewhere no test drives, which is exactly
    how the two in `snapshot/volumes.py` survived.
    """

    def test_no_shipped_volume_create_omits_the_labels(self):
        offenders = []
        for path in LDM_CORE.rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if '"create"' not in text and "'create'" not in text:
                continue
            # The call spans several lines; inspect a window after each
            # `"volume"` token and require the flags helper inside it.
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if '"volume",' not in line:
                    continue
                window = "\n".join(lines[i : i + 6])
                if '"create"' not in window:
                    continue
                if "volume_label_flags" not in window:
                    offenders.append(
                        f"{path.relative_to(LDM_CORE)}:{i + 1} creates a volume "
                        "without volume_label_flags()"
                    )
        self.assertEqual(
            offenders,
            [],
            "A volume created without ownership labels can never be reclaimed "
            "by `ldm prune` -- Docker applies volume labels only at creation. "
            + "; ".join(offenders),
        )
