import importlib
import inspect
import os
import pkgutil
import re
import sys
import tempfile
import typing
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import ldm_core.handlers
from ldm_core.manager import LiferayManager


class TestArchitecturalContracts(unittest.TestCase):
    """
    Verification suite to ensure core LDM architectural mandates are preserved.
    These tests verify the OUTPUT of the orchestration engine (Compose, Portal-Ext).
    """

    def test_handler_constructor_contract(self):
        """Contract: Every specialized handler class MUST accept an 'args' object and store UI flags."""
        handler_package = ldm_core.handlers

        class MockArgs:
            verbose = True
            non_interactive = True

        mock_args = MockArgs()

        # Iterate through all modules in the handlers package
        for _loader, module_name, is_pkg in pkgutil.walk_packages(
            handler_package.__path__, handler_package.__name__ + "."
        ):
            if is_pkg:
                continue

            module = importlib.import_module(module_name)

            # Find all classes defined in this module
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # We only care about classes defined in the handler modules themselves (not imports)
                if obj.__module__ == module_name and name.endswith("Handler"):
                    try:
                        # 1. Verification: Instantiation
                        instance: typing.Any = obj(mock_args)  # type: ignore[call-arg]
                        self.assertIsNotNone(
                            instance, f"Failed to instantiate {name} in {module_name}"
                        )

                        # 2. Verification: Attribute Storage (Mandate: Consistency)
                        for attr in ["args", "verbose", "non_interactive"]:
                            self.assertTrue(
                                hasattr(instance, attr),
                                f"Architectural Violation: Handler '{name}' in {module_name} is missing mandatory attribute '{attr}'.",
                            )

                        # 3. Verification: Value Integrity
                        self.assertTrue(
                            instance.verbose,
                            f"Handler '{name}' did not correctly capture 'verbose' flag.",
                        )
                        self.assertTrue(
                            instance.non_interactive,
                            f"Handler '{name}' did not correctly capture 'non_interactive' flag.",
                        )

                    except TypeError as e:
                        self.fail(
                            f"Handler Constructor Contract Violation: {module_name}.{name} failed instantiation with args. Error: {e}"
                        )

    def setUp(self):
        from unittest.mock import patch

        # LDM-#1409: the shared-search branch of sync_common_assets runs
        # `docker inspect -f {{.Config.Image}} liferay-search-global` through
        # the module-scope run_command in handlers/config.py. The tests patch
        # `self.manager.run_command`, a different function, so it never reached
        # that call and a contract test about compose output was querying the
        # developer's real daemon. None is the "no shared search container"
        # branch these tests already assume.
        config_run_patcher = patch(
            "ldm_core.handlers.config.run_command", return_value=None
        )
        config_run_patcher.start()
        self.addCleanup(config_run_patcher.stop)

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

        # Patch get_compose_cmd to avoid dependencies on external docker bin in unit tests
        self.patcher_compose = patch(
            "ldm_core.runtime.orchestration.get_compose_cmd",
            return_value=["docker", "compose"],
        )
        self.mock_compose = self.patcher_compose.start()

        # Setup a dummy project
        self.project_path = self.root / "contract-test"
        self.project_path.mkdir()
        (self.project_path / "files").mkdir()
        (self.project_path / "deploy").mkdir()
        (self.project_path / "osgi" / "configs").mkdir(parents=True)

        # Mock args
        class Args:
            project = "contract-test"
            verbose = False
            non_interactive = True
            command = "run"

        self.manager = LiferayManager(Args())

    def tearDown(self):
        self.patcher_compose.stop()
        self.tmp_dir.cleanup()

    def test_docker_compose_labels_mandate(self):
        """Mandate: Every Liferay container MUST have the LDM project label for 'ldm status' and 'ldm prune'."""
        paths = self.manager.setup_paths(self.project_path)
        meta = {
            "container_name": "contract-test",
            "tag": "2025.q1.0",
            "host_name": "contract.local",
        }

        self.manager.composer.write_docker_compose(paths, meta)

        compose_content = yaml.safe_load(paths["compose"].read_text())
        liferay_labels = compose_content["services"]["liferay"].get("labels", [])

        # Check for the mandatory project label
        self.assertIn(
            "com.liferay.ldm.project=contract-test",
            liferay_labels,
            "CRITICAL: Mandatory Docker label 'com.liferay.ldm.project' is missing!",
        )

    def test_portal_ext_domain_alignment_mandate(self):
        """Mandate: Liferay MUST be configured to trust the proxy and identify itself when using custom domains."""
        paths = self.manager.setup_paths(self.project_path)
        meta = {
            "container_name": "contract-test",
            "tag": "2025.q1.0",
            "host_name": "contract.local",
        }

        # Trigger the sync logic (which handles domain alignment)
        from unittest.mock import patch

        with patch.object(self.manager, "run_command"):
            with patch.object(self.manager.infra, "setup_infrastructure"):
                with patch.object(
                    self.manager.composer, "write_docker_compose"
                ) as mock_write:
                    self.manager.runtime.cmd_run(
                        project_id="redline-domain",
                        no_up=True,
                        paths=paths,
                        project_meta=meta,
                    )

                    # Verify that environment variables were passed to write_docker_compose
                    # It might be in call_args.args[2] or call_args.kwargs['liferay_env']
                    args, kwargs = mock_write.call_args
                    passed_env = kwargs.get("liferay_env") or args[2]

                    # Verify domain alignment env vars are present
                    self.assertTrue(
                        any(
                            "LIFERAY_WEB_PERIOD_SERVER_PERIOD_DISPLAY_PERIOD_NODE_PERIOD_NAME=true"
                            in e
                            for e in passed_env
                        ),
                        "Mandate Loss: Liferay is not configured to display node name for custom domains.",
                    )
                    self.assertTrue(
                        any(
                            "LIFERAY_REDIRECT_PERIOD_URL_PERIOD_IPS_PERIOD_ALLOWED="
                            in e
                            for e in passed_env
                        ),
                        "Mandate Loss: Liferay is not configured to allow redirects from the proxy.",
                    )

    def test_redline_database_in_properties_mandate(self):
        """Redline 1: ALL Database/JDBC settings MUST live in portal-ext.properties for case-integrity."""
        paths = self.manager.setup_paths(self.project_path)
        meta = {
            "container_name": "redline-db",
            "tag": "2026.q1.4",
            "db_type": "mysql",
            "host_name": "localhost",
        }

        # Trigger sync
        from unittest.mock import patch

        with patch.object(self.manager, "run_command"):
            with patch.object(self.manager.infra, "setup_infrastructure"):
                self.manager.runtime.cmd_run(
                    project_id="redline-database",
                    no_up=True,
                    paths=paths,
                    project_meta=meta,
                )

        # 1. POSITIVE: Verify it IS in portal-ext.properties
        pe_content = (paths["files"] / "portal-ext.properties").read_text()
        self.assertIn(
            "jdbc.default.driverClassName=org.mariadb.jdbc.Driver", pe_content
        )
        self.assertIn(
            "hibernate.dialect=org.hibernate.dialect.MariaDB103Dialect", pe_content
        )

        # 2. NEGATIVE: Verify it is NOT in environment variables
        compose_content = yaml.safe_load(paths["compose"].read_text())
        liferay_env = compose_content["services"]["liferay"].get("environment", [])

        for env in liferay_env:
            self.assertFalse(
                "LIFERAY_JDBC_PERIOD_" in env and "DRIVER_CLASS_NAME" in env,
                "REDLINE VIOLATION: Database driver found in environment variables. MUST be in portal-ext.properties.",
            )

    def test_redline_search_in_env_mandate(self):
        """Redline 2: ALL Search/Elasticsearch settings MUST live in Env Vars or .config, NEVER portal-ext."""
        paths = self.manager.setup_paths(self.project_path)
        meta = {
            "container_name": "redline-search",
            "tag": "2026.q1.4",
            "use_shared_search": "true",
            "host_name": "localhost",
        }

        # Trigger sync
        from unittest.mock import patch

        with patch.object(self.manager, "run_command"):
            with patch.object(self.manager.infra, "setup_infrastructure"):
                self.manager.runtime.cmd_run(
                    project_id="redline-search",
                    no_up=True,
                    paths=paths,
                    project_meta=meta,
                )

        # 1. POSITIVE: Verify it IS in environment variables
        compose_content = yaml.safe_load(paths["compose"].read_text())
        liferay_env = compose_content["services"]["liferay"].get("environment", [])
        self.assertTrue(
            any(
                "LIFERAY_ELASTICSEARCH_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=true"
                in e
                for e in liferay_env
            ),
            "REDLINE FAILURE: Sidecar disable variable missing from environment.",
        )

        # 2. NEGATIVE: Verify it is NOT in portal-ext.properties
        pe_content = (paths["files"] / "portal-ext.properties").read_text()
        self.assertNotIn(
            "elasticsearch.sidecar.enabled",
            pe_content,
            "REDLINE VIOLATION: Search settings found in portal-ext.properties. MUST be in Env Vars or .config.",
        )

    @patch("ldm_core.handlers.config.ConfigService.get_samples_root")
    def test_get_samples_root_delegation_mandate(self, mock_get):
        """Mandate: ConfigService.get_samples_root correctly retrieves the path."""
        mock_get.return_value = Path("/tmp/mock_samples")
        samples_root = self.manager.config.get_samples_root()
        mock_get.assert_called_once()
        self.assertEqual(samples_root, Path("/tmp/mock_samples"))

    def test_cli_preprocess_gating_contracts(self):  # noqa: C901
        """Mandate: All registered CLI subparsers/commands MUST be synchronized with preprocess_args."""
        import argparse
        import ast
        import inspect

        import ldm_core.cli as cli_module
        from ldm_core.cli import get_parser

        # 1. Extract sets and lists from AST
        source = inspect.getsource(cli_module)
        tree = ast.parse(source)

        all_cmds_val = set()
        subcmds_val = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "all_cmds":
                            if isinstance(node.value, ast.Set):
                                all_cmds_val = {
                                    elt.value
                                    for elt in node.value.elts
                                    if isinstance(elt, ast.Constant)
                                }
                        elif target.id == "subcmds":
                            if isinstance(node.value, ast.List):
                                subcmds_val = [
                                    elt.value
                                    for elt in node.value.elts
                                    if isinstance(elt, ast.Constant)
                                ]

        self.assertTrue(
            all_cmds_val, "Failed to parse all_cmds from preprocess_args source."
        )
        self.assertTrue(
            subcmds_val, "Failed to parse subcmds from preprocess_args source."
        )

        # 2. Extract registered choices from the Parser
        parser, _ = get_parser()

        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for choice, subparser in action.choices.items():
                    # The namespace command itself must be recognized (e.g. 'config', 'system')
                    self.assertIn(
                        choice,
                        all_cmds_val,
                        f"Top-level namespace/command '{choice}' is not registered in all_cmds in preprocess_args!",
                    )

                    # Inspect nested subparsers (e.g. subcommands of config, system, etc.)
                    for sub_action in subparser._actions:
                        if isinstance(sub_action, argparse._SubParsersAction):
                            for sub_choice in sub_action.choices:
                                # Every nested subcommand MUST be registered in all_cmds
                                self.assertIn(
                                    sub_choice,
                                    all_cmds_val,
                                    f"Subcommand '{sub_choice}' under namespace '{choice}' is not registered in all_cmds in preprocess_args!",
                                )

                                # For 'config' namespace, it MUST also be in the subcmds list to prevent key get/set collision
                                if choice == "config" and sub_choice not in [
                                    "get",
                                    "set",
                                    "remove",
                                ]:
                                    self.assertIn(
                                        sub_choice,
                                        subcmds_val,
                                        f"Subcommand '{sub_choice}' under namespace '{choice}' is missing from the subcmds bypass list in preprocess_args!",
                                    )

    def test_release_announcements_contract(self):
        """Mandate: RELEASE_ANNOUNCEMENTS must contain a valid, non-empty entry for the current VERSION's minor series."""
        from ldm_core.constants import RELEASE_ANNOUNCEMENTS, VERSION

        v_prefix = ".".join(VERSION.split(".")[:2]) if "." in VERSION else VERSION
        self.assertIn(
            v_prefix,
            RELEASE_ANNOUNCEMENTS,
            f"Quality Gate Violation: RELEASE_ANNOUNCEMENTS in constants.py is missing an entry for active minor version '{v_prefix}'.",
        )
        items = RELEASE_ANNOUNCEMENTS[v_prefix]
        self.assertTrue(
            len(items) > 0,
            f"Quality Gate Violation: RELEASE_ANNOUNCEMENTS['{v_prefix}'] is empty.",
        )
        for item in items:
            if isinstance(item, (tuple, list)):
                self.assertEqual(
                    len(item),
                    2,
                    f"Quality Gate Violation: RELEASE_ANNOUNCEMENTS item {item} must be a 2-tuple (command, description).",
                )
                self.assertTrue(
                    item[0] and item[1],
                    f"Quality Gate Violation: RELEASE_ANNOUNCEMENTS item {item} contains empty strings.",
                )

    def test_no_test_files_outside_suite_directory(self):
        """Mandate: every test_*.py must live under ldm_core/tests/, the only directory the suite scans.

        `testpaths` in pyproject.toml and the explicit `pytest ldm_core/tests/`
        invocation in ci.yml both scan that directory alone, so a test file
        placed anywhere else is silently never executed rather than failing
        loudly. This guard makes the misplacement a test failure instead.
        See #1235, where 4 tests covering node power-management scheduling sat
        unexecuted in a root-level tests/ directory.
        """
        repo_root = Path(__file__).resolve().parent.parent.parent
        suite_dir = repo_root / "ldm_core" / "tests"
        skip_dirs = {
            "node_modules",
            "build",
            "dist",
            "site",
            "e2e-work-dir",
            "__pycache__",
        }

        # Matches what pytest would actually collect: the default
        # python_functions = "test*" and python_classes = "Test*" patterns.
        collectable = re.compile(r"^\s*(?:async\s+)?def\s+test|^\s*class\s+Test", re.M)

        orphans = []
        for path in repo_root.rglob("test_*.py"):
            rel = path.relative_to(repo_root)
            # Prune hidden directories (covers .git and every .*venv variant)
            # and generated/vendored trees, none of which are ours to police.
            if any(
                part.startswith(".") or part in skip_dirs or part.endswith(".egg-info")
                for part in rel.parts[:-1]
            ):
                continue
            if suite_dir in path.parents:
                continue
            # A test_*.py-named file with nothing collectable in it is a
            # standalone script, not a misplaced test -- e.g.
            # scripts/test_ui.py is a manual Playwright driver requiring a live
            # portal, documented as such in docs/TESTING.md. Only flag files
            # that genuinely define tests which would never be executed.
            if collectable.search(path.read_text(encoding="utf-8")):
                orphans.append(str(rel))

        self.assertEqual(
            [],
            sorted(orphans),
            "Quality Gate Violation: test file(s) found outside ldm_core/tests/. "
            "Files here are never executed by pytest (see the testpaths setting "
            "in pyproject.toml and the explicit path in ci.yml). Move them into "
            f"ldm_core/tests/: {sorted(orphans)}",
        )

    def test_suite_never_touches_the_developers_real_ldm_home(self):
        """Contract: no test may read or write the developer's real ~/.ldm (#1342).

        The suite used to register pytest tempdirs as real projects, so a run
        added entries like ``tmp58psgp9w`` to ``ldm list``, and it overwrote
        ``last-command.log`` -- the trace needed to diagnose whatever the
        developer last ran. It also *deleted* real entries: a registry key
        pointing at a path that no longer exists was pruned during
        reconciliation.

        The ``isolate_ldm_home`` autouse fixture in conftest.py redirects
        ``LDM_HOME`` per test. This asserts the fixture is actually in force,
        so removing or renaming it fails loudly here rather than silently
        resuming the pollution.
        """
        from ldm_core.utils import get_actual_home

        resolved = get_actual_home()
        real_home = Path.home()

        self.assertNotEqual(
            resolved.resolve(),
            real_home.resolve(),
            "Quality Gate Violation: get_actual_home() resolved to the "
            "developer's real home during a test. The isolate_ldm_home "
            "fixture in ldm_core/tests/conftest.py is missing or disabled; "
            "without it the suite mutates the developer's ~/.ldm registry "
            "and last-command.log (see #1342).",
        )
        self.assertTrue(
            os.environ.get("LDM_HOME"),
            "Quality Gate Violation: LDM_HOME is unset during a test. "
            "get_actual_home() ignores HOME on macOS (see #1349), so "
            "LDM_HOME is the only lever that can redirect LDM state.",
        )

    def test_the_docker_guard_can_actually_fail(self):
        """Contract: block_real_docker must stop a Docker call (#1409).

        A guard that cannot fail is worse than no guard, because it reports
        safety it does not provide. That is not hypothetical here: the first
        version of the #1349 home-isolation canary used a filename that did not
        match prune's glob, so it passed against the unfixed code and proved
        nothing.

        So this drives a real Docker argv through the same boundary the guard
        watches, and asserts it is stopped. Two properties are checked
        together, because either alone can pass while the guard is useless:

        1. A Docker argv raises.
        2. A non-Docker argv still runs -- the guard intercepts, it does not
           replace subprocess wholesale. If this half broke, every test that
           legitimately shells out would fail and the guard would be reverted.

        `docker version --format {{.Client.Version}}` is chosen deliberately:
        entirely read-only, so if the guard ever *does* regress, the worst this
        test can do to the machine is ask it a question.
        """
        import subprocess

        with self.assertRaises(BaseException) as caught:
            subprocess.run(
                ["docker", "version", "--format", "{{.Client.Version}}"],
                capture_output=True,
                check=False,
            )
        self.assertIn(
            "reached the real Docker daemon",
            str(caught.exception),
            "Quality Gate Violation: a Docker command ran during a test. The "
            "block_real_docker fixture in ldm_core/tests/conftest.py is "
            "missing or disabled; without it the suite stops and removes the "
            "developer's own containers, contexts and volumes (see #1409).",
        )

        # The pass-through half.
        completed = subprocess.run(
            [sys.executable, "-c", "print('not docker')"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode)
        self.assertIn("not docker", completed.stdout)

    def test_reclaim_volume_permissions_is_neutralised(self):
        """Contract: the alpine chown/chmod helper must not reach Docker (#1409).

        `reclaim_volume_permissions()` runs
        `docker run --rm -v <path>:/workspace alpine sh -c chown.../chmod...`.
        Three tests reached the daemon through it while this suite reported
        zero, because its caller at `pipelines/run.py` is gated behind
        `platform.system() == "linux"` -- so a macOS run never took the branch
        and CI did, on every Linux runner.

        The stub lives in `stub_docker_environment_probes` rather than in those
        three tests, because ~20 call sites reach this helper and a per-test
        patch is a thing to forget.

        This canary is platform-independent even though the bug was not: the
        helper accepts both darwin and linux, so calling it directly here would
        shell out on either, and the guard would stop it. Removing the stub
        fails this test on any developer machine, not only on CI.
        """
        import os
        import tempfile

        self.assertNotEqual(
            "true",
            os.environ.get("LDM_DRY_RUN", "").lower(),
            "LDM_DRY_RUN short-circuits the helper, which would make this "
            "canary pass without proving anything.",
        )

        from ldm_core.utils import reclaim_volume_permissions

        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(
                reclaim_volume_permissions(tmp),
                "Quality Gate Violation: reclaim_volume_permissions() is not "
                "stubbed. Without it the suite runs `docker run alpine "
                "chown/chmod` against real paths -- see the platform-gating "
                "trap in docs/TESTING.md.",
            )


if __name__ == "__main__":
    unittest.main()
