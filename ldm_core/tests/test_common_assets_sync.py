"""The shared `common/` folder: sync into a project, `init-common`, and doctor.

LDM-#2193: a non-search `.config` placed in `~/.ldm/common` must reach every
project, and a project-local file of the same name must win. A test using only
the search configs would pass while the general case was broken -- the search
path has its own branch in the same function -- so both are covered here, and
the search branch's own rules (it overwrites on any difference and keeps no
history, LDM-#2453) are pinned separately.

LDM-#2452: `init-common` wrote `./common` in the current directory when
`~/.ldm/common` did not exist yet.

Every test drives the real path resolution (`setup_paths` -> `get_common_dirs`)
under an isolated home. Under pytest `sys.argv[0]` ends in `.py`, so
`get_common_dir()` would otherwise return the REPOSITORY's own `common/`
folder; each test patches `sys.argv`, points `LDM_HOME` at a temp dir, runs
from a temp cwd and removes `LDM_COMMON_DIR`.
"""

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ES_PREFIX = "com.liferay.portal.search.elasticsearch8.configuration."
ES_CONN = f"{ES_PREFIX}ElasticsearchConnectionConfiguration.config"
ES_CONN_REMOTE = f"{ES_PREFIX}ElasticsearchConnectionConfiguration-REMOTE.config"
SHARED_CONFIG = "com.example.SharedSetting.config"


class _IsolatedHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()
        self.global_common = self.home / ".ldm" / "common"

        env = {k: v for k, v in os.environ.items() if k != "LDM_COMMON_DIR"}
        env["LDM_HOME"] = str(self.home)
        for p in (
            patch.dict(os.environ, env, clear=True),
            patch("sys.argv", ["ldm"]),
            # LDM-#1409: the search-version probe goes through the module-scope
            # run_command in handlers/config.py; never ask the real daemon.
            patch(
                "ldm_core.handlers.config.run_command",
                return_value="docker.elastic.co/elasticsearch/elasticsearch:8.17.0",
            ),
        ):
            p.start()
            self.addCleanup(p.stop)

        from ldm_core.config import TargetNode

        p = patch(
            "ldm_core.docker_service.get_active_target",
            return_value=TargetNode(name="local", host="localhost", is_default=True),
        )
        p.start()
        self.addCleanup(p.stop)

        old_cwd = Path.cwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, old_cwd)

        from ldm_core.ui import UI

        saved = {
            k: getattr(UI, k)
            for k in ("NON_INTERACTIVE", "VERBOSE", "INFO_MODE", "QUIET_MODE")
        }
        for key, value in saved.items():
            self.addCleanup(setattr, UI, key, value)

        from ldm_core.manager import LiferayManager

        self.manager = LiferayManager(
            argparse.Namespace(
                target=None,
                node=None,
                verbose=False,
                info=False,
                quiet=False,
                non_interactive=True,
                dry_run=False,
            )
        )


class TestCommonAssetsSync(_IsolatedHome):
    def setUp(self):
        super().setUp()
        self.global_common.mkdir(parents=True)
        self.project = self.tmp / "projects" / "demo"
        self.paths = self.manager.setup_paths(self.project)
        self.paths["configs"].mkdir(parents=True)
        self.paths["deploy"].mkdir(parents=True)
        self.paths["files"].mkdir(parents=True)

    def _sync(self):
        self.manager.config.sync_common_assets(
            self.paths, project_meta={"use_shared_search": "true"}
        )

    def _history(self):
        history_file = self.project / ".liferay-docker.deployed"
        if not history_file.exists():
            return set()
        return set(history_file.read_text(encoding="utf-8").splitlines())

    def test_paths_resolve_to_the_isolated_global_folder(self):
        self.assertEqual(self.paths["common_dirs"], [self.global_common])

    def test_non_search_config_reaches_the_project_and_is_recorded(self):
        (self.global_common / SHARED_CONFIG).write_text('setting="shared"\n')

        self._sync()

        dest = self.paths["configs"] / SHARED_CONFIG
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_text(), 'setting="shared"\n')
        self.assertIn(SHARED_CONFIG, self._history())

    def test_project_local_file_of_the_same_name_wins(self):
        (self.global_common / SHARED_CONFIG).write_text('setting="shared"\n')
        local = self.paths["configs"] / SHARED_CONFIG
        local.write_text('setting="local"\n')

        self._sync()
        self._sync()

        self.assertEqual(local.read_text(), 'setting="local"\n')
        self.assertNotIn(SHARED_CONFIG, self._history())

    def test_search_connection_config_without_a_checksum_is_rewritten_then_recorded(
        self,
    ):
        # A connection config copied before LDM-#2424 has no recorded checksum,
        # so it is treated as unedited: rewritten once from common/, then
        # recorded so common_config_policy applies to it from now on.
        (self.global_common / ES_CONN_REMOTE).write_text(
            'networkHostAddresses=["http://search:9200"]\n'
        )
        local = self.paths["configs"] / ES_CONN
        local.write_text('networkHostAddresses=["http://edited:9200"]\n')

        self._sync()

        self.assertEqual(
            local.read_text(), 'networkHostAddresses=["http://search:9200"]\n'
        )
        self.assertIn(ES_CONN_REMOTE, self._history())


class TestInitCommon(_IsolatedHome):
    def test_creates_the_global_folder_and_nothing_in_cwd(self):
        self.assertFalse(self.global_common.exists())

        self.manager.config.cmd_init_common()

        self.assertTrue(self.global_common.is_dir())
        self.assertTrue((self.global_common / "portal-ext.properties").exists())
        self.assertFalse((self.cwd / "common").exists())

    def test_leaves_an_active_workspace_common_unchanged_and_names_it(self):
        workspace_common = self.cwd / "common"
        workspace_common.mkdir()

        with patch("ldm_core.handlers.config.UI.info") as info:
            self.manager.config.cmd_init_common()

        self.assertEqual(list(workspace_common.iterdir()), [])
        self.assertTrue((self.global_common / "portal-ext.properties").exists())
        self.assertTrue(
            any(str(workspace_common) in str(c.args[0]) for c in info.call_args_list)
        )

    def test_never_overwrites_an_existing_file(self):
        self.global_common.mkdir(parents=True)
        pe = self.global_common / "portal-ext.properties"
        pe.write_text("mine=true\n")

        self.manager.config.cmd_init_common()

        self.assertEqual(pe.read_text(), "mine=true\n")


class TestDoctorSharedConfigRow(unittest.TestCase):
    def test_missing_row_names_the_folder_and_what_it_does(self):
        import types

        from ldm_core.diagnostics.doctor import DoctorRunner

        class _Stub:
            def __init__(self):
                self.results = []
                self.hints = []

            def add_hint(self, text, doc=None):
                self.hints.append({"text": text, "doc": doc})

        stub = _Stub()
        report = types.MethodType(DoctorRunner._report_missing_shared_config, stub)
        missing = Path("/nonexistent/.ldm/common")

        report(missing)

        label, status, ok = stub.results[0]
        self.assertEqual(label, "Shared Config (common/)")
        self.assertIn(str(missing), status)
        self.assertIn("reach every project", status)
        self.assertIn("ldm init-common", status)
        self.assertEqual(ok, "warn")
        self.assertTrue(stub.hints[0]["doc"].endswith("how-to/share_configuration.md"))


if __name__ == "__main__":
    unittest.main()
