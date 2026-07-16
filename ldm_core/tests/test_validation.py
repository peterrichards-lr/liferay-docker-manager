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


class TestCustomContainerValidator(unittest.TestCase):
    def setUp(self):
        from ldm_core.handlers.validation import CustomContainerValidator

        self.validator = CustomContainerValidator.validate_custom_containers

    def test_not_a_list(self):
        errors = self.validator({"service_name": "foo"})
        self.assertEqual(len(errors), 1)
        self.assertIn("'custom_containers' must be a list", errors[0])

    def test_valid_container(self):
        containers = [
            {
                "service_name": "wordpress",
                "image": "wordpress:latest",
                "ports": ["8080:80"],
                "environment": ["WORDPRESS_DB_USER=root"],
                "networks": ["frontend"],
                "depends_on": ["db"],
            }
        ]
        errors = self.validator(containers)
        self.assertEqual(len(errors), 0, f"Expected 0 errors, got: {errors}")

    def test_missing_required_fields(self):
        containers = [{"image": "wordpress"}]
        errors = self.validator(containers)
        self.assertEqual(len(errors), 1)
        self.assertIn("missing required field 'service_name'", errors[0])

        containers = [{"service_name": "wordpress"}]
        errors = self.validator(containers)
        self.assertEqual(len(errors), 1)
        self.assertIn("missing required field 'image'", errors[0])

    def test_invalid_service_name(self):
        containers = [{"service_name": "my server!", "image": "nginx"}]
        errors = self.validator(containers)
        self.assertEqual(len(errors), 1)
        self.assertIn("invalid characters", errors[0])

    def test_service_name_collision(self):
        containers = [{"service_name": "liferay", "image": "nginx"}]
        errors = self.validator(containers)
        self.assertEqual(len(errors), 1)
        self.assertIn("collides with an LDM core service", errors[0])

    def test_invalid_ports(self):
        containers = [{"service_name": "wp", "image": "wp", "ports": ["8080"]}]
        errors = self.validator(containers)
        self.assertEqual(len(errors), 1)
        self.assertIn("format 'host_port:container_port'", errors[0])

    def test_invalid_environment(self):
        containers = [
            {"service_name": "wp", "image": "wp", "environment": ["123INVALID=value"]}
        ]
        errors = self.validator(containers)
        self.assertEqual(len(errors), 1)
        self.assertIn("valid KEY or KEY=VALUE", errors[0])
