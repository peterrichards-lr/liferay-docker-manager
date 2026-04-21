import unittest
import yaml
from pathlib import Path
import tempfile
from ldm_core.manager import LiferayManager


class TestArchitecturalContracts(unittest.TestCase):
    """
    Verification suite to ensure core LDM architectural mandates are preserved.
    These tests verify the OUTPUT of the orchestration engine (Compose, Portal-Ext).
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

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
        self.tmp_dir.cleanup()

    def test_docker_compose_labels_mandate(self):
        """Mandate: Every Liferay container MUST have the LDM project label for 'ldm status' and 'ldm prune'."""
        paths = self.manager.setup_paths(self.project_path)
        meta = {
            "container_name": "contract-test",
            "tag": "2025.q1.0",
            "host_name": "contract.local",
        }

        self.manager.write_docker_compose(paths, meta)

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
            with patch.object(self.manager, "setup_infrastructure"):
                with patch.object(self.manager, "write_docker_compose") as mock_write:
                    self.manager.sync_stack(paths, meta, no_up=True)

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


if __name__ == "__main__":
    unittest.main()
