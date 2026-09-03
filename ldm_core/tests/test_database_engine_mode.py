"""LDM-#1511: `external` is a database MODE, not a database TYPE.

Deliberately behavioural throughout (the #1516 gate). Every test here either
builds a real compose fragment and asserts what came out of it, or calls the
real validator and asserts the process *exits*. Nothing asserts that a string
appears somewhere.

The three properties under test:

1. The container question is asked of the MODE. `_build_db_service` emits the
   per-project database in exactly one mode, so `depends_on` must name it in
   exactly that mode -- naming it in any other produces the #1359 failure,
   "depends on undefined service", which `docker compose config` refuses.
2. The engine survives `external`. Under the old model choosing `external`
   discarded it, so `driverClassName` and `hibernate.dialect` were never
   written for an external database at all.
3. An impossible engine/mode pairing is REJECTED, not accepted and ignored.
"""

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.composer import ComposerService
from ldm_core.tests.test_composer import MockComposerManager
from ldm_core.utils import (
    DatabaseConfigError,
    infer_db_engine_from_jdbc_url,
    normalize_database_selection,
    resolve_database_config,
)

PATHS_KEYS = (
    "deploy",
    "files",
    "data",
    "configs",
    "modules",
    "cx",
    "scripts",
    "state",
    "logs",
    "portal_log4j",
    "ce_dir",
)


def _paths():
    root = Path("/tmp/Ext-Project")  # nosec B108 -- never written to; composer
    paths = {"root": root}  # only renders the path into YAML strings
    paths.update({k: root / k for k in PATHS_KEYS})
    return paths


class _ComposerCase(unittest.TestCase):
    """Shared harness: a composer over a mock manager, as test_composer.py does."""

    def setUp(self):
        self.manager = MockComposerManager()
        self.composer = ComposerService(self.manager)
        self.manager.defaults.get.side_effect = lambda _key, default=None: default
        self.target_patcher = patch(
            "ldm_core.config.get_active_target",
            return_value=MagicMock(name="local", host="localhost", is_default=True),
        )
        self.target_patcher.start()

    def tearDown(self):
        self.target_patcher.stop()

    def _build(self, meta, project_name="Ext-Project"):
        """Builds both halves of the compose file and returns them.

        Returns (liferay_service, db_service, portal_ext_updates).
        """
        service = self.composer._build_liferay_service(
            _paths(), meta, "localhost", project_name, False, None
        )
        db_service = self.composer._build_db_service(meta, project_name)

        updates = {}
        for call in self.manager.config.update_portal_ext.call_args_list:
            args = call[0]
            if len(args) > 1 and isinstance(args[1], dict):
                updates.update(args[1])
        return service, db_service, updates

    def assert_compose_is_self_consistent(self, service, db_service, project_name):
        """No `depends_on` may name a service the compose file does not define.

        This is the #1359 failure verbatim, and it is the property that made
        `--db external` unusable in `isolated` mode: the database service was
        deliberately not emitted, yet the dependency on it was.
        """
        defined = {"liferay"}
        if db_service:
            defined.add(f"{project_name}-db")
        for dep in service.get("depends_on") or []:
            self.assertIn(
                dep,
                defined,
                f"liferay depends on undefined service {dep!r} -- "
                "docker compose will refuse this file",
            )


