"""Behavioural cover for ``ldm_core/workspace/hydration.py`` (LDM-#1515).

This is the artefact-seed path: it walks a Liferay workspace and lays the built
artefacts out inside the LDM project. Two shipped bugs this cycle (#1509, #1512)
lived in the seed path, and the pre-existing tests for this module asserted only
that ``safe_copy``/``safe_move`` *were called* -- which cannot distinguish "the
jar landed where Liferay looks for it" from "a jar was copied somewhere".

So nothing here is mocked. The workspace is real, the zips are real zips, and
every assertion is about **which file exists at which path with which bytes**
once the sync has run. No Docker or network is involved in this path at all.

Deliberately left uncovered, rather than covered hollowly:

- ``_sync_fragment_overrides``'s bare ``except Exception: pass``. Reaching it
  needs the copy itself to fail, which means either mocking ``safe_copy`` (an
  assertion about a mock, not about behaviour) or engineering a permission
  failure that will not reproduce on every platform CI runs on.
- The ``if not overwrite`` branch in step 3 of ``_sync_cx_artifact``. It is
  unreachable: the function already returns at the top when ``overwrite`` is
  false and ``dest_zip`` exists, so ``dest_zip`` can never exist by the time
  step 3 tests it again. No test can reach it because no caller can.
"""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.workspace import WorkspaceService


class _FakeManager(BaseHandler):
    """Minimal manager: real ``read_meta``/``write_meta``, fixed project path."""

    def __init__(self, project_path, args=None):
        args = args or SimpleNamespace(
            project="proj", non_interactive=True, verbose=False
        )
        super().__init__(args)
        self._project_path = project_path
        self._non_interactive = True
        self.cloud = MagicMock()

        from ldm_core.defaults import DefaultsManager

        self.defaults = DefaultsManager()

    @property
    def non_interactive(self):
        return self._non_interactive

    @non_interactive.setter
    def non_interactive(self, value):
        self._non_interactive = value

    def detect_project_path(self, project_id=None, for_init=False, fatal=True):
        return self._project_path


def _make_zip(path: Path, entries: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


class _HydrationCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)

        self.workspace = base / "workspace"
        self.workspace.mkdir()

        self.project = base / "project"
        self.manager = _FakeManager(self.project)
        self.service = WorkspaceService(self.manager)

        # Mirrors the subset of BaseHandler.setup_paths() this module consumes.
        self.paths = {
            "root": self.project,
            "cx": self.project / "osgi" / "client-extensions",
            "modules": self.project / "osgi" / "modules",
            "deploy": self.project / "deploy",
        }
        for directory in self.paths.values():
            directory.mkdir(parents=True, exist_ok=True)

    def hydrate(self, overwrite=True):
        return self.service._hydrate_from_workspace(
            self.workspace, self.paths, overwrite=overwrite
        )


