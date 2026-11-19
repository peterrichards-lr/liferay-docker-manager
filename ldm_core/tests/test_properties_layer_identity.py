"""LDM-#2451: the properties cascade layers a common/ folder by identity.

Layer 3 (global) is `<LDM_HOME>/.ldm/common` and nothing else. Every other
folder -- a workspace `common/`, `LDM_COMMON_DIR` -- is layer 4 (local). The
cascade used to substring-match the resolved path for `.ldm`, `home` and
`global`, so on Linux, where every user path is under `/home/`, a workspace
`common/` became layer 3 and silently overwrote the global file.

LDM_HOME is placed under `<tmp>/home/alice` so every path in these tests
contains "home", reproducing the Linux layout on any OS.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.config import ConfigService
from ldm_core.tests.test_config import MockConfigManager


class _PathsManager(MockConfigManager):
    """The test manager, with the REAL common-folder discovery."""

    def setup_paths(self, root):
        return BaseHandler.setup_paths(self, root)  # type: ignore[arg-type]

    def get_common_dirs(self, project_path=None):
        return BaseHandler.get_common_dirs(self, project_path)  # type: ignore[arg-type]

    def get_common_dir(self, project_path=None):
        return BaseHandler.get_common_dir(self, project_path)  # type: ignore[arg-type]


class TestPropertiesLayerIdentity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

        self.home = self.tmp / "home" / "alice"
        self.global_common = self.home / ".ldm" / "common"
        self.global_common.mkdir(parents=True)

        env = {k: v for k, v in os.environ.items() if k != "LDM_COMMON_DIR"}
        env["LDM_HOME"] = str(self.home)
        for patcher in (
            patch.dict(os.environ, env, clear=True),
            # Under pytest get_common_dir would return the repo's own common/
            # via SCRIPT_DIR; a non-.py argv[0] keeps it out.
            patch("sys.argv", ["ldm"]),
            # LDM-#1409: sync_common_assets asks docker for the search image.
            patch("ldm_core.handlers.config.run_command", return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        from ldm_core.config import TargetNode

        target_patcher = patch("ldm_core.docker_service.get_active_target")
        target_patcher.start().return_value = TargetNode(
            name="local", host="localhost", is_default=True
        )
        self.addCleanup(target_patcher.stop)

        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)

        self.manager = _PathsManager()
        self.config = ConfigService(self.manager)

    def _workspace(self, *parts):
        ws = self.home.joinpath(*parts)
        (ws / "common").mkdir(parents=True)
        proj = ws / "proj"
        (proj / "files").mkdir(parents=True)
        return ws / "common", proj

    def _sync(self, proj):
        paths = self.manager.setup_paths(proj)
        self.config.sync_common_assets(paths)
        props, imp = self.config._get_properties_with_metadata(
            (proj / "files" / "portal-ext.properties").read_text()
        )
        return paths, props, imp

    def _assert_workspace_is_local(self, *parts):
        ws_common, proj = self._workspace(*parts)
        (self.global_common / "portal-ext.properties").write_text(
            "ldm.test.shared=global\nldm.test.global.only=yes\n"
        )
        (ws_common / "portal-ext.properties").write_text("ldm.test.shared=workspace\n")

        paths, props, _ = self._sync(proj)

        self.assertEqual(
            [p.resolve() for p in paths["common_dirs"]],
            [self.global_common.resolve(), ws_common.resolve()],
        )
        self.assertEqual(props.get("ldm.test.shared"), "workspace")
        self.assertEqual(props.get("ldm.test.global.only"), "yes")

    def test_workspace_under_home_is_local(self):
        """T1: a workspace common/ under /home is layer 4, not layer 3."""
        self._assert_workspace_is_local("demos")

    def test_workspace_under_homepage_is_local(self):
        """T2: a 'home' substring elsewhere in the path changes nothing."""
        self._assert_workspace_is_local("work", "homepage", "demos")

    def test_global_important_beats_workspace(self):
        """T3: ~/.ldm/common really is layer 3 -- its !important key wins."""
        ws_common, proj = self._workspace("demos")
        (self.global_common / "portal-ext.properties").write_text(
            "# !important\nldm.test.shared=global\n"
        )
        (ws_common / "portal-ext.properties").write_text("ldm.test.shared=workspace\n")

        _, props, imp = self._sync(proj)

        self.assertEqual(props.get("ldm.test.shared"), "global")
        self.assertIn("ldm.test.shared", imp)

    def test_ldm_common_dir_named_global_is_local(self):
        """T4: LDM_COMMON_DIR is layer 4 even when its path says 'global'."""
        env_common = self.tmp / "global-org" / "common"
        env_common.mkdir(parents=True)
        proj = self.tmp / "proj"
        (proj / "files").mkdir(parents=True)
        (self.global_common / "portal-ext.properties").write_text(
            "ldm.test.shared=global\nldm.test.global.only=yes\n"
        )
        (env_common / "portal-ext.properties").write_text("ldm.test.shared=env\n")

        with patch.dict(os.environ, {"LDM_COMMON_DIR": str(env_common)}):
            _, props, _ = self._sync(proj)

        self.assertEqual(props.get("ldm.test.shared"), "env")
        self.assertEqual(props.get("ldm.test.global.only"), "yes")

    def test_dashboard_reports_workspace_key_as_local(self):
        """T5: the dashboard's properties view layers by the same rule."""
        from ldm_core.dashboard.server import create_app

        ws_common, proj = self._workspace("demos")
        (self.global_common / "portal-ext.properties").write_text(
            "ldm.test.shared=global\nldm.test.global.only=yes\n"
        )
        (ws_common / "portal-ext.properties").write_text("ldm.test.shared=workspace\n")

        dash = MagicMock()
        dash.args = MagicMock()
        dash.find_dxp_roots.return_value = [{"path": proj}]
        dash.read_meta.return_value = {"liferay_container_name": "proj"}
        dash.setup_paths.side_effect = self.manager.setup_paths
        dash.config = MagicMock(wraps=self.config)
        dash.config.get_global_config.return_value = {}
        dash.config._get_properties_with_metadata.side_effect = (
            self.config._get_properties_with_metadata
        )

        app = create_app(dash, secret_key="t")  # pragma: allowlist secret
        response = app.test_client().get(
            "/api/projects/proj/properties", headers={"X-LDM-Token": "t"}
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        rows = {r["key"]: r for r in response.get_json()}

        self.assertEqual(rows["ldm.test.shared"]["source"], "local")
        self.assertEqual(rows["ldm.test.shared"]["value"], "workspace")
        self.assertEqual(rows["ldm.test.global.only"]["source"], "global")


if __name__ == "__main__":
    unittest.main()
