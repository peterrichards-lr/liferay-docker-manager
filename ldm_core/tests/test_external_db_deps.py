"""`--db external` must not depend on a service that is never defined.

composer._build_db_service emits the per-project `<project>-db` in exactly one
database mode. The run pipeline nevertheless named it as a startup dependency
for anything that was neither hypersonic nor shared, and ran

    docker compose up -d <project>-db

with check=True against a service the compose file does not define. So
`--db external` could not work in the default isolated mode (LDM-#1554).

This asserts the two halves agree: the pipeline may only name a dependency that
the composer actually emits. That relationship is the bug -- testing either side
alone would have missed it, which is why the assertion is written across both.

LDM-#1511 moved `external` and `embedded` onto the mode axis, so the predicate
now takes one value instead of two. The cases below are unchanged in substance:
each names the (engine, mode) pair a user can actually express, and asserts the
pipeline and the composer reach the same answer for it.
"""

import unittest
from unittest.mock import MagicMock

from ldm_core.handlers.composer import ComposerService
from ldm_core.pipelines.run import project_has_own_db_service
from ldm_core.utils import normalize_database_selection


def _emits_db_service(db_type, db_mode):
    """True when _build_db_service would define a <project>-db service."""
    manager = MagicMock()
    manager.args.database_mode = db_mode
    manager.defaults = {}
    svc = ComposerService(manager)
    meta = {"db_type": db_type, "database_mode": db_mode}
    if db_type == "external":
        # A legacy external project's engine lives in its JDBC URL; without one
        # there is nothing for the composer to read forward.
        meta["jdbc_url"] = "jdbc:postgresql://db.example.com:5432/lportal"
    return svc._build_db_service(meta, "proj") is not None


# The real predicates -- NOT restatements of them. An earlier draft of this
# file reimplemented the rule locally, which would have passed no matter what
# the pipeline did.
_names_db_dependency = project_has_own_db_service


def _resolved_mode(db_type, db_mode):
    """The mode LDM actually runs with, through the real resolver."""
    return normalize_database_selection(
        db_type, db_mode, "jdbc:postgresql://db.example.com:5432/lportal"
    )[1]


class TestDependencyMatchesWhatIsEmitted(unittest.TestCase):
    """For every expressible pairing, both halves must agree."""

    CASES = (
        # (db_type, db_mode, expects a <project>-db service)
        ("postgresql", "isolated", True),
        ("mysql", "isolated", True),
        ("postgresql", "shared", False),
        ("postgresql", "external", False),
        ("hypersonic", "embedded", False),
        # The pre-#1511 spelling, still supported.
        ("external", None, False),
    )

    def test_both_halves_agree_for_every_pairing(self):
        for db_type, db_mode, expected in self.CASES:
            with self.subTest(db_type=db_type, db_mode=db_mode):
                mode = _resolved_mode(db_type, db_mode)
                self.assertEqual(
                    _names_db_dependency(mode),
                    expected,
                    "the pipeline disagrees with what the composer emits -- "
                    "`docker compose up -d <project>-db` would hit an "
                    "undefined service",
                )
                self.assertEqual(
                    _emits_db_service(db_type, db_mode),
                    expected,
                    "the composer disagrees with the pipeline",
                )

    def test_external_names_no_dependency(self):
        self.assertFalse(
            _names_db_dependency(_resolved_mode("external", None)),
            "external has no <project>-db service, so depending on one makes "
            "`docker compose up -d <project>-db` fail on an undefined service",
        )

    def test_external_really_emits_no_service(self):
        # The other half of the contract: if this ever starts emitting one,
        # the guard above should be revisited rather than silently diverging.
        self.assertFalse(_emits_db_service("external", None))

    def test_hypersonic_names_no_dependency(self):
        self.assertFalse(_names_db_dependency(_resolved_mode("hypersonic", None)))

    def test_shared_names_no_dependency(self):
        self.assertFalse(_names_db_dependency(_resolved_mode("postgresql", "shared")))

    def test_an_isolated_engine_still_names_its_dependency(self):
        # The fix must not stop a normal isolated project waiting for its DB.
        self.assertTrue(_names_db_dependency(_resolved_mode("postgresql", "isolated")))
        self.assertTrue(_names_db_dependency(_resolved_mode("mysql", "isolated")))

    def test_an_isolated_engine_really_emits_a_service(self):
        self.assertTrue(_emits_db_service("postgresql", "isolated"))


if __name__ == "__main__":
    unittest.main()
