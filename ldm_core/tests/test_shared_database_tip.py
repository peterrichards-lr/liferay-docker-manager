"""LDM-#1510: offer shared database mode once, and name the engine.

Shared mode works and saves ~120 MB per project, but nothing advertises it, so
the default of one container per project is a choice nobody makes knowingly.

Behavioural throughout. Every test either calls the real predicate, or calls
the real emitter with `UI.hint` captured and asserts on **what was printed**.
Nothing asserts that a string appears in a source file, and nothing restates
the rule the code implements -- `offer_shared_database_tip` is invoked, not
re-derived.

`LDM_HOME` is redirected to a temp directory for every test that touches the
registry. `HOME` alone would not do it: `get_actual_home()` reconstructs
`/Users/<username>` from `SUDO_USER`/`USER` and ignores `HOME` entirely
(#1349), so the real `~/.ldm/registry.json` would otherwise be read.
"""

import json
import os
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar
from unittest.mock import MagicMock, patch

from ldm_core.constants import REGISTRY_FILE
from ldm_core.defaults import CONVENTION_DEFAULTS, DefaultsManager
from ldm_core.pipelines.run import (
    SHARED_DB_TIP_MB,
    offer_shared_database_tip,
    should_offer_shared_database_tip,
)
from ldm_core.utils import registered_project_count, shared_database_engine