class TestClientExtensionSync(_HydrationCase):
    def test_a_client_extension_zip_is_expanded_and_handed_to_liferay(self):
        source = _make_zip(
            self.workspace / "client-extensions" / "my-ext.zip",
            {
                "client-extension.yaml": "my-ext:\n  type: customElement\n",
                "src/index.js": "console.log('hi');",
            },
        )
        original_bytes = source.read_bytes()

        self.hydrate()

        # 1. The zip Liferay deploys must be in osgi/client-extensions, intact.
        deployed = self.paths["cx"] / "my-ext.zip"
        self.assertTrue(deployed.exists(), "zip never reached osgi/client-extensions")
        self.assertEqual(deployed.read_bytes(), original_bytes)

        # 2. The expanded tree Docker builds from must be at the project root.
        expanded = self.project / "client-extensions" / "my-ext"
        self.assertEqual(
            (expanded / "src" / "index.js").read_text(), "console.log('hi');"
        )

        # 3. The staging copy must be moved, not left behind: a stray zip beside
        #    the expanded folder gets re-synced on the next pass.
        self.assertFalse((self.project / "client-extensions" / "my-ext.zip").exists())

    def test_host_node_modules_are_pruned_from_the_build_context(self):
        _make_zip(
            self.workspace / "client-extensions" / "my-ext.zip",
            {
                "client-extension.yaml": "type: customElement\n",
                "node_modules/left-pad/index.js": "// host-built native binding",
                "src/index.js": "x",
            },
        )

        self.hydrate()

        expanded = self.project / "client-extensions" / "my-ext"
        self.assertTrue((expanded / "src" / "index.js").exists())
        self.assertFalse(
            (expanded / "node_modules").exists(),
            "host node_modules survived into the Docker build context",
        )

    def test_a_stale_expansion_is_cleared_rather_than_merged_into(self):
        stale = self.project / "client-extensions" / "my-ext"
        stale.mkdir(parents=True)
        (stale / "deleted-upstream.js").write_text("stale")

        _make_zip(
            self.workspace / "client-extensions" / "my-ext.zip",
            {"src/index.js": "fresh"},
        )

        self.hydrate()

        self.assertEqual((stale / "src" / "index.js").read_text(), "fresh")
        self.assertFalse(
            (stale / "deleted-upstream.js").exists(),
            "a file deleted upstream survived the re-expansion",
        )

    def test_dist_zips_under_a_client_extension_subfolder_are_picked_up(self):
        _make_zip(
            self.workspace / "client-extensions" / "my-ext" / "dist" / "my-ext.zip",
            {"src/index.js": "x"},
        )

        self.hydrate()

        self.assertTrue((self.paths["cx"] / "my-ext.zip").exists())

    def test_overwrite_replaces_an_already_deployed_client_extension(self):
        (self.paths["cx"] / "my-ext.zip").write_bytes(b"OLD")
        source = _make_zip(
            self.workspace / "client-extensions" / "my-ext.zip",
            {"src/index.js": "fresh"},
        )

        self.hydrate(overwrite=True)

        self.assertEqual(
            (self.paths["cx"] / "my-ext.zip").read_bytes(), source.read_bytes()
        )

    def test_no_overwrite_leaves_a_deployed_extension_and_its_expansion_alone(self):
        (self.paths["cx"] / "my-ext.zip").write_bytes(b"OLD")
        expanded = self.project / "client-extensions" / "my-ext"
        expanded.mkdir(parents=True)
        (expanded / "marker.txt").write_text("existing")

        _make_zip(
            self.workspace / "client-extensions" / "my-ext.zip",
            {"src/index.js": "fresh"},
        )

        self.hydrate(overwrite=False)

        self.assertEqual((self.paths["cx"] / "my-ext.zip").read_bytes(), b"OLD")
        self.assertEqual((expanded / "marker.txt").read_text(), "existing")
        self.assertFalse((expanded / "src").exists())

    def test_no_overwrite_skips_a_deployed_extension_before_doing_any_work(self):
        # The guard is at the top of the sync for a reason: an already-deployed
        # extension must not be re-expanded, and must not leave a staging copy
        # behind either. Asserting only "the deployed zip is unchanged" cannot
        # tell the guard apart from the redundant one at the end of the
        # function -- the absence of the build context can.
        (self.paths["cx"] / "my-ext.zip").write_bytes(b"OLD")
        _make_zip(
            self.workspace / "client-extensions" / "my-ext.zip",
            {"src/index.js": "fresh"},
        )

        self.hydrate(overwrite=False)

        self.assertEqual((self.paths["cx"] / "my-ext.zip").read_bytes(), b"OLD")
        self.assertFalse((self.project / "client-extensions" / "my-ext").exists())
        self.assertFalse((self.project / "client-extensions" / "my-ext.zip").exists())

    def test_no_overwrite_still_expands_an_extension_that_was_never_deployed(self):
        expanded = self.project / "client-extensions" / "my-ext"
        expanded.mkdir(parents=True)
        (expanded / "stale.txt").write_text("stale")

        _make_zip(
            self.workspace / "client-extensions" / "my-ext.zip",
            {"src/index.js": "fresh"},
        )

        self.hydrate(overwrite=False)

        # The zip is deployed, because nothing was there to preserve...
        self.assertTrue((self.paths["cx"] / "my-ext.zip").exists())
        # ...but an existing build context is left exactly as it was: under
        # no-overwrite the expansion is skipped entirely, not merged. Observed,
        # not inferred -- the first draft of this test asserted a merge and
        # failed.
        self.assertEqual([p.name for p in expanded.iterdir()], ["stale.txt"])

    def test_a_corrupt_client_extension_still_reaches_liferay(self):
        # Expansion is only needed for the Docker build context. A zip LDM
        # cannot read may still be one Liferay can deploy, so a bad expand must
        # report and continue rather than swallow the artefact.
        ce_dir = self.workspace / "client-extensions"
        ce_dir.mkdir(parents=True)
        (ce_dir / "broken.zip").write_bytes(b"not a zip at all")

        self.hydrate()

        self.assertEqual(
            (self.paths["cx"] / "broken.zip").read_bytes(), b"not a zip at all"
        )

    def test_non_ascii_artefact_names_survive_the_sync(self):
        # #1512 was a non-ASCII path bug in the neighbouring seed code; the
        # names travel through zipfile, shutil and os.rename here too.
        _make_zip(
            self.workspace / "client-extensions" / "café-ext.zip",
            {"src/índex.js": "olá"},
        )

        self.hydrate()

        self.assertTrue((self.paths["cx"] / "café-ext.zip").exists())
        self.assertEqual(
            (
                self.project / "client-extensions" / "café-ext" / "src" / "índex.js"
            ).read_text(encoding="utf-8"),
            "olá",
        )


