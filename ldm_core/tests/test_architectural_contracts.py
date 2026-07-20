import importlib
import inspect
import pkgutil
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


if __name__ == "__main__":
    unittest.main()