class TestExternalIsAMode(_ComposerCase):
    """A project whose database LDM does not run gets no container for it."""

    def test_legacy_external_project_emits_no_database_service(self):
        # Exactly what an existing project's `meta` looks like today.
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "Ext-Project",
            "db_type": "external",
            "jdbc_url": "jdbc:postgresql://db.example.com:5432/lportal",
            "jdbc_user": "liferay",
            "jdbc_pass": "secret",  # pragma: allowlist secret
        }
        service, db_service, _ = self._build(meta)

        self.assertIsNone(db_service, "LDM must not run someone else's database")
        self.assert_compose_is_self_consistent(service, db_service, "Ext-Project")
        self.assertNotIn("Ext-Project-db", service.get("depends_on") or [])

    def test_external_mode_on_a_named_engine_emits_no_database_service(self):
        """`--db postgresql --database-mode external`: previously inexpressible."""
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "Ext-Project",
            "db_type": "postgresql",
            "database_mode": "external",
            "jdbc_url": "jdbc:postgresql://db.example.com:5432/lportal",
            "jdbc_user": "liferay",
            "jdbc_pass": "secret",  # pragma: allowlist secret
        }
        service, db_service, _ = self._build(meta)

        self.assertIsNone(db_service)
        self.assert_compose_is_self_consistent(service, db_service, "Ext-Project")

    def test_isolated_is_untouched(self):
        """Guard against over-reach: the default path must still emit both."""
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "Ext-Project",
            "db_type": "postgresql",
            "database_mode": "isolated",
        }
        service, db_service, updates = self._build(meta)

        self.assertIsNotNone(db_service)
        self.assertIn("Ext-Project-db", service.get("depends_on") or [])
        self.assertIn("Ext-Project-db", updates["jdbc.default.url"])

    def test_hypersonic_is_embedded_even_when_meta_says_shared(self):
        """A state LDM itself used to write must still boot (decision 2).

        `database_mode` has been persisted since #1359 and was resolved from
        defaults before that, so a Hypersonic project carrying
        `database_mode: "shared"` exists in the wild. It must resolve to
        `embedded` -- no container, no dependency -- not be rejected.
        """
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "Ext-Project",
            "db_type": "hypersonic",
            "database_mode": "shared",
        }
        service, db_service, updates = self._build(meta)

        self.assertIsNone(db_service)
        self.assert_compose_is_self_consistent(service, db_service, "Ext-Project")
        self.assertEqual(service.get("depends_on"), None)
        # Hypersonic is in-JVM: no JDBC coordinates are written for it at all.
        self.assertNotIn("jdbc.default.url", updates)


class TestTheMigrationNoticeIsAboutAChoice(_ComposerCase):
    """The normalisation warning must fire for a CHOICE, not for a default.

    `database_mode` has a convention default of "isolated", so comparing the
    engine's implied mode against the fully resolved
    `resolve_infrastructure_mode` result warns about a value nobody typed:
    every Hypersonic project -- whose mode is `embedded` by definition, always,
    with no way to change it -- would print "mode 'isolated' does not apply" on
    every single run, forever. Nothing the user could do would silence it.

    So the comparison is against the explicit sources only: the CLI override
    and a value persisted into this project's own meta.
    """

    def _warnings(self, meta):
        with patch("ldm_core.ui.UI.warning") as warn:
            self._build(meta)
        return [str(call[0][0]) for call in warn.call_args_list]

    def _hypersonic(self, **extra):
        meta = {
            "tag": "2026.q1.7-lts",
            "container_name": "Ext-Project",
            "db_type": "hypersonic",
        }
        meta.update(extra)
        return meta

    def test_hypersonic_with_no_stored_mode_is_silent(self):
        self.assertEqual(
            self._warnings(self._hypersonic()),
            [],
            "warned about a database mode the user never chose",
        )

    def test_hypersonic_with_a_stored_contradiction_says_so_once(self):
        """The other half: a mode LDM itself persisted IS worth reporting."""
        warnings = self._warnings(self._hypersonic(database_mode="shared"))
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("shared", warnings[0])
        self.assertIn("embedded", warnings[0])

    def test_an_ordinary_isolated_project_is_silent(self):
        """Guard against over-reach in the other direction."""
        self.assertEqual(
            self._warnings(
                {
                    "tag": "2026.q1.7-lts",
                    "container_name": "Ext-Project",
                    "db_type": "postgresql",
                }
            ),
            [],
        )