class TestModuleAndThemeSync(_HydrationCase):
    def _build_artifact(self, folder, module, filename, content=b"JAR"):
        path = self.workspace / folder / module / "build" / "libs" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_built_jars_and_wars_land_in_osgi_modules(self):
        self._build_artifact("modules", "my-mod", "my-mod.jar", b"MODULE-BYTES")
        self._build_artifact("themes", "my-theme", "my-theme.war", b"THEME-BYTES")

        self.hydrate()

        self.assertEqual(
            (self.paths["modules"] / "my-mod.jar").read_bytes(), b"MODULE-BYTES"
        )
        self.assertEqual(
            (self.paths["modules"] / "my-theme.war").read_bytes(), b"THEME-BYTES"
        )

    def test_sources_javadoc_and_test_jars_are_not_deployed(self):
        self._build_artifact("modules", "my-mod", "my-mod.jar")
        for noise in ("my-mod-sources.jar", "my-mod-javadoc.jar", "my-mod-tests.jar"):
            self._build_artifact("modules", "my-mod", noise)

        self.hydrate()

        deployed = sorted(p.name for p in self.paths["modules"].iterdir())
        self.assertEqual(deployed, ["my-mod.jar"])

    def test_overwrite_replaces_a_previously_deployed_jar(self):
        (self.paths["modules"] / "my-mod.jar").write_bytes(b"OLD")
        self._build_artifact("modules", "my-mod", "my-mod.jar", b"NEW")

        self.hydrate(overwrite=True)

        self.assertEqual((self.paths["modules"] / "my-mod.jar").read_bytes(), b"NEW")

    def test_no_overwrite_preserves_what_is_already_deployed(self):
        (self.paths["modules"] / "my-mod.jar").write_bytes(b"OLD")
        self._build_artifact("modules", "my-mod", "my-mod.jar", b"NEW")

        self.hydrate(overwrite=False)

        self.assertEqual((self.paths["modules"] / "my-mod.jar").read_bytes(), b"OLD")


