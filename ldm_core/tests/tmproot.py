"""A scratch root that is unique to this pytest process (LDM-#1402).

Eight test modules built their ``paths`` fixtures from one constant absolute
path -- ``Path("/tmp/proj")``, ``Path("/tmp/proj/deploy")`` and so on, 303
literals in all. Several tests then let the code under test write into it:
``test_shared_infra_ux_warning`` copies a ``.tmp.config`` into the project's
``osgi/configs/``.

Because that path was fixed rather than owned by the test, two concurrent
``pytest`` runs on one machine shared it, and one deleted or moved a file the
other was about to read::

    FAILED test_composer.py::TestComposerService::test_shared_infra_ux_warning
    E  FileNotFoundError: [Errno 2] No such file or directory:
       '/private/tmp/proj/osgi/configs/...ElasticsearchConfiguration.tmp.config'

Observed on 2026-08-27 with two agents running the suite in different worktrees
of this repository. The test passes in isolation, so the failure reads as a
regression in whatever diff happens to be under test -- which is what makes it
expensive rather than merely untidy.

Why a module-scope root rather than pytest's ``tmp_path``: these are
``unittest.TestCase`` classes, so there is no fixture to inject, and threading
a per-test directory through 303 call sites would mean restructuring eight
suites. A per-process root fixes the actual defect -- collision *between* runs
-- as a mechanical substitution, and keeps the path constant within a run, so
tests that compare two fixtures still agree.

This is deliberately weaker than ``tmp_path``: tests within one process still
share it. Nothing here relies on per-test isolation; a future test that does
should take its own ``tempfile.mkdtemp()``.
"""

import atexit
import os
import shutil
import tempfile

#: Unique per pytest process, so concurrent runs cannot collide.
TEST_TMP_ROOT = tempfile.mkdtemp(prefix=f"ldm-tests-{os.getpid()}-")


@atexit.register
def _cleanup_tmp_root() -> None:
    """Removes the scratch root when the process exits.

    ``ignore_errors`` because this runs at interpreter shutdown, where a
    failure to tidy up must never be able to mask the suite's own result.
    """
    shutil.rmtree(TEST_TMP_ROOT, ignore_errors=True)