class TestTheEngineSurvivesExternal(_ComposerCase):
    """The information the old model provably lost."""

    def _external_meta(self, url, db_type="external"):
        return {
            "tag": "2026.q1.7-lts",
            "container_name": "Ext-Project",
            "db_type": db_type,
            "jdbc_url": url,
            "jdbc_user": "liferay",
            "jdbc_pass": "secret",  # pragma: allowlist secret
        }

    def test_external_postgresql_gets_a_driver_and_a_dialect(self):
        meta = self._external_meta("jdbc:postgresql://db.example.com:5432/lportal")
        _, _, updates = self._build(meta)

        self.assertIn("postgresql", updates["jdbc.default.driverClassName"].lower())
        self.assertIn("postgres", updates["hibernate.dialect"].lower())
        # ...pointed at the user's server, not at a container LDM would run.
        self.assertEqual(
            updates["jdbc.default.url"], "jdbc:postgresql://db.example.com:5432/lportal"
        )
        self.assertEqual(updates["jdbc.default.username"], "liferay")
        self.assertEqual(updates["jdbc.default.password"], "secret")

    def test_external_mysql_gets_the_mysql_driver_and_dialect(self):
        """The #1361 per-tag dialect care, now available to an external MySQL."""
        meta = self._external_meta("jdbc:mysql://db.example.com:3306/lportal")
        _, _, updates = self._build(meta)

        driver = updates["jdbc.default.driverClassName"].lower()
        self.assertTrue(
            "mysql" in driver or "mariadb" in driver,
            f"expected a MySQL/MariaDB driver, got {driver!r}",
        )
        dialect = updates["hibernate.dialect"].lower()
        self.assertTrue(
            "mysql" in dialect or "mariadb" in dialect,
            f"expected a MySQL/MariaDB dialect, got {dialect!r}",
        )
        self.assertEqual(
            updates["jdbc.default.url"], "jdbc:mysql://db.example.com:3306/lportal"
        )

    def test_an_uninferable_url_keeps_the_legacy_read_path(self):
        """Decision 2's fallback: no guessing, no prompt, no crash.

        An Oracle URL names no engine LDM supports, so there is no driver or
        dialect to resolve. The pre-#1511 behaviour -- URL, user and password
        only -- must survive verbatim, because a project that booted yesterday
        must boot today.
        """
        meta = self._external_meta("jdbc:oracle:thin:@db.example.com:1521:XE")
        service, db_service, updates = self._build(meta)

        self.assertIsNone(db_service)
        self.assert_compose_is_self_consistent(service, db_service, "Ext-Project")
        self.assertEqual(
            updates["jdbc.default.url"], "jdbc:oracle:thin:@db.example.com:1521:XE"
        )
        self.assertNotIn("jdbc.default.driverClassName", updates)
        self.assertNotIn("hibernate.dialect", updates)


