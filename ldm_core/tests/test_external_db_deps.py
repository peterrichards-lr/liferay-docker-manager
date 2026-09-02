"""`--db external` must not depend on a service that is never defined.

composer._build_db_service returns early for `db_type == "external"`, so no
`<project>-db` service is emitted. The run pipeline nevertheless named it as a
startup dependency for anything that was neither hypersonic nor shared, and ran

    docker compose up -d <project>-db

with check=True against a service the compose file does not define. So
`--db external` could not work in the default isolated mode.

This asserts the two halves agree: the pipeline may only name a dependency that
the composer actually emits. That relationship is the bug -- testing either side
alone would have missed it, which is why the assertion is written across both.
"""

import unittest
from unittest.mock import MagicMock

from ldm_core.handlers.composer import ComposerService
from ldm_core.pipelines.run import project_has_own_db_service


def _emits_db_service(db_type, db_mode):
    """True when _build_db_service would define a <project>-db service."""
    manager = MagicMock()
    manager.args.database_mode = db_mode
    manager.defaults = {}
    svc = ComposerService(manager)
    meta = {"db_type": db_type, "database_mode": db_mode}
    return svc._build_db_service(meta, "proj") is not None


# The real predicate from the run pipeline -- NOT a restatement of it. An
# earlier draft of this file reimplemented the rule locally, which would have
# passed no matter what the pipeline did.
_names_db_dependency = project_has_own_db_service


class TestDependencyMatchesWhatIsEmitted(unittest.TestCase):
    def test_external_names_no_dependency(self):
        self.assertFalse(
            _names_db_dependency("external", use_shared_db=False),
            "external has no <project>-db service, so depending on one makes "
            "`docker compose up -d <project>-db` fail on an undefined service",
        )

    def test_external_really_emits_no_service(self):
        # The other half of the contract: if this ever starts emitting one,
        # the guard above should be revisited rather than silently diverging.
        self.assertFalse(_emits_db_service("external", "isolated"))

    def test_hypersonic_names_no_dependency(self):
        self.assertFalse(_names_db_dependency("hypersonic", use_shared_db=False))

    def test_shared_names_no_dependency(self):
        self.assertFalse(_names_db_dependency("postgresql", use_shared_db=True))

    def test_an_isolated_engine_still_names_its_dependency(self):
        # The fix must not stop a normal isolated project waiting for its DB.
        self.assertTrue(_names_db_dependency("postgresql", use_shared_db=False))
        self.assertTrue(_names_db_dependency("mysql", use_shared_db=False))

    def test_an_isolated_engine_really_emits_a_service(self):
        self.assertTrue(_emits_db_service("postgresql", "isolated"))


if __name__ == "__main__":
    unittest.main()
