import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.infra import InfraService


class MockInfraManager:
    def __init__(self):
        self.args = MagicMock()
        self.verbose = False
        self.non_interactive = True

    def run_command(self, *args, **kwargs):
        pass

    def get_resource_path(self, *args, **kwargs):
        from ldm_core.utils import get_resource_path

        return get_resource_path(*args, **kwargs)


class TestInfraService(unittest.TestCase):
    def setUp(self):
        self.manager = MockInfraManager()
        self.infra = InfraService(self.manager)

    @patch("ldm_core.ui.UI.confirm", return_value=True)
    def test_fix_cert_permissions_success(self, mock_confirm):
        with patch.object(self.manager, "run_command") as mock_run:
            res = self.infra._fix_cert_permissions(Path("/tmp/certs"))
            self.assertTrue(res)
            self.assertTrue(mock_run.called)
            cmd = mock_run.call_args[0][0]
            self.assertIn("chown", cmd)

    def test_get_infra_env_basic(self):
        env = self.infra._get_infra_env("192.168.1.1", 8443)
        self.assertEqual(env["LDM_RESOLVED_IP"], "192.168.1.1")
        self.assertEqual(env["LDM_SSL_PORT"], "8443")


if __name__ == "__main__":
    unittest.main()
