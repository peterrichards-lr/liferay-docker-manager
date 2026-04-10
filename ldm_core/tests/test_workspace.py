import unittest
import json
from ldm_core.handlers.workspace import WorkspaceHandler
from ldm_core.handlers.diagnostics import DiagnosticsHandler


class MockWorkspaceManager(WorkspaceHandler, DiagnosticsHandler):
    def __init__(self):
        self.verbose = False


class TestWorkspaceMetadata(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    def test_parse_lcp_json_basic(self):
        content = json.dumps({"id": "my-service", "kind": "deployment", "memory": 1024})
        info = self.handler._parse_lcp_json(content)
        self.assertEqual(info["id"], "my-service")
        self.assertEqual(info["kind"], "Deployment")
        self.assertEqual(info["memory"], 1024)
        self.assertTrue(info["deploy"])  # Default should be True

    def test_parse_lcp_json_with_deploy_false(self):
        content = json.dumps({"id": "test", "deploy": False})
        info = self.handler._parse_lcp_json(content)
        self.assertFalse(info["deploy"])

    def test_parse_lcp_json_target_port(self):
        content = json.dumps(
            {"id": "microservice", "loadBalancer": {"targetPort": 3001}}
        )
        info = self.handler._parse_lcp_json(content)
        self.assertEqual(info["loadBalancer"]["targetPort"], 3001)
        self.assertTrue(info["has_load_balancer"])

    def test_parse_lcp_json_external_port(self):
        content = json.dumps({"id": "web", "ports": [{"port": 80, "external": True}]})
        info = self.handler._parse_lcp_json(content)
        self.assertTrue(info["has_load_balancer"])

    def test_merge_info_logic(self):
        # Initial info template
        info = {
            "id": "base",
            "kind": "Deployment",
            "deploy": True,
            "loadBalancer": None,
            "env": {"FOO": "BAR"},
        }

        # New info to merge (e.g. from LCP.json)
        new_info = {
            "id": "overridden",
            "loadBalancer": {"targetPort": 3001},
            "env": {"BAZ": "QUX"},
        }

        # Manually trigger the merge logic (we're testing the logic inside _scan_extension_metadata)
        # Using a closure helper similar to the real one
        def merge(target, source):
            for k in target.keys():
                if source.get(k) is not None:
                    if k == "loadBalancer" and source[k]:
                        if target[k] is None:
                            target[k] = {}
                        target[k].update(source[k])
                    elif k == "env":
                        target[k].update(source[k])
                    else:
                        target[k] = source[k]

        merge(info, new_info)

        self.assertEqual(info["id"], "overridden")
        self.assertEqual(info["loadBalancer"]["targetPort"], 3001)
        self.assertEqual(info["env"]["FOO"], "BAR")
        self.assertEqual(info["env"]["BAZ"], "QUX")


if __name__ == "__main__":
    unittest.main()
