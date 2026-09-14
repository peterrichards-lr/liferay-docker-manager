"""Workspace `configs/<env>/` must land where Liferay reads it (LDM-#1692).

A Liferay Workspace keeps environment configuration as `configs/<environment>/`,
holding `portal-ext.properties`, an `osgi/configs/` tree and optionally
`deploy/`. PR #497 replaced the selective copy that understood that shape with a
wholesale directory mapping into `paths["configs"]` (`root/osgi/configs`), which
put every file one environment-directory too deep:

    configs/local/osgi/configs/x.config
      -> osgi/configs/local/osgi/configs/x.config     (never scanned)
    configs/local/portal-ext.properties
      -> osgi/configs/local/portal-ext.properties     (never read)

Measured against the unfixed code: `ls <project>/osgi/configs/*.config` matched
nothing, and a marker property placed in the workspace's `portal-ext.properties`
appeared only at that dead path.

The issue as originally filed said `portal-ext.properties` was unaffected because
`_hydrate_from_workspace` handled it. That was wrong -- nothing in
`workspace/hydration.py` mentions the file, and the copy in `<project>/files/`
seen while investigating was LDM's own generated one, not the workspace's. The
regression covered both halves.

Properties are **merged**, not copied over. The pre-refactor code did
`safe_copy(pe, paths["files"] / "portal-ext.properties")`, replacing the
project's file wholesale; `update_portal_ext` merges key by key, so anything LDM
wrote survives and LDM's later writes still win on the keys it owns.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from ldm_core.pipelines.base import PipelineContext
from ldm_core.pipelines.import_pipeline import VolumeSyncStage


class _Config:
    """Only the two members the sync uses, with real merge semantics."""

    def __init__(self):
        self.merged: dict = {}

    @staticmethod
    def _get_properties(content):
        props = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "!")) or "=" not in line:
                continue
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()
        return props

    def update_portal_ext(self, paths, updates):
        self.merged.update(updates)


class _Manager:
    def __init__(self, target_env="local"):
        self.args = SimpleNamespace(target_env=target_env)
        self.config = _Config()


class _Context(PipelineContext):
    def __init__(self, manager=None, **data):
        super().__init__(**data)
        self.manager = manager or _Manager()


class EnvironmentConfigsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.workspace = self.base / "workspace"
        self.project = self.base / "project"
        self.paths = {
            "root": self.project,
            "configs": self.project / "osgi" / "configs",
            "deploy": self.project / "deploy",
            "files": self.project / "files",
        }
        for p in self.paths.values():
            p.mkdir(parents=True, exist_ok=True)

    def _env(self, name="local"):
        d = self.workspace / "configs" / name
        (d / "osgi" / "configs").mkdir(parents=True, exist_ok=True)
        return d

    def _sync(self, manager=None):
        context = _Context(manager=manager)
        VolumeSyncStage._sync_environment_configs(context, self.workspace, self.paths)
        return context.manager


class OsgiConfigs(EnvironmentConfigsTestCase):
    def test_a_config_lands_in_liferays_scan_path(self):
        env = self._env()
        (env / "osgi" / "configs" / "com.example.Thing.config").write_text('a="b"\n')

        self._sync()

        self.assertTrue(
            (self.paths["configs"] / "com.example.Thing.config").is_file(),
            "Liferay scans osgi/configs/*.config -- anything deeper is never read",
        )

    def test_cfg_files_are_taken_too(self):
        env = self._env()
        (env / "osgi" / "configs" / "legacy.cfg").write_text("a=b\n")

        self._sync()

        self.assertTrue((self.paths["configs"] / "legacy.cfg").is_file())

    def test_no_environment_subdirectory_is_created(self):
        env = self._env()
        (env / "osgi" / "configs" / "com.example.Thing.config").write_text('a="b"\n')

        self._sync()

        self.assertFalse(
            (self.paths["configs"] / "local").exists(),
            "the environment directory must be flattened away, not reproduced",
        )


class PortalExtProperties(EnvironmentConfigsTestCase):
    def test_the_workspace_properties_are_merged(self):
        env = self._env()
        (env / "portal-ext.properties").write_text(
            "ldm.probe.marker=WORKSPACE\nsecond.key=2\n"
        )

        manager = self._sync()

        self.assertEqual(manager.config.merged.get("ldm.probe.marker"), "WORKSPACE")
        self.assertEqual(manager.config.merged.get("second.key"), "2")

    def test_merging_is_used_rather_than_replacing_the_file(self):
        """`safe_copy` over the file would flatten whatever LDM had written."""
        env = self._env()
        (env / "portal-ext.properties").write_text("a=1\n")
        target = self.paths["files"] / "portal-ext.properties"
        target.write_text("ldm.generated=keep-me\n")

        self._sync()

        self.assertIn(
            "keep-me",
            target.read_text(),
            "the project's own portal-ext.properties was overwritten",
        )

    def test_comments_and_blanks_are_not_properties(self):
        env = self._env()
        (env / "portal-ext.properties").write_text("# a comment\n\nreal.key=1\n")

        manager = self._sync()

        self.assertEqual(list(manager.config.merged), ["real.key"])


class TargetEnvironmentSelection(EnvironmentConfigsTestCase):
    def test_only_the_selected_environment_is_taken(self):
        local = self._env("local")
        uat = self._env("uat")
        (local / "osgi" / "configs" / "local-only.config").write_text('a="b"\n')
        (uat / "osgi" / "configs" / "uat-only.config").write_text('a="b"\n')

        self._sync()

        self.assertTrue((self.paths["configs"] / "local-only.config").is_file())
        self.assertFalse(
            (self.paths["configs"] / "uat-only.config").exists(),
            "--target-env was ignored; every environment was copied",
        )

    def test_target_env_is_honoured(self):
        uat = self._env("uat")
        (uat / "osgi" / "configs" / "uat-only.config").write_text('a="b"\n')

        self._sync(manager=_Manager(target_env="uat"))

        self.assertTrue((self.paths["configs"] / "uat-only.config").is_file())


class Deploy(EnvironmentConfigsTestCase):
    def test_the_environment_deploy_folder_is_taken(self):
        env = self._env()
        (env / "deploy").mkdir()
        (env / "deploy" / "marker.jar").write_text("jar")

        self._sync()

        self.assertTrue((self.paths["deploy"] / "marker.jar").is_file())


class Silence(EnvironmentConfigsTestCase):
    def test_a_workspace_with_no_configs_is_silent(self):
        self.workspace.mkdir(parents=True, exist_ok=True)

        self._sync()  # must not raise

        self.assertEqual(list(self.paths["configs"].iterdir()), [])

    def test_an_unknown_target_env_is_silent(self):
        env = self._env("local")
        (env / "osgi" / "configs" / "x.config").write_text('a="b"\n')

        self._sync(manager=_Manager(target_env="nosuchenv"))

        self.assertEqual(list(self.paths["configs"].iterdir()), [])


class TheWholesaleMappingIsGone(unittest.TestCase):
    """`configs` must not return to `structural_mappings`.

    That is the line that produced `osgi/configs/<env>/osgi/configs/`, and it
    looks harmless beside `deploy`/`files`/`scripts`, which genuinely are flat
    mirrors.
    """

    def test_configs_is_not_a_structural_mapping(self):
        import inspect

        src = inspect.getsource(VolumeSyncStage.execute)
        mapping = src[src.index("structural_mappings") :]
        mapping = mapping[: mapping.index("}")]

        self.assertNotIn(
            '"configs"',
            mapping,
            "configs is back in structural_mappings -- workspace OSGi "
            "configuration will land below Liferay's scan path again (LDM-#1692)",
        )
        for flat in ("deploy", "files", "scripts"):
            self.assertIn(f'"{flat}"', mapping, f"{flat} is a real flat mirror")


if __name__ == "__main__":
    unittest.main()