class _IsolatedHome(unittest.TestCase):
    """Redirects LDM's whole state directory into a temp dir."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {"LDM_HOME": str(self.home)})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._tmp.cleanup)

    def write_registry(self, *project_names):
        ldm_dir = self.home / ".ldm"
        ldm_dir.mkdir(parents=True, exist_ok=True)
        (ldm_dir / REGISTRY_FILE).write_text(
            json.dumps(
                {
                    name: {"path": str(self.home / name), "host": None}
                    for name in project_names
                }
            ),
            encoding="utf-8",
        )


class TestRegisteredProjectCount(_IsolatedHome):
    """The only signal available for "is this the first project" (#1510)."""

    def test_no_registry_file_at_all_counts_zero(self):
        # What a genuinely first-ever run starts from.
        self.assertEqual(registered_project_count(), 0)

    def test_counts_the_entries(self):
        self.write_registry("alpha")
        self.assertEqual(registered_project_count(), 1)
        self.write_registry("alpha", "beta", "gamma")
        self.assertEqual(registered_project_count(), 3)

    def test_a_corrupt_registry_counts_zero_and_does_not_raise(self):
        """A cosmetic tip must never turn into a failed `ldm run`."""
        ldm_dir = self.home / ".ldm"
        ldm_dir.mkdir(parents=True, exist_ok=True)
        (ldm_dir / REGISTRY_FILE).write_text("{not json", encoding="utf-8")
        self.assertEqual(registered_project_count(), 0)

    def test_a_registry_that_is_not_an_object_counts_zero(self):
        ldm_dir = self.home / ".ldm"
        ldm_dir.mkdir(parents=True, exist_ok=True)
        (ldm_dir / REGISTRY_FILE).write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(registered_project_count(), 0)

    def test_it_reads_the_ldm_home_override_not_the_real_home(self):
        """The isolation this whole file depends on, asserted rather than assumed."""
        self.write_registry("only-in-the-temp-home")
        self.assertEqual(registered_project_count(), 1)
        with patch.dict(os.environ, {"LDM_HOME": str(self.home / "nowhere")}):
            self.assertEqual(registered_project_count(), 0)


class TestTheOfferRule(unittest.TestCase):
    """The three conditions, each shown to matter on its own."""

    BASE: ClassVar[dict] = {
        "is_first_project": True,
        "mode_chosen_explicitly": False,
        "resolved_mode": "isolated",
    }

    def _offer(self, **overrides):
        return should_offer_shared_database_tip(**{**self.BASE, **overrides})

    def test_the_baseline_case_is_offered(self):
        self.assertTrue(self._offer())

    def test_not_on_a_later_project(self):
        self.assertFalse(self._offer(is_first_project=False))

    def test_not_when_the_user_has_already_chosen(self):
        self.assertFalse(self._offer(mode_chosen_explicitly=True))

    def test_not_when_the_project_is_not_isolated(self):
        for mode in ("shared", "external", "embedded", None, ""):
            with self.subTest(mode=mode):
                self.assertFalse(self._offer(resolved_mode=mode))

    def test_every_condition_is_load_bearing(self):
        """Each condition alone must be able to veto the offer.

        Guards against the rule degrading into one that cannot reject: if any
        single flag stopped mattering, the baseline would still pass and only
        this would notice.
        """
        vetoes = (
            {"is_first_project": False},
            {"mode_chosen_explicitly": True},
            {"resolved_mode": "shared"},
        )
        for veto in vetoes:
            with self.subTest(veto=veto):
                self.assertFalse(self._offer(**veto))


class TestDefaultsKnowsAChoiceFromAFallback(_IsolatedHome):
    """`has_explicit` is what makes condition 2 answerable (#1510)."""

    def _write_ldmrc(self, **defaults):
        (self.home / ".ldmrc").write_text(
            json.dumps({"defaults": defaults}), encoding="utf-8"
        )

    def test_a_convention_default_is_not_explicit(self):
        mgr = DefaultsManager()
        # The convention default exists and resolves...
        self.assertEqual(mgr.get("database_mode"), CONVENTION_DEFAULTS["database_mode"])
        # ...but nobody chose it.
        self.assertFalse(mgr.has_explicit("database_mode"))

    def test_choosing_the_default_value_still_counts_as_choosing(self):
        """The case `get()` cannot distinguish, and the reason this exists.

        An explicit `isolated` and the convention `isolated` resolve to the
        same string. Only the layer they came from separates them.
        """
        self._write_ldmrc(database_mode="isolated")
        mgr = DefaultsManager()
        self.assertEqual(mgr.get("database_mode"), "isolated")
        self.assertTrue(mgr.has_explicit("database_mode"))

    def test_choosing_a_different_value_counts_too(self):
        self._write_ldmrc(database_mode="shared")
        mgr = DefaultsManager()
        self.assertTrue(mgr.has_explicit("database_mode"))

    def test_an_unrelated_setting_does_not_count(self):
        self._write_ldmrc(port="9080")
        mgr = DefaultsManager()
        self.assertTrue(mgr.has_explicit("port"))
        self.assertFalse(mgr.has_explicit("database_mode"))


class TestTheTipIsEmitted(_IsolatedHome):
    """End to end through the real emitter, asserting on what was printed."""

    def _manager(self, *, explicit_mode=None, db_type=None):
        manager = MagicMock()
        manager.args = Namespace(db=None, database_mode=None)
        ldmrc = {}
        if explicit_mode is not None:
            ldmrc["database_mode"] = explicit_mode
        if db_type is not None:
            ldmrc["db_type"] = db_type
        if ldmrc:
            (self.home / ".ldmrc").write_text(
                json.dumps({"defaults": ldmrc}), encoding="utf-8"
            )
        manager.defaults = DefaultsManager()
        return manager

    def _hints(self, manager, db_mode="isolated", is_new_project=True):
        with patch("ldm_core.pipelines.run.UI.hint") as hint:
            emitted = offer_shared_database_tip(manager, db_mode, is_new_project)
        return emitted, [str(call[0][0]) for call in hint.call_args_list]

    def test_the_first_project_is_told_about_shared_mode(self):
        self.write_registry("my-first-project")
        emitted, hints = self._hints(self._manager())

        self.assertTrue(emitted)
        self.assertEqual(len(hints), 1, hints)
        # It carries the command, the engine and the number.
        self.assertIn("ldm config database-mode shared", hints[0])
        self.assertIn("PostgreSQL", hints[0])
        self.assertIn(str(SHARED_DB_TIP_MB), hints[0])

    def test_the_second_project_is_not(self):
        self.write_registry("my-first-project", "my-second-project")
        emitted, hints = self._hints(self._manager())
        self.assertFalse(emitted)
        self.assertEqual(hints, [], "the tip repeated on a later project")

    def test_an_existing_project_being_re_run_is_not(self):
        """`is_new_project` is False on every re-run of the same project."""
        self.write_registry("my-first-project")
        emitted, hints = self._hints(self._manager(), is_new_project=False)
        self.assertFalse(emitted)
        self.assertEqual(hints, [])

    def test_a_user_who_has_chosen_is_not_pestered(self):
        self.write_registry("my-first-project")
        emitted, hints = self._hints(self._manager(explicit_mode="isolated"))
        self.assertFalse(emitted)
        self.assertEqual(hints, [], "offered a mode the user had already set")

    def test_a_project_already_in_shared_mode_is_not_offered_shared_mode(self):
        self.write_registry("my-first-project")
        emitted, hints = self._hints(self._manager(), db_mode="shared")
        self.assertFalse(emitted)
        self.assertEqual(hints, [])

    def test_the_tip_names_the_configured_engine_not_a_hardcoded_one(self):
        """Part 3 of #1510: choosing shared must not silently pick an engine."""
        self.write_registry("my-first-project")
        emitted, hints = self._hints(self._manager(db_type="mysql"))

        self.assertTrue(emitted)
        self.assertIn("MySQL", hints[0])
        self.assertNotIn("PostgreSQL", hints[0])


class TestTheSharedEngineIsConfigurable(unittest.TestCase):
    """`setup_global_database(db_type=None)` used to mean PostgreSQL, hardcoded."""

    def _defaults(self, db_type=None):
        defaults = MagicMock()
        defaults.get.side_effect = lambda key, default=None: (
            db_type if key == "db_type" else default
        )
        return defaults

    def test_no_defaults_at_all_is_postgresql(self):
        """The pre-#1361 behaviour every existing caller relies on."""
        self.assertEqual(shared_database_engine(None), "postgresql")

    def test_the_convention_default_is_postgresql(self):
        self.assertEqual(CONVENTION_DEFAULTS["db_type"], "postgresql")
        self.assertEqual(
            shared_database_engine(self._defaults("postgresql")), "postgresql"
        )

    def test_a_configured_mysql_default_selects_mysql(self):
        self.assertEqual(shared_database_engine(self._defaults("mysql")), "mysql")
        self.assertEqual(shared_database_engine(self._defaults("mariadb")), "mariadb")

    def test_an_engine_with_no_global_container_falls_back(self):
        """Hypersonic is in-process and `external` is someone else's server.

        Neither can back a global container, so naming one would produce a
        container name that can never exist.
        """
        for engine in ("hypersonic", "external", "oracle", "", None):
            with self.subTest(engine=engine):
                self.assertEqual(
                    shared_database_engine(self._defaults(engine)), "postgresql"
                )

    def test_a_defaults_object_that_raises_falls_back(self):
        broken = MagicMock()
        broken.get.side_effect = RuntimeError("boom")
        self.assertEqual(shared_database_engine(broken), "postgresql")


class TestGlobalDatabaseUsesTheConfiguredEngine(unittest.TestCase):
    """The container actually provisioned, observed through DockerService."""

    def _infra(self, db_type_default):
        from ldm_core.handlers.infra import InfraService

        manager = MagicMock()
        manager.args = Namespace(db=None, database_mode=None)
        manager.target = None
        manager.defaults = MagicMock()
        manager.defaults.get.side_effect = lambda key, default=None: (
            db_type_default if key == "db_type" else default
        )
        return InfraService(manager)

    def test_the_default_engine_provisions_the_postgresql_global(self):
        from ldm_core.utils import shared_database_container

        self.assertEqual(shared_database_container("postgresql"), "liferay-db-global")
        self.assertEqual(
            shared_database_container(shared_database_engine(None)),
            "liferay-db-global",
        )

    def test_a_configured_mysql_default_provisions_the_mysql_global(self):
        from ldm_core.utils import shared_database_container

        defaults = MagicMock()
        defaults.get.side_effect = lambda key, default=None: (
            "mysql" if key == "db_type" else default
        )
        self.assertEqual(
            shared_database_container(shared_database_engine(defaults)),
            "liferay-db-mysql-global",
        )

    def test_setup_global_database_reports_the_configured_engine(self):
        """The engine is stated, not assumed -- the point of part 3.

        Asserts on the message `setup_global_database` actually emitted, with
        the Docker layer replaced so nothing is created.
        """
        cases = (
            ("postgresql", "PostgreSQL", "liferay-db-global"),
            ("mysql", "MySQL", "liferay-db-mysql-global"),
        )
        for db_type_default, expected_label, expected_container in cases:
            with self.subTest(default=db_type_default):
                infra = self._infra(db_type_default)
                with (
                    patch(
                        "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
                        return_value=["docker"],
                    ),
                    patch(
                        "ldm_core.docker_service.DockerService.exists",
                        return_value=True,
                    ),
                    # Not running, then running: the container is restarted and
                    # the #1545 readiness re-probe is satisfied.
                    patch(
                        "ldm_core.docker_service.DockerService.is_running",
                        side_effect=[False, True],
                    ),
                    patch(
                        "ldm_core.docker_service.DockerService.start"
                    ) as docker_start,
                    patch("ldm_core.handlers.infra.UI.detail") as detail,
                    patch("ldm_core.handlers.infra.UI.success"),
                    patch("ldm_core.handlers.infra.UI.warning"),
                    patch("time.sleep"),
                ):
                    infra.setup_global_database()

                # The engine decided WHICH container was acted on...
                started = [c[0][0] for c in docker_start.call_args_list]
                self.assertEqual(started, [expected_container])
                # ...and the engine is stated rather than left implicit.
                messages = " ".join(str(c[0][0]) for c in detail.call_args_list)
                self.assertIn(expected_label, messages)


if __name__ == "__main__":
    unittest.main()
