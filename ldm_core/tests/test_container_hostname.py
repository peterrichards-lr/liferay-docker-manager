"""A supported, persisted Liferay container hostname (LDM-#2445).

Docker defaults the hostname to the container ID, which changes on every
recreate, and host-locked Marketplace activation keys are checked against it.
These tests follow the value across each seam it crosses: the flag into meta
(`ConfigResolutionStage`), meta into the rendered compose
(`write_docker_compose`), the running container back against meta
(`verify_container_hostname`, called from `ExecutionStage`), and the
`config container-hostname` subcommand from the real parser to project meta.

No real Docker: every `docker inspect` is stubbed at `DockerService`.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import yaml

from ldm_core.handlers.base import BaseHandler
from ldm_core.pipelines.run import (
    ConfigResolutionStage,
    ExecutionStage,
    RunPipelineContext,
    _verify_container_hostname,
)
from ldm_core.runtime import container_hostname as ch
from ldm_core.utils import read_meta, resolve_meta_file_path, write_meta

INVALID = ["Bad_Name!", "a" * 70, "a.b", "-x", "x-", "", "UPPER"]


def _write_project(root: Path, meta: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    write_meta(resolve_meta_file_path(root), meta)


def _read_project(root: Path) -> dict:
    return read_meta(resolve_meta_file_path(root))


def _register(projects: dict) -> None:
    """Writes a registry under the test's LDM_HOME (set by conftest)."""
    from ldm_core.constants import REGISTRY_FILE
    from ldm_core.utils import get_actual_home

    reg = get_actual_home() / ".ldm" / REGISTRY_FILE
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps(projects))


class Validation(unittest.TestCase):
    def test_valid_labels_are_accepted(self):
        for name in ("liferay", "dsr-01", "a", "a" * 63, "0node"):
            with self.subTest(name=name):
                self.assertTrue(ch.validate(name))

    def test_invalid_labels_are_refused(self):
        for name in INVALID:
            with self.subTest(name=name):
                self.assertFalse(ch.validate(name))


