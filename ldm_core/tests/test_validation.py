import io
import json
import unittest
import zipfile
from unittest.mock import patch

import yaml

from ldm_core.handlers.validation import ClientExtensionAnalyzer


class TestClientExtensionAnalyzer(unittest.TestCase):
    def create_mock_zip(self, files_dict):
        """Creates a mock zip file in memory for testing."""
        memory_zip = io.BytesIO()
        with zipfile.ZipFile(memory_zip, "w") as zf:
            for filename, content in files_dict.items():
                zf.writestr(filename, content)
        memory_zip.seek(0)
        return memory_zip

    @patch("ldm_core.utils.UI.error")
    def test_missing_dockerfile(self, mock_error):
        files = {"LCP.json": json.dumps({"id": "test", "kind": "Deployment"})}
        mock_zip = self.create_mock_zip(files)
        with zipfile.ZipFile(mock_zip, "r") as zf:
            result = ClientExtensionAnalyzer._analyze_zip(zf, "test.zip")
            self.assertFalse(result)
            mock_error.assert_called_once()
            self.assertIn("no 'Dockerfile' was found", mock_error.call_args[0][0])

    @patch("ldm_core.utils.UI.error")
    def test_valid_deployment(self, mock_error):
        files = {
            "LCP.json": json.dumps({"id": "test", "kind": "Deployment"}),
            "Dockerfile": "FROM liferay/node-runner:latest",
        }
        mock_zip = self.create_mock_zip(files)
        with zipfile.ZipFile(mock_zip, "r") as zf:
            result = ClientExtensionAnalyzer._analyze_zip(zf, "test.zip")
            self.assertTrue(result)
            mock_error.assert_not_called()

    @patch("ldm_core.utils.UI.error")
    def test_invalid_type_microservice(self, mock_error):
        files = {
            "client-extension.yaml": yaml.dump({"my-ext": {"type": "microservice"}})
        }
        mock_zip = self.create_mock_zip(files)
        with zipfile.ZipFile(mock_zip, "r") as zf:
            result = ClientExtensionAnalyzer._analyze_zip(zf, "test.zip")
            self.assertFalse(result)
            mock_error.assert_called_once()
            self.assertIn("UNSUPPORTED CONFIG", mock_error.call_args[0][0])

    @patch("ldm_core.utils.UI.error")
    def test_missing_external_port_with_loadbalancer(self, mock_error):
        files = {
            "LCP.json": json.dumps(
                {
                    "id": "test",
                    "kind": "Deployment",
                    "loadBalancer": {"environment": {}},
                    "ports": [{"port": 3001}],  # Missing external: true
                }
            ),
            "Dockerfile": "FROM liferay/node-runner:latest",
        }
        mock_zip = self.create_mock_zip(files)
        with zipfile.ZipFile(mock_zip, "r") as zf:
            result = ClientExtensionAnalyzer._analyze_zip(zf, "test.zip")
            self.assertFalse(result)
            mock_error.assert_called_once()
            self.assertIn(
                "loadBalancer defined but no ports are marked as 'external: true'",
                mock_error.call_args[0][0],
            )
