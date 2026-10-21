"""The run pipeline's Liferay environment reaches the rendered compose (LDM-#2279).

`run.py` builds `liferay_env` -- LDM-#2154's virtual host, `--upgrade-db`,
`LIFERAY_HOME` -- and hands it to `write_docker_compose`, which passes it to
`_build_liferay_service` as `base_env`. Since eba44451 (v2.7.2) that parameter
was never read: the service's environment started empty, and every entry was
dropped. The #2154 tests asserted on the list the run stage hands over, which
was right, and nothing asserted on what came out. Reported from AICA#1283,
where the default company's virtual host stayed `localhost` and client
extensions' OAuth redirects pointed at `localhost:8080`.

These start at `write_docker_compose` and read the YAML it would write.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerService
from ldm_core.tests.test_composer import MockComposerManager
from ldm_core.tests.test_workspace import MockWorkspaceManager

VHOST = "LIFERAY_COMPANY_PERIOD_DEFAULT_PERIOD_VIRTUAL_PERIOD_HOST_PERIOD_NAME"
UPGRADE = "LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN"


class _ComposeFixture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name) / "proj"
        root.mkdir()
        self.paths = BaseHandler.setup_paths(MockWorkspaceManager(), root)
        manager = MockComposerManager()
        # A MagicMock here renders into the YAML as an unrepresentable object.
        from ldm_core.defaults import DefaultsManager

        manager.defaults = DefaultsManager()  # type: ignore[assignment]
        self.composer = ComposerService(manager)

    def tearDown(self):
        self._td.cleanup()

    def _rendered_env(self, liferay_env, meta_extra=None):
        from ldm_core.config import TargetNode

        meta = {
            "container_name": "proj",
            "tag": "2026.q1.0-lts",
            "host_name": "aica.demo",
            "db_type": "postgresql",
            **(meta_extra or {}),
        }
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}),
            patch(
                "ldm_core.config.get_active_target",
                return_value=TargetNode(
                    name="local", host="localhost", is_default=True
                ),
            ),
            patch("ldm_core.utils.safe_write_text") as write,
        ):
            self.composer.write_docker_compose(
                self.paths, meta, liferay_env=liferay_env
            )
        compose = yaml.safe_load(write.call_args.args[1])
        self.raw_env = compose["services"]["liferay"]["environment"]
        return dict(entry.split("=", 1) for entry in self.raw_env)


class TheRunEnvironmentReachesTheContainer(_ComposeFixture):
    def test_the_2154_virtual_host_is_in_the_liferay_service(self):
        env = self._rendered_env([f"{VHOST}=aica.demo", "LIFERAY_HOME=/opt/liferay"])
        self.assertEqual(env.get(VHOST), "aica.demo")
        self.assertEqual(env.get("LIFERAY_HOME"), "/opt/liferay")

    def test_upgrade_db_reaches_the_container(self):
        env = self._rendered_env([f"{UPGRADE}=true"])
        self.assertEqual(env.get(UPGRADE), "true")

    def test_custom_env_still_wins_and_no_key_is_repeated(self):
        """The user's custom_env is an explicit choice; it keeps its value, and
        the key appears once rather than relying on Compose's tie-break."""
        env_list = [f"{VHOST}=aica.demo"]
        env = self._rendered_env(
            env_list, {"custom_env": '{"' + VHOST + '": "mine.example"}'}
        )
        self.assertEqual(env.get(VHOST), "mine.example")
        keys = [entry.split("=", 1)[0] for entry in self.raw_env]
        self.assertEqual(keys.count(VHOST), 1, self.raw_env)

    def test_no_base_env_changes_nothing(self):
        """Control: callers that pass none still get the composer's own set."""
        env = self._rendered_env(None)
        self.assertNotIn(VHOST, env)
        self.assertIn("LIFERAY_JVM_OPTS", env)


class EverythingTheRunStageBuildsArrives(_ComposeFixture):
    """The whole seam: the list `ComposerStage` actually hands over, not a
    hand-written one, so a variable added to `run.py` later is covered too."""

    def _stage_env(self):
        from unittest.mock import MagicMock

        from ldm_core.pipelines.run import ComposerStage, RunPipelineContext

        context = RunPipelineContext(MagicMock())
        manager = context.manager
        manager.non_interactive = True
        # An upgrade boot with --upgrade-db, so that branch contributes too.
        manager.args.upgrade_db = True
        manager.args.no_backup_on_upgrade = True
        manager.args.backup_on_upgrade = False
        manager.parse_version = lambda tag: BaseHandler.parse_version(
            MockWorkspaceManager(), tag
        )
        context.set("project_id", "proj")
        context.set("is_new_project", False)
        context.set("dry_run", True)
        context.set("host_name", "aica-e2e.demo")
        context.set("tag", "2026.q2.0")
        context.set(
            "project_meta",
            {
                "container_name": "proj",
                "ssl": "false",
                "port": 8080,
                "last_run_liferay_version": "2025.q1.0-lts",
            },
        )
        root_mock = MagicMock()
        root_mock.__truediv__.return_value.exists.return_value = False
        context.set("paths", {"root": root_mock, "configs": MagicMock()})
        context.set("infra_ports", {})
        with patch("ldm_core.pipelines.run.UI"):
            ComposerStage().execute(context)
        return list(
            manager.composer.write_docker_compose.call_args.kwargs["liferay_env"]
        )

    def test_every_entry_the_stage_builds_is_rendered(self):
        stage_env = self._stage_env()
        # The branches this exercises, so the assertion below cannot pass on
        # an empty list.
        keys = {entry.split("=", 1)[0] for entry in stage_env}
        self.assertTrue(
            {
                "LIFERAY_HOME",
                UPGRADE,
                VHOST,
                "LIFERAY_WEB_PERIOD_SERVER_PERIOD_DISPLAY_PERIOD_NODE_PERIOD_NAME",
            }
            <= keys,
            stage_env,
        )
        rendered = self._rendered_env(stage_env)
        for entry in stage_env:
            key, value = entry.split("=", 1)
            self.assertEqual(rendered.get(key), value, entry)


if __name__ == "__main__":
    unittest.main()