class TheComposerRendersIt(unittest.TestCase):
    """Starts at the real `write_docker_compose` and reads the YAML it writes."""

    def setUp(self):
        from ldm_core.defaults import DefaultsManager
        from ldm_core.handlers.composer import ComposerService
        from ldm_core.tests.test_composer import MockComposerManager
        from ldm_core.tests.test_workspace import MockWorkspaceManager

        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name) / "proj"
        root.mkdir()
        self.paths = BaseHandler.setup_paths(MockWorkspaceManager(), root)
        manager = MockComposerManager()
        manager.defaults = DefaultsManager()  # type: ignore[assignment]
        self.composer = ComposerService(manager)

    def tearDown(self):
        self._td.cleanup()

    def _liferay_service(self, meta_extra):
        from ldm_core.config import TargetNode

        meta = {
            "container_name": "proj",
            "tag": "2026.q1.0-lts",
            "host_name": "localhost",
            "db_type": "postgresql",
            **meta_extra,
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
            self.composer.write_docker_compose(self.paths, meta)
        compose = yaml.safe_load(write.call_args.args[1])
        return compose["services"]["liferay"]

    def test_the_hostname_is_on_the_liferay_service(self):
        service = self._liferay_service({"container_hostname": "liferay"})
        self.assertEqual(service.get("hostname"), "liferay")

    def test_no_hostname_when_none_is_configured(self):
        self.assertNotIn("hostname", self._liferay_service({}))

    def test_a_scaled_stack_warns_and_skips_it(self):
        with patch("ldm_core.handlers.composer.UI.warning") as warn:
            service = self._liferay_service(
                {"container_hostname": "liferay", "scale_liferay": "2"}
            )
        self.assertNotIn("hostname", service)
        self.assertTrue(
            any("liferay" in str(c.args[0]) for c in warn.call_args_list),
            warn.call_args_list,
        )


class TheFlagReachesMeta(unittest.TestCase):
    """Drives the real `ConfigResolutionStage.execute`, flag on `args` only."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name) / "proj"
        self.root.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def _execute(self, flag, meta):
        manager = MagicMock()
        manager.non_interactive = True
        manager.verbose = False
        manager.args.sidecar = False
        manager.args.database_mode = None
        manager.args.search_mode = None
        manager.args.reindex = False
        manager.args.container_hostname = flag
        manager.defaults.get.side_effect = lambda _k, default=None: default
        context = RunPipelineContext(
            manager, project_id="proj", paths={"root": self.root}
        )
        context.set("project_meta", dict(meta))
        ConfigResolutionStage().execute(context)
        return context.get("project_meta")

    def test_the_flag_is_saved_and_a_later_run_without_it_keeps_it(self):
        base = {"container_name": "proj", "tag": "2026.q1.12-lts"}
        first = self._execute("liferay", base)
        self.assertEqual(first.get("container_hostname"), "liferay")

        second = self._execute(None, first)
        self.assertEqual(second.get("container_hostname"), "liferay")

    def test_the_flag_replaces_an_earlier_value(self):
        meta = {"container_name": "proj", "tag": "2026.q1.12-lts"}
        meta["container_hostname"] = "old"
        self.assertEqual(self._execute("new", meta).get("container_hostname"), "new")

    def test_an_invalid_flag_is_refused_before_meta_is_touched(self):
        for name in ("Bad_Name!", "a" * 70, "a.b", "-x"):
            with self.subTest(name=name):
                meta = {"project_name": "proj"}
                with patch(
                    "ldm_core.runtime.container_hostname.UI.die",
                    side_effect=SystemExit(1),
                ) as die:
                    with self.assertRaises(SystemExit):
                        ConfigResolutionStage._apply_container_hostname(
                            MagicMock(args=MagicMock(container_hostname=name)),
                            meta,
                            self.root,
                        )
                self.assertEqual(die.call_args.kwargs.get("exit_code"), 1)
                self.assertNotIn("container_hostname", meta)

    def test_a_duplicate_across_projects_warns_and_names_the_other(self):
        other = Path(self._td.name) / "other"
        _write_project(other, {"container_hostname": "liferay"})
        _register({"other": {"path": str(other)}, "proj": str(self.root)})

        meta: dict = {}
        with patch("ldm_core.runtime.container_hostname.UI.warning") as warn:
            ConfigResolutionStage._apply_container_hostname(
                MagicMock(args=MagicMock(container_hostname="liferay")),
                meta,
                self.root,
            )
        self.assertEqual(meta["container_hostname"], "liferay")
        warn.assert_called_once()
        self.assertIn("other", warn.call_args.args[0])

    def test_no_warning_for_the_project_itself(self):
        _write_project(self.root, {"container_hostname": "liferay"})
        _register({"proj": str(self.root)})
        with patch("ldm_core.runtime.container_hostname.UI.warning") as warn:
            ch.warn_if_shared("liferay", self.root)
        warn.assert_not_called()


class TheRunningContainerIsChecked(unittest.TestCase):
    META: ClassVar[dict] = {"container_name": "proj", "container_hostname": "liferay"}

    def _verify(self, actual, meta=None):
        with (
            patch(
                "ldm_core.docker_service.DockerService.container_hostname",
                return_value=actual,
            ) as read,
            patch(
                "ldm_core.runtime.container_hostname.UI.die",
                side_effect=SystemExit(3),
            ) as die,
            patch("ldm_core.runtime.container_hostname.UI.warning") as warn,
        ):
            try:
                ch.verify_container_hostname(dict(meta or self.META), None)
            except SystemExit:
                pass
        return read, die, warn

    def test_a_different_hostname_dies_with_exit_3(self):
        _, die, _ = self._verify("not-liferay")
        die.assert_called_once()
        self.assertEqual(die.call_args.kwargs.get("exit_code"), 3)
        self.assertIn("not-liferay", die.call_args.args[0])

    def test_a_matching_hostname_passes(self):
        _, die, warn = self._verify("liferay")
        die.assert_not_called()
        warn.assert_not_called()

    def test_unreadable_warns_rather_than_dies(self):
        _, die, warn = self._verify(None)
        die.assert_not_called()
        warn.assert_called_once()

    def test_nothing_is_inspected_without_a_configured_hostname(self):
        read, die, _ = self._verify("anything", {"container_name": "proj"})
        read.assert_not_called()
        die.assert_not_called()

    def test_a_scaled_project_is_not_checked(self):
        read, _, _ = self._verify("x", {**self.META, "scale_liferay": "2"})
        read.assert_not_called()

    def test_the_pipeline_wrapper_reaches_it(self):
        manager = MagicMock(target=None, dry_run=False)
        context = MagicMock()
        context.get.return_value = "proj"
        with patch(
            "ldm_core.runtime.container_hostname.verify_container_hostname"
        ) as verify:
            _verify_container_hostname(manager, context, dict(self.META))
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[0]["container_hostname"], "liferay")


class TheExecutionStageChecksIt(unittest.TestCase):
    """Drives the real `ExecutionStage.execute` to the post-bring-up check.

    The helper is proven above; this proves the stage that ships calls it.
    Harness follows `test_readiness_exit_code.py`: compose and readiness are
    answered by the mocked manager, and only `DockerService.container_hostname`
    reports a value.
    """

    def setUp(self):
        self.context = RunPipelineContext(MagicMock())
        self.context.set("project_id", "proj")
        self.context.set("is_new_project", False)
        self.context.set("dry_run", False)
        self.context.set("no_up", False)
        self.context.set("paths", {"root": MagicMock()})
        self.context.set(
            "project_meta",
            {"container_name": "proj", "container_hostname": "liferay"},
        )
        manager = self.context.manager
        manager.non_interactive = True
        manager.target = None
        # Real Falses: an unset attribute on a MagicMock is truthy and would
        # send the stage down the dry-run or follow branch instead.
        for flag in (
            "no_wait",
            "follow",
            "force_recreate",
            "rebuild",
            "quiet",
            "dry_run",
            "no_up",
        ):
            setattr(manager.args, flag, False)
        manager.dry_run = False
        manager.args.timeout = 900
        manager.get_container_status.return_value = "healthy"
        manager.runtime._wait_for_ready.return_value = True

    def _run_stage(self, actual):
        with (
            patch("ldm_core.config.sync_project_to_target"),
            patch("ldm_core.pipelines.run.offer_shared_database_tip"),
            patch("ldm_core.pipelines.run.time.sleep"),
            patch(
                "ldm_core.docker_service.DockerService.container_hostname",
                return_value=actual,
            ) as read,
            patch("ldm_core.pipelines.run.UI.die", side_effect=SystemExit) as died,
        ):
            try:
                ExecutionStage().execute(self.context)
            except SystemExit:
                pass
        return read, died

    def test_a_mismatched_hostname_exits_3(self):
        read, died = self._run_stage("not-liferay")
        read.assert_called_once()
        died.assert_called_once()
        self.assertEqual(died.call_args.kwargs.get("exit_code"), 3)
        self.assertIn("not-liferay", died.call_args.args[0])

    def test_a_matching_hostname_does_not_die(self):
        """Control: the same harness with the right value boots cleanly, so the
        exit above is the hostname check and not the harness."""
        read, died = self._run_stage("liferay")
        read.assert_called_once()
        died.assert_not_called()


class TheConfigSubcommand(unittest.TestCase):
    """`ldm config container-hostname` from the real parser to project meta."""

    def setUp(self):
        from ldm_core.handlers.config import ConfigService

        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name) / "demo"
        _write_project(self.root, {"container_name": "demo"})

        inner = MagicMock()
        inner.detect_project_path = lambda _pid=None: self.root
        inner.read_meta = _read_project
        inner.write_meta = MagicMock(
            side_effect=lambda root, meta: write_meta(
                resolve_meta_file_path(root), meta
            )
        )
        self.inner = inner
        self.manager = MagicMock()
        self.manager.config = ConfigService(inner)

    def tearDown(self):
        self._td.cleanup()

    def _run(self, *argv):
        from ldm_core.cli import _build_command_map, get_parser, preprocess_args

        args = get_parser()[0].parse_args(preprocess_args(list(argv)))
        self.assertEqual(
            (args.command, args.subcommand), ("config", "container-hostname")
        )
        _build_command_map(args, self.manager)[("config", "container-hostname")]()
        return args

    def test_set_writes_project_meta_not_ldmrc(self):
        from ldm_core.utils import get_actual_home

        ldmrc = get_actual_home() / ".ldmrc"
        before = ldmrc.read_text() if ldmrc.exists() else None

        args = self._run("config", "container-hostname", "liferay", "demo")

        self.assertEqual(args.value, "liferay")
        self.assertEqual(_read_project(self.root).get("container_hostname"), "liferay")
        after = ldmrc.read_text() if ldmrc.exists() else None
        self.assertEqual(before, after, "~/.ldmrc was written")

    def test_unset_removes_it(self):
        _write_project(self.root, {"container_name": "demo", "container_hostname": "x"})
        self._run("config", "container-hostname", "--unset", "demo")
        self.assertNotIn("container_hostname", _read_project(self.root))

    def test_no_value_prints_the_current_one(self):
        _write_project(self.root, {"container_name": "demo", "container_hostname": "x"})
        with patch("builtins.print") as out:
            self._run("config", "container-hostname")
        out.assert_called_once_with("x")
        self.inner.write_meta.assert_not_called()

    def test_an_invalid_value_is_refused_before_anything_is_written(self):
        for name in ("Bad_Name!", "a.b", "a" * 70):
            with self.subTest(name=name):
                with patch(
                    "ldm_core.runtime.container_hostname.UI.die",
                    side_effect=SystemExit(1),
                ):
                    with self.assertRaises(SystemExit):
                        self.manager.config.cmd_container_hostname(name, "demo")
                self.inner.write_meta.assert_not_called()
                self.assertNotIn("container_hostname", _read_project(self.root))


if __name__ == "__main__":
    unittest.main()
