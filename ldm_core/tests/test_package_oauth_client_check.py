"""After an imported package boots, each routed OAuth client ID is checked (LDM-#2446).

The routes tree on disk uses Liferay's real file names (measured on an AICA
project: `<ERC>.oauth2.headless.server.client.id` beside `.client.secret`,
`.token.uri` and the rest). The database is a mocked `run_command`; the
command it is asked is asserted, so a query that could never run cannot pass.

The three boot sites -- `ldm import <file>`, `ldm import <github-url>` and
`ldm quickstart` -- are each shown to call the check, so the helper cannot be
proven while the shipped path never reaches it.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.snapshot import oauth_clients

EXT = "acme-microservice"
ERC = "acme-microservice-oauth"
CLIENT_ID = "id-1111"


def _routes(root, client_id=CLIENT_ID):
    """A routes tree as Liferay writes it, with one headless-server client."""
    ext = root / "routes" / "default" / EXT
    ext.mkdir(parents=True)
    (ext / f"{ERC}.oauth2.headless.server.client.id").write_text(f"{client_id}\n")
    (ext / f"{ERC}.oauth2.headless.server.client.secret").write_text("secret-x")
    (ext / f"{ERC}.oauth2.token.uri").write_text("http://dxp/o/oauth2/token")
    dxp = root / "routes" / "default" / "dxp"
    dxp.mkdir()
    (dxp / "com.liferay.lxc.dxp.mainDomain").write_text("localhost:8080")
    return root / "routes"


class _Project(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name) / "acme"
        self.root.mkdir()
        self.meta = {
            "container_name": "acme",
            "db_type": "postgresql",
            "database_mode": "isolated",
        }

    def _manager(self, db_rows="", running=True):
        manager = MagicMock()
        manager.args = MagicMock()
        manager.target = None
        manager.defaults = {}
        manager.detect_project_path.return_value = self.root
        manager.read_meta.return_value = dict(self.meta)
        manager.setup_paths.return_value = {
            "root": self.root,
            "routes": self.root / "routes",
        }

        def run(cmd, check=True, **_):
            if "ps" in cmd:
                return "abc123" if running else ""
            return db_rows

        manager.run_command.side_effect = run
        return manager

    def _warnings(self, manager):
        with patch("ldm_core.snapshot.oauth_clients.UI.warning") as warn:
            oauth_clients.warn_unresolved_oauth_clients(manager, "acme")
        return [c.args[0] for c in warn.call_args_list]


class TheRoutesAreRead(_Project):
    def test_only_client_ids_are_read(self):
        found = oauth_clients.routed_client_ids(_routes(self.root))
        self.assertEqual(found, [(EXT, ERC, CLIENT_ID)])

    def test_user_agent_client_ids_count_too(self):
        routes = _routes(self.root)
        ua = routes / "default" / EXT / "acme-ua.oauth2.user.agent.client.id"
        ua.write_text("id-2222")
        ids = {c for _, _, c in oauth_clients.routed_client_ids(routes)}
        self.assertEqual(ids, {CLIENT_ID, "id-2222"})

    def test_no_routes_tree_is_nothing(self):
        self.assertEqual(oauth_clients.routed_client_ids(self.root / "routes"), [])


class TheDatabaseIsAsked(_Project):
    def test_postgres_query_and_parse(self):
        manager = self._manager(db_rows="id-1111\nid-9999\n")
        known = oauth_clients.database_client_ids(manager, self.meta, self.root)
        self.assertEqual(known, {"id-1111", "id-9999"})
        cmd = manager.run_command.call_args.args[0]
        self.assertEqual(cmd[cmd.index("exec") + 1], "acme-db")
        self.assertIn("psql", cmd)
        self.assertIn("SELECT clientId FROM oauth2application;", cmd)

    def test_mysql_uses_its_own_client(self):
        self.meta["db_type"] = "mysql"
        manager = self._manager(db_rows="id-1111\n")
        oauth_clients.database_client_ids(manager, self.meta, self.root)
        cmd = manager.run_command.call_args.args[0]
        self.assertIn("mysql", cmd)
        self.assertIn("-N", cmd)

    def test_a_container_that_is_not_up_cannot_be_asked(self):
        manager = self._manager(running=False)
        self.assertIsNone(
            oauth_clients.database_client_ids(manager, self.meta, self.root)
        )

    def test_a_failed_query_cannot_be_asked(self):
        manager = self._manager(db_rows=None)
        self.assertIsNone(
            oauth_clients.database_client_ids(manager, self.meta, self.root)
        )

    def test_a_database_ldm_does_not_run_cannot_be_asked(self):
        self.meta["database_mode"] = "external"
        self.meta["jdbc_url"] = "jdbc:postgresql://db.example:5432/lportal"
        manager = self._manager()
        self.assertIsNone(
            oauth_clients.database_client_ids(manager, self.meta, self.root)
        )
        manager.run_command.assert_not_called()


class TheWarning(_Project):
    def test_an_unknown_client_id_names_the_extension_and_the_fix(self):
        _routes(self.root)
        said = self._warnings(self._manager(db_rows="id-other\n"))
        self.assertEqual(len(said), 1)
        self.assertIn(f"'{EXT}'", said[0])
        self.assertIn(ERC, said[0])
        self.assertIn(f"rm -rf {self.root / 'routes' / 'default' / EXT}", said[0])
        self.assertIn("ldm restart acme", said[0])
        self.assertNotIn(CLIENT_ID, said[0], "the client ID is a credential half")

    def test_a_known_client_id_is_silent(self):
        _routes(self.root)
        self.assertEqual(self._warnings(self._manager(db_rows="id-1111\n")), [])

    def test_no_routes_means_the_database_is_not_asked(self):
        manager = self._manager()
        self.assertEqual(self._warnings(manager), [])
        manager.run_command.assert_not_called()

    def test_an_unaskable_database_is_silent(self):
        _routes(self.root)
        self.assertEqual(self._warnings(self._manager(running=False)), [])

    def test_an_error_does_not_fail_the_import(self):
        manager = self._manager()
        manager.read_meta.side_effect = RuntimeError("boom")
        self.assertEqual(self._warnings(manager), [])


CHECK = "ldm_core.snapshot.oauth_clients.warn_unresolved_oauth_clients"


class TheLocalImportCallsIt(unittest.TestCase):
    """`FinalizationStage`: `ldm import <file>`."""

    def _finalize(self, is_ldmp, started=True):
        from ldm_core.pipelines.import_pipeline import (
            FinalizationStage,
            ImportPipelineContext,
        )

        manager = MagicMock()
        manager.runtime.cmd_run.return_value = started
        context = ImportPipelineContext(manager=manager, no_run=False)
        context.set("project_name", "acme")
        context.set("is_ldmp", is_ldmp)
        with (
            patch("ldm_core.pipelines.import_pipeline.UI"),
            patch(CHECK) as check,
        ):
            FinalizationStage().execute(context)
        return manager, check

    def test_a_package_import_is_checked_after_boot(self):
        manager, check = self._finalize(is_ldmp=True)
        check.assert_called_once_with(manager, "acme")

    def test_a_workspace_import_is_not(self):
        _, check = self._finalize(is_ldmp=False)
        check.assert_not_called()

    def test_a_failed_boot_is_not(self):
        _, check = self._finalize(is_ldmp=True, started=False)
        check.assert_not_called()


class TheReleaseAssetImportCallsIt(unittest.TestCase):
    """`_import_ldm_package`: `ldm import <github-url>`."""

    def test_checked_after_boot(self):
        from ldm_core.workspace import importer

        fake = MagicMock()
        fake.manager.non_interactive = True
        fake.manager.runtime.cmd_run.return_value = True
        cwd = os.getcwd()
        with TemporaryDirectory() as td:
            os.chdir(td)
            try:
                with (
                    patch.object(importer, "_download_ldm_package_assets"),
                    patch.object(importer, "_extract_ldm_package"),
                    patch.object(
                        importer,
                        "_verify_ldm_package_manifest",
                        return_value={"includes_database": "true"},
                    ),
                    patch.object(importer, "_apply_package_tag"),
                    patch.object(importer, "check_package_portal_patches"),
                    patch.object(importer, "_hydrate_ldm_package_to_project"),
                    patch(CHECK) as check,
                ):
                    importer._import_ldm_package(
                        fake, None, None, "o", "r", None, "acme", False
                    )
            finally:
                os.chdir(cwd)
        check.assert_called_once_with(fake.manager, "acme")


class QuickstartCallsIt(unittest.TestCase):
    def _quickstart(self, meta):
        from ldm_core.tests.test_workspace import MockWorkspaceManager

        manager = MockWorkspaceManager()
        manager.args.no_seed = True
        manager.args.replace = False
        with TemporaryDirectory() as td:
            project_dir = Path(td) / "aica"
            project_dir.mkdir()
            with (
                patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import"),
                patch.object(
                    MockWorkspaceManager,
                    "detect_project_path",
                    return_value=project_dir,
                ),
                patch(
                    "ldm_core.handlers.base.BaseHandler.read_meta",
                    return_value=dict(meta),
                ),
                patch.object(MockWorkspaceManager, "setup_paths", return_value={}),
                patch(
                    "ldm_core.handlers.runtime.RuntimeService.cmd_run",
                    return_value=True,
                ) as run,
                patch(CHECK) as check,
            ):
                manager.workspace.cmd_quickstart("aica")
        return check, run

    def test_a_package_is_checked_after_boot(self):
        check, run = self._quickstart(
            {
                "tag": "2026.q3.0",
                "db_type": "postgresql",
                "restored_from_package": "true",
            }
        )
        check.assert_called_once()
        self.assertEqual(check.call_args.args[1], run.call_args.args[0])

    def test_a_cloned_workspace_is_not(self):
        check, _ = self._quickstart({"tag": "2026.q3.0", "db_type": "postgresql"})
        check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
