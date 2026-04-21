import unittest
from unittest.mock import patch
from pathlib import Path
from ldm_core.utils import version_to_tuple, verify_executable_checksum


class TestUtils(unittest.TestCase):
    @patch("sys.argv", ["ldm.py"])
    @patch("sys.frozen", False, create=True)
    def test_verify_executable_checksum_source(self):
        # When running as source (pytest), it should return "Source", True, VERSION
        status, ok, version = verify_executable_checksum("1.6.11")
        self.assertEqual(status, "Source")
        self.assertTrue(ok)
        self.assertEqual(version, "1.6.11")

    def test_version_to_tuple(self):
        self.assertEqual(version_to_tuple("1.5.4"), (1, 5, 4))
        self.assertEqual(version_to_tuple("v1.5.4"), (1, 5, 4))
        self.assertEqual(version_to_tuple("1.5"), (1, 5, 0))
        self.assertEqual(version_to_tuple("2"), (2, 0, 0))
        self.assertEqual(version_to_tuple(""), (0, 0, 0))
        self.assertEqual(version_to_tuple(None), (0, 0, 0))
        self.assertEqual(version_to_tuple("invalid"), (0, 0, 0))

    def test_version_comparison(self):
        self.assertTrue(version_to_tuple("1.5.5") > version_to_tuple("1.5.4"))
        self.assertTrue(version_to_tuple("1.6.0") > version_to_tuple("1.5.9"))
        self.assertTrue(version_to_tuple("2.0.0") > version_to_tuple("1.9.9"))
        self.assertFalse(version_to_tuple("1.5.4") > version_to_tuple("1.5.4"))
        self.assertFalse(version_to_tuple("1.4.9") > version_to_tuple("1.5.0"))

    def test_sanitize_id(self):
        from ldm_core.utils import sanitize_id

        self.assertEqual(sanitize_id("my-project"), "my-project")
        self.assertEqual(sanitize_id("project.123"), "project.123")
        self.assertEqual(sanitize_id("project_123"), "project_123")
        self.assertEqual(sanitize_id("my project!"), "myproject")
        self.assertEqual(sanitize_id("path/to/../../etc/passwd"), "pathto....etcpasswd")
        self.assertEqual(sanitize_id("user; drop table users"), "userdroptableusers")
        self.assertEqual(sanitize_id(""), "")
        self.assertEqual(sanitize_id(None), None)

    @patch("ldm_core.utils.platform.system")
    @patch("ldm_core.utils.os.environ.get")
    def test_get_actual_home_case_insensitive(self, mock_env, mock_system):
        from ldm_core.utils import get_actual_home

        # Mock macOS with capitalized "Darwin"
        mock_system.return_value = "Darwin"
        mock_env.return_value = "tester"

        with patch.object(Path, "exists", return_value=True):
            home = get_actual_home()
            self.assertEqual(str(home), "/Users/tester")

    @patch("ldm_core.utils.get_raw")
    @patch("ldm_core.utils.get_actual_home")
    def test_discover_latest_tag_html_and_json(self, mock_home, mock_get_raw):
        from ldm_core.utils import discover_latest_tag

        mock_home.return_value = Path("/tmp")

        # 1. Test JSON (Docker Hub Style)
        json_data = (
            '{"results": [{"name": "2025.q1.0"}, {"name": "2025.q1.1"}], "next": null}'
        )
        mock_get_raw.return_value = json_data
        tag = discover_latest_tag("https://hub.docker.com/v2/...", refresh=True)
        self.assertEqual(tag, "2025.q1.1")

        # 2. Test HTML (releases.liferay.com Style)
        html_data = """
        <html><body>
        <ul>
            <li><a href="/dxp/7.4.13-u100">7.4.13-u100</a></li>
            <li><a href="/dxp/2026.q1.4-lts">2026.q1.4-lts</a></li>
            <li><a href="/dxp/not-a-tag">not-a-tag</a></li>
        </ul>
        </body></html>
        """
        mock_get_raw.return_value = html_data
        tag = discover_latest_tag("https://releases.liferay.com/dxp", refresh=True)
        self.assertEqual(tag, "2026.q1.4-lts")

    @patch("ldm_core.utils.get_raw")
    @patch("ldm_core.utils.get_actual_home")
    def test_discover_latest_tag_resilience(self, mock_home, mock_get_raw):
        from ldm_core.utils import discover_latest_tag

        mock_home.return_value = Path("/tmp")

        # 1. Test HTML Resilience (No tags found in HTML)
        mock_get_raw.return_value = "<html><body>No tags here</body></html>"
        tag = discover_latest_tag("https://releases.liferay.com/dxp", refresh=True)
        self.assertIsNone(tag)

        # 2. Test JSON Resilience (Malformed JSON)
        mock_get_raw.return_value = '{"results": ['  # Broken JSON
        tag = discover_latest_tag("https://hub.docker.com/v2/...", refresh=True)
        self.assertIsNone(tag)

        # 3. Test HTML Success after failure (Verify it still works when HTML is valid)
        mock_get_raw.return_value = '<li><a href="/dxp/2026.q1.5">2026.q1.5</a></li>'
        tag = discover_latest_tag("https://releases.liferay.com/dxp", refresh=True)
        self.assertEqual(tag, "2026.q1.5")

    def test_metadata_flat_file(self):
        from ldm_core.utils import read_meta, write_meta
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_path = Path(tmp_dir) / ".meta"
            data = {"tag": "2025.q1.0", "container_name": "my-test", "key": "value"}

            # Write and Read
            write_meta(meta_path, data)
            read_data = read_meta(meta_path)

            self.assertEqual(read_data["tag"], "2025.q1.0")
            self.assertEqual(read_data["container_name"], "my-test")
            self.assertEqual(read_data["key"], "value")

    def test_metadata_json(self):
        from ldm_core.utils import read_meta
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_path = Path(tmp_dir) / ".meta"
            data = {"tag": "2025.q1.0", "container_name": "my-test", "json_key": True}
            meta_path.write_text(json.dumps(data))

            read_data = read_meta(meta_path)
            self.assertEqual(read_data["tag"], "2025.q1.0")
            self.assertTrue(read_data["json_key"])

    @patch("ldm_core.utils.get_actual_home")
    def test_find_dxp_roots(self, mock_home):
        from ldm_core.utils import find_dxp_roots
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_home.return_value = tmp_path / "home"

            # Create a project with structure
            project1 = tmp_path / "project1"
            project1.mkdir()
            (project1 / "files").mkdir()
            (project1 / "deploy").mkdir()

            # Create a project with .meta
            project2 = tmp_path / "project2"
            project2.mkdir()
            (project2 / ".liferay-docker.meta").write_text(
                "tag=2025.q1.0\ncontainer_name=p2"
            )

            # Search in tmp_dir
            roots = find_dxp_roots(search_dir=tmp_path)

            self.assertEqual(len(roots), 2)
            root_names = [r["path"].name for r in roots]
            self.assertIn("project1", root_names)
            self.assertIn("project2", root_names)


if __name__ == "__main__":
    unittest.main()