class TestFragmentRouting(_HydrationCase):
    def test_a_fragment_zip_goes_to_deploy_and_a_plain_zip_goes_to_client_extensions(
        self,
    ):
        # The discriminator is the marker file inside the archive, not the path.
        _make_zip(
            self.workspace / "fragments" / "real-fragment.zip",
            {
                "liferay-deploy-fragments.json": "{}",
                "collection/fragment.html": "<div/>",
            },
        )
        _make_zip(
            self.workspace / "fragments" / "misfiled-ext.zip",
            {"client-extension.yaml": "type: customElement\n"},
        )

        self.hydrate()

        self.assertTrue((self.paths["deploy"] / "real-fragment.zip").exists())
        self.assertFalse((self.paths["cx"] / "real-fragment.zip").exists())

        self.assertTrue((self.paths["cx"] / "misfiled-ext.zip").exists())
        self.assertFalse((self.paths["deploy"] / "misfiled-ext.zip").exists())

    def test_no_overwrite_preserves_an_already_deployed_fragment(self):
        (self.paths["deploy"] / "real-fragment.zip").write_bytes(b"OLD")
        _make_zip(
            self.workspace / "fragments" / "real-fragment.zip",
            {"liferay-deploy-fragments.json": "{}"},
        )

        self.hydrate(overwrite=False)

        self.assertEqual(
            (self.paths["deploy"] / "real-fragment.zip").read_bytes(), b"OLD"
        )

    def test_a_corrupt_zip_does_not_abort_the_rest_of_the_sync(self):
        (self.workspace / "fragments").mkdir(parents=True)
        (self.workspace / "fragments" / "broken.zip").write_bytes(b"not a zip at all")
        module = self.workspace / "modules" / "my-mod" / "build" / "libs" / "my-mod.jar"
        module.parent.mkdir(parents=True)
        module.write_bytes(b"JAR")

        self.assertTrue(self.hydrate())

        self.assertTrue((self.paths["modules"] / "my-mod.jar").exists())


class TestFragmentOverrides(_HydrationCase):
    def test_overrides_are_copied_into_the_project_ldm_directory(self):
        payload = json.dumps({"my-fragment": {"html": "override.html"}})
        source = self.workspace / ".ldm" / "fragment-overrides.json"
        source.parent.mkdir(parents=True)
        source.write_text(payload, encoding="utf-8")

        self.hydrate()

        dest = self.project / ".ldm" / "fragment-overrides.json"
        self.assertEqual(
            json.loads(dest.read_text(encoding="utf-8")), json.loads(payload)
        )

    def test_the_configs_location_is_also_honoured(self):
        source = self.workspace / "configs" / "fragment-overrides.json"
        source.parent.mkdir(parents=True)
        source.write_text('{"from": "configs"}', encoding="utf-8")

        self.hydrate()

        dest = self.project / ".ldm" / "fragment-overrides.json"
        self.assertEqual(json.loads(dest.read_text())["from"], "configs")

    def test_no_overwrite_preserves_existing_overrides(self):
        dest = self.project / ".ldm" / "fragment-overrides.json"
        dest.parent.mkdir(parents=True)
        dest.write_text('{"from": "project"}', encoding="utf-8")

        source = self.workspace / ".ldm" / "fragment-overrides.json"
        source.parent.mkdir(parents=True)
        source.write_text('{"from": "workspace"}', encoding="utf-8")

        self.hydrate(overwrite=False)

        self.assertEqual(json.loads(dest.read_text())["from"], "project")