class TestJdbcEngineInference(unittest.TestCase):
    """The migration table from the #1511 decision."""

    def test_the_decision_table(self):
        cases = {
            "jdbc:postgresql://host:5432/lportal": "postgresql",
            "jdbc:postgres://host:5432/lportal": "postgresql",
            "jdbc:mysql://host:3306/lportal": "mysql",
            "jdbc:mariadb://host:3306/lportal": "mysql",
            "jdbc:oracle:thin:@host:1521:XE": None,
            "jdbc:sqlserver://host:1433": None,
            "": None,
            None: None,
            "not a url at all": None,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(infer_db_engine_from_jdbc_url(url), expected)

    def test_a_legacy_external_project_is_read_forward(self):
        engine, mode = normalize_database_selection(
            "external", None, "jdbc:postgresql://host:5432/lportal"
        )
        self.assertEqual((engine, mode), ("postgresql", "external"))

    def test_a_legacy_external_project_without_an_engine_keeps_the_marker(self):
        engine, mode = normalize_database_selection("external", None, "jdbc:h2:mem:x")
        self.assertEqual((engine, mode), ("external", "external"))

    def test_hypersonic_implies_embedded(self):
        self.assertEqual(
            normalize_database_selection("hypersonic", "shared"),
            ("hypersonic", "embedded"),
        )

    def test_an_absent_mode_defaults_to_isolated(self):
        self.assertEqual(
            normalize_database_selection("postgresql", None), ("postgresql", "isolated")
        )


class TestImpossiblePairingsAreRejected(unittest.TestCase):
    """The point of making the mode axis total (decision 3).

    `--database-mode shared` was accepted and silently did nothing until
    #1359. These assert the *outcome* -- the process exits 1 -- not that a
    message was produced.
    """

    def _die(self, db, mode):
        from ldm_core.cli import _validate_database_flags

        with self.assertRaises(SystemExit) as ctx:
            _validate_database_flags(Namespace(db=db, database_mode=mode))
        return ctx.exception.code

    def test_hypersonic_cannot_be_shared(self):
        self.assertEqual(self._die("hypersonic", "shared"), 1)

    def test_hypersonic_cannot_be_isolated(self):
        self.assertEqual(self._die("hypersonic", "isolated"), 1)

    def test_legacy_external_cannot_also_be_shared(self):
        """The combination the #1511 note calls out.

        Both flags suppressed the per-project container today, so the pair was
        accepted and meaningless. Under the new model it is not expressible.
        """
        self.assertEqual(self._die("external", "shared"), 1)

    def test_a_real_engine_cannot_be_embedded(self):
        self.assertEqual(self._die("postgresql", "embedded"), 1)
        self.assertEqual(self._die("mysql", "embedded"), 1)

    def test_the_message_names_both_values(self):
        """Not a substitute for the exit assertions above -- an addition.

        A refusal the user cannot act on is barely better than silence, and
        naming only one of the two values is what makes the current split
        confusing in the first place.
        """
        with self.assertRaises(DatabaseConfigError) as ctx:
            normalize_database_selection("hypersonic", "shared", strict=True)
        message = str(ctx.exception)
        self.assertIn("hypersonic", message)
        self.assertIn("shared", message)

    def test_valid_pairings_are_not_rejected(self):
        from ldm_core.cli import _validate_database_flags

        for db, mode in (
            ("postgresql", "isolated"),
            ("postgresql", "shared"),
            ("postgresql", "external"),
            ("mysql", "isolated"),
            ("mysql", "shared"),
            ("mysql", "external"),
            ("hypersonic", "embedded"),
            ("external", "external"),
            (None, "shared"),
            ("mysql", None),
        ):
            with self.subTest(db=db, mode=mode):
                _validate_database_flags(Namespace(db=db, database_mode=mode))

    def test_an_impossible_persisted_pairing_exits_one(self):
        """Resolve time, not just parse time.

        A hand-edited `meta` is the other way an impossible pair reaches the
        composer, and it must not be accepted and behave unexpectedly later.
        """
        defaults = MagicMock()
        defaults.get.side_effect = lambda _key, default=None: default
        meta = {"db_type": "postgresql", "database_mode": "embedded"}

        with self.assertRaises(SystemExit) as ctx:
            resolve_database_config(meta, defaults)
        self.assertEqual(ctx.exception.code, 1)


class TestRunPipelineMigratesTheMetadata(unittest.TestCase):
    """The explicit read-old-write-new step (#1509's lesson, applied)."""

    def _stage(self):
        from ldm_core.pipelines.run import ConfigResolutionStage

        return ConfigResolutionStage()

    def _manager(self, args_db=None, args_mode=None):
        manager = MagicMock()
        manager.args = Namespace(db=args_db, database_mode=args_mode)
        manager.defaults = MagicMock()
        manager.defaults.get.side_effect = lambda _key, default=None: default
        return manager

    def test_a_stored_external_project_comes_back_with_its_engine(self):
        meta = {
            "db_type": "external",
            "jdbc_url": "jdbc:mariadb://db.example.com:3306/lportal",
            "jdbc_user": "liferay",
            "jdbc_pass": "secret",  # pragma: allowlist secret
        }
        db_type, db_mode = self._stage()._resolve_database(
            self._manager(), meta, is_samples=False
        )
        self.assertEqual((db_type, db_mode), ("mysql", "external"))

    def test_an_uninferable_external_project_keeps_the_legacy_marker(self):
        meta = {
            "db_type": "external",
            "jdbc_url": "jdbc:oracle:thin:@db.example.com:1521:XE",
            "jdbc_user": "liferay",
            "jdbc_pass": "secret",  # pragma: allowlist secret
        }
        db_type, db_mode = self._stage()._resolve_database(
            self._manager(), meta, is_samples=False
        )
        self.assertEqual((db_type, db_mode), ("external", "external"))

    def test_external_and_shared_in_one_stored_project_resolves_to_external(self):
        """The migration question the #1511 note leaves open.

        Both values suppressed the container, but only `external` decided
        anything else -- the JDBC branch keyed on it -- so `external` is what
        the project was actually running. It wins.
        """
        meta = {
            "db_type": "external",
            "database_mode": "shared",
            "jdbc_url": "jdbc:postgresql://db.example.com:5432/lportal",
        }
        db_type, db_mode = self._stage()._resolve_database(
            self._manager(), meta, is_samples=False
        )
        self.assertEqual((db_type, db_mode), ("postgresql", "external"))

    def test_the_jdbc_prompt_is_driven_by_the_mode_not_the_engine(self):
        """`--db postgresql --database-mode external` must still ask."""
        manager = self._manager(args_db="postgresql", args_mode="external")
        meta: dict = {}

        with patch(
            "ldm_core.ui.UI.ask",
            side_effect=["jdbc:postgresql://host:5432/lportal", "u", "p"],
        ) as ask:
            db_type, db_mode = self._stage()._resolve_database(
                manager, meta, is_samples=False
            )

        self.assertEqual((db_type, db_mode), ("postgresql", "external"))
        self.assertEqual(ask.call_count, 3)
        self.assertEqual(meta["jdbc_url"], "jdbc:postgresql://host:5432/lportal")

    def test_the_prompt_recovers_the_engine_for_bare_db_external(self):
        """`--db external` with nothing stored: the URL names the engine."""
        manager = self._manager(args_db="external")
        meta: dict = {}

        with patch(
            "ldm_core.ui.UI.ask",
            side_effect=["jdbc:mysql://host:3306/lportal", "u", "p"],
        ):
            db_type, db_mode = self._stage()._resolve_database(
                manager, meta, is_samples=False
            )

        self.assertEqual((db_type, db_mode), ("mysql", "external"))

    def test_an_isolated_project_is_not_prompted(self):
        manager = self._manager(args_db="postgresql")
        with patch("ldm_core.ui.UI.ask") as ask:
            db_type, db_mode = self._stage()._resolve_database(
                manager, {}, is_samples=False
            )
        ask.assert_not_called()
        self.assertEqual((db_type, db_mode), ("postgresql", "isolated"))


class TestLdmDoesNotTouchSomeoneElsesDatabase(unittest.TestCase):
    """`external` means LDM has no container to exec into.

    Before #1511 the engine check at the top of `cmd_query` happened to cover
    this, because choosing "external" discarded the engine. Now the engine of
    an external database is known and real, so the guard has to be the mode's.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _handler(self, meta):
        from ldm_core.handlers.database import DatabaseService

        manager = MagicMock()
        manager.args = Namespace(database_mode=None, db=None)
        manager.defaults = MagicMock()
        manager.defaults.get.side_effect = lambda _key, default=None: default
        manager.detect_project_path.return_value = self.root
        manager.read_meta.return_value = meta
        # The container-is-running probe. Answering "yes" means the isolated
        # case below reaches the exec it is asserting on.
        manager.run_command.return_value = "deadbeef"
        return DatabaseService(manager), manager

    def _run_query(self, meta):
        """Runs `ldm db query` with the daemon replaced. Returns the exec argv."""
        handler, _ = self._handler(meta)
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch(
            "ldm_core.handlers.database.subprocess.run", return_value=completed
        ) as run:
            handler.cmd_query(sql="SELECT 1", allow_query=True)
        return [call[0][0] for call in run.call_args_list]

    def test_db_query_execs_nothing_in_external_mode(self):
        commands = self._run_query(
            {
                "db_type": "postgresql",
                "database_mode": "external",
                "container_name": "Ext-Project",
                "jdbc_url": "jdbc:postgresql://db.example.com:5432/lportal",
            }
        )
        self.assertEqual(
            commands, [], f"LDM reached for a container it does not own: {commands!r}"
        )

    def test_db_query_still_execs_for_an_isolated_project(self):
        """Guard against over-reach: the refusal must be mode-specific."""
        commands = self._run_query(
            {
                "db_type": "postgresql",
                "database_mode": "isolated",
                "container_name": "Ext-Project",
                "db_container_name": "Ext-Project-db",
            }
        )
        self.assertTrue(commands, "an isolated project must still be queryable")
        self.assertIn("Ext-Project-db", commands[0])


if __name__ == "__main__":
    unittest.main()