class TestCloudHydrationPrompt(_HydrationCase):
    """``_prompt_cloud_hydration`` decides *whether* to pull cloud data.

    ``cmd_cloud_fetch`` itself talks to the Liferay Cloud CLI over the network,
    so it is the boundary and stands in as a MagicMock. Everything on this side
    of it -- the LCP detection, the two-phase fetch order, ``--no-env-sync``,
    and the ``cloud_env_id`` that is persisted to the project's meta file on
    disk -- is exercised for real.
    """

    def _make_lcp_workspace(self):
        (self.workspace / "liferay").mkdir(parents=True, exist_ok=True)
        (self.workspace / "liferay" / "LCP.json").write_text('{"id": "liferay"}')
        return self.workspace

    def test_a_plain_workspace_never_reaches_the_cloud(self):
        self.manager.args.hydrate_from = "prd"

        self.service._prompt_cloud_hydration(self.workspace, "proj")

        self.manager.cloud.cmd_cloud_fetch.assert_not_called()

    def test_hydrate_from_persists_the_environment_and_fetches_in_two_phases(self):
        source = self._make_lcp_workspace()
        self.manager.args.hydrate_from = "uat"
        self.manager.args.no_env_sync = False

        self.service._prompt_cloud_hydration(source, "proj")

        # The chosen environment must survive on disk -- later cloud commands
        # read it back rather than re-prompting.
        self.assertEqual(
            self.manager.read_meta(self.project).get("cloud_env_id"), "uat"
        )

        calls = self.manager.cloud.cmd_cloud_fetch.call_args_list
        self.assertEqual(len(calls), 2)
        # Env vars first: they must be in place before the restore boots.
        self.assertTrue(calls[0].kwargs["sync_env"])
        self.assertFalse(calls[0].kwargs["download"])
        self.assertFalse(calls[1].kwargs["sync_env"])
        self.assertTrue(calls[1].kwargs["download"])
        self.assertTrue(calls[1].kwargs["restore"])
        # The outer import owns startup; this must not boot the stack early.
        self.assertTrue(calls[1].kwargs["no_run"])

    def test_no_env_sync_skips_the_environment_phase_only(self):
        source = self._make_lcp_workspace()
        self.manager.args.hydrate_from = "prd"
        self.manager.args.no_env_sync = True

        self.service._prompt_cloud_hydration(source, "proj")

        calls = self.manager.cloud.cmd_cloud_fetch.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0].kwargs["sync_env"])
        self.assertTrue(calls[0].kwargs["download"])

    def test_a_non_interactive_run_is_never_prompted(self):
        source = self._make_lcp_workspace()
        self.manager.args.hydrate_from = None
        self.manager.non_interactive = True

        from unittest.mock import patch

        # Answering "yes" for it would be worse than staying local: an
        # unattended run must not start pulling a production database.
        with (
            patch(
                "ldm_core.workspace.hydration.UI.confirm", return_value=True
            ) as confirm,
            patch("ldm_core.workspace.hydration.UI.ask", return_value="prd"),
        ):
            self.service._prompt_cloud_hydration(source, "proj")

        confirm.assert_not_called()
        self.manager.cloud.cmd_cloud_fetch.assert_not_called()

    def test_declining_the_prompt_stays_local(self):
        source = self._make_lcp_workspace()
        self.manager.args.hydrate_from = None
        self.manager.non_interactive = False

        from unittest.mock import patch

        with (
            patch("ldm_core.workspace.hydration.UI.confirm", return_value=False),
            patch("ldm_core.workspace.hydration.UI.ask", return_value="prd"),
        ):
            self.service._prompt_cloud_hydration(source, "proj")

        self.manager.cloud.cmd_cloud_fetch.assert_not_called()

    def test_accepting_the_prompt_uses_the_environment_the_user_typed(self):
        source = self._make_lcp_workspace()
        self.manager.args.hydrate_from = None
        self.manager.args.no_env_sync = True
        self.manager.non_interactive = False

        from unittest.mock import patch

        with (
            patch("ldm_core.workspace.hydration.UI.confirm", return_value=True),
            patch("ldm_core.workspace.hydration.UI.ask", return_value=" uat "),
        ):
            self.service._prompt_cloud_hydration(source, "proj")

        self.assertEqual(
            self.manager.read_meta(self.project).get("cloud_env_id"), "uat"
        )
        self.manager.cloud.cmd_cloud_fetch.assert_called_once()
        self.assertEqual(
            self.manager.cloud.cmd_cloud_fetch.call_args.kwargs["env_id"], "uat"
        )

    def test_an_unresolvable_project_never_reaches_the_cloud(self):
        source = self._make_lcp_workspace()
        self.manager.args.hydrate_from = "prd"
        self.manager._project_path = None

        self.service._prompt_cloud_hydration(source, "proj")

        self.manager.cloud.cmd_cloud_fetch.assert_not_called()

    def test_a_failed_cloud_fetch_degrades_to_local_instead_of_exiting(self):
        source = self._make_lcp_workspace()
        self.manager.args.hydrate_from = "prd"
        self.manager.args.no_env_sync = True
        self.manager.cloud.cmd_cloud_fetch.side_effect = SystemExit(1)

        # SystemExit here would kill the enclosing `ldm import`, losing a
        # workspace that had already been laid down.
        self.service._prompt_cloud_hydration(source, "proj")


if __name__ == "__main__":
    unittest.main()
