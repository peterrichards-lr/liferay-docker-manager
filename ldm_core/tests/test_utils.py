import json
import os
import tempfile
import typing
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from ldm_core.utils import (
    dict_to_yaml,
    get_json,
    get_raw,
    is_local_host,
    verify_executable_checksum,
    version_to_tuple,
)


class TestUtils(unittest.TestCase):
    def test_is_local_host(self):
        """Verify is_local_host recognizes all 127.0.0.0/8 IPs, localhost, ::1, and empty values as local."""
        self.assertTrue(is_local_host(None))
        self.assertTrue(is_local_host(""))
        self.assertTrue(is_local_host("localhost"))
        self.assertTrue(is_local_host("LOCALHOST"))
        self.assertTrue(is_local_host("127.0.0.1"))
        self.assertTrue(is_local_host("127.0.0.2"))
        self.assertTrue(is_local_host("127.0.1.1"))
        self.assertTrue(is_local_host("127.255.255.254"))
        self.assertTrue(is_local_host("::1"))

        self.assertFalse(is_local_host("34.1.1.1"))
        self.assertFalse(is_local_host("192.168.1.100"))
        self.assertFalse(is_local_host("example.com"))

    def test_dict_to_yaml(self):
        data = {
            "services": {
                "web": {
                    "image": "nginx:latest",
                    "ports": ["80:80", "443:443"],
                    "environment": {
                        "DEBUG": True,
                        "VERSION": 1.0,
                        "MSG": "Hello\nWorld",
                    },
                }
            }
        }
        yaml_str = dict_to_yaml(data)
        self.assertIn("services:", yaml_str)
        self.assertIn("web:", yaml_str)
        self.assertIn("image: nginx:latest", yaml_str)
        self.assertIn("- 80:80", yaml_str)
        self.assertIn("DEBUG: true", yaml_str)

    @patch("sys.argv", ["ldm.py"])
    @patch("sys.frozen", False, create=True)
    def test_verify_executable_checksum_source(self):
        # When running as source (pytest), it should return "Source", True, VERSION
        status, ok, version = verify_executable_checksum("1.6.11")
        self.assertEqual(status, "Source")
        self.assertTrue(ok)
        self.assertEqual(version, "1.6.11")

    def test_version_to_tuple(self):
        # 1. Stable versions (assigned weight 999 to beat pre-releases)
        self.assertEqual(version_to_tuple("2.4.26"), (2, 4, 26, 999))
        self.assertEqual(version_to_tuple("v2.4.26"), (2, 4, 26, 999))
        self.assertEqual(version_to_tuple("1.0"), (1, 0, 0, 999))

        # 2. Beta / Pre-release versions
        self.assertEqual(version_to_tuple("2.4.26-beta.1"), (2, 4, 26, 1, 1))
        self.assertEqual(version_to_tuple("2.4.26-beta.2"), (2, 4, 26, 1, 2))
        self.assertEqual(version_to_tuple("2.4.26-beta.10"), (2, 4, 26, 1, 10))
        self.assertEqual(version_to_tuple("2.4.26-pre.1"), (2, 4, 26, 2, 1))

        # 3. Comparisons
        self.assertTrue(version_to_tuple("2.4.26-beta.1") > version_to_tuple("2.4.25"))
        self.assertTrue(version_to_tuple("2.4.26") > version_to_tuple("2.4.26-beta.1"))
        self.assertTrue(
            version_to_tuple("2.4.26-beta.2") > version_to_tuple("2.4.26-beta.1")
        )
        self.assertTrue(
            version_to_tuple("2.4.26-beta.10") > version_to_tuple("2.4.26-beta.9")
        )
        self.assertTrue(
            version_to_tuple("2.4.26-pre.1") > version_to_tuple("2.4.26-beta.48")
        )
        self.assertTrue(version_to_tuple("1.6.0") > version_to_tuple("1.5.9"))
        self.assertFalse(version_to_tuple("2.4.25") > version_to_tuple("2.4.25"))

        # 4. Edge cases / Invalid
        self.assertEqual(version_to_tuple(""), (0, 0, 0, 0))
        self.assertEqual(version_to_tuple(None), (0, 0, 0, 0))
        self.assertEqual(version_to_tuple("invalid"), (0, 0, 0, 0))

    def test_sanitize_id(self):
        from ldm_core.utils import sanitize_id

        self.assertEqual(sanitize_id("my-project"), "my-project")
        self.assertEqual(sanitize_id("project.123"), "project.123")
        self.assertEqual(sanitize_id("project_123"), "project_123")
        self.assertEqual(sanitize_id("my project!"), "my-project")
        self.assertEqual(sanitize_id("path/to/../../etc/passwd"), "pathto....etcpasswd")
        self.assertEqual(sanitize_id("user; drop table users"), "user-drop-table-users")
        self.assertEqual(sanitize_id(""), "")
        self.assertEqual(sanitize_id(None), None)

    @patch("ldm_core.utils.platform.system")
    @patch("ldm_core.utils.os.environ.get")
    def test_get_actual_home_case_insensitive(self, mock_env, mock_system):
        from ldm_core.utils import get_actual_home

        # Mock macOS with capitalized "Darwin"
        mock_system.return_value = "Darwin"

        # LDM-#1349: answer per-key rather than returning "tester" for every
        # lookup. A blanket return value also answers LDM_HOME, which would
        # make this test assert the override path while claiming to cover the
        # SUDO_USER/USER one.
        def env(key, default=None):
            return {"SUDO_USER": "", "USER": "tester"}.get(key, default)

        mock_env.side_effect = env

        with patch.object(Path, "exists", return_value=True):
            home = get_actual_home()
            self.assertEqual(home.as_posix(), "/Users/tester")


class TestLdmHomeOverride(unittest.TestCase):
    """LDM-#1349: `LDM_HOME` is the only way to redirect LDM's state directory."""

    def test_ldm_home_wins_over_everything(self):
        from ldm_core.utils import get_actual_home

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ, {"LDM_HOME": tmp, "SUDO_USER": "root", "USER": "someone"}
            ):
                self.assertEqual(Path(tmp), get_actual_home())

    def test_a_tilde_is_expanded(self):
        from ldm_core.utils import get_actual_home

        with patch.dict(os.environ, {"LDM_HOME": "~/ldm-state"}):
            self.assertEqual(Path.home() / "ldm-state", get_actual_home())

    def test_an_unset_or_blank_value_falls_through(self):
        """Blank must not resolve to Path("") -- that would be the CWD."""
        from ldm_core.utils import get_actual_home

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LDM_HOME", None)
            unset = get_actual_home()

        for blank in ("", "   "):
            with patch.dict(os.environ, {"LDM_HOME": blank}):
                self.assertEqual(unset, get_actual_home())

    def test_the_override_does_not_need_to_exist_yet(self):
        """Callers mkdir under it; requiring existence would break first use."""
        from ldm_core.utils import get_actual_home

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "not-created-yet"
            with patch.dict(os.environ, {"LDM_HOME": str(target)}):
                self.assertEqual(target, get_actual_home())

    def test_the_registry_follows_the_override(self):
        """The point of the primitive: state lands under LDM_HOME, not ~."""
        from ldm_core.utils import find_dxp_roots

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            workspace = home / "ws"
            project = workspace / "handmade"
            project.mkdir(parents=True)
            (project / "meta").write_text('{"container_name": "handmade"}')

            with patch.dict(
                os.environ, {"LDM_HOME": str(home), "LDM_WORKSPACE": str(workspace)}
            ):
                find_dxp_roots()

            self.assertTrue(
                (home / ".ldm" / "registry.json").exists(),
                "registry was not written under LDM_HOME",
            )

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
            <li><a href="/dxp/2026.q1.3-lts">2026.q1.3-lts</a></li>
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

        import tempfile

        from ldm_core.utils import read_meta, write_meta

        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_path = Path(tmp_dir) / "project.meta"
            data = {"tag": "2025.q1.0", "container_name": "my-test", "key": "value"}

            # Write and Read
            write_meta(meta_path, data)
            read_data = read_meta(meta_path)

            self.assertEqual(read_data["tag"], "2025.q1.0")
            self.assertEqual(read_data["container_name"], "my-test")
            self.assertEqual(read_data["key"], "value")

    def test_metadata_json(self):
        import json
        import tempfile

        from ldm_core.utils import read_meta

        with tempfile.TemporaryDirectory() as tmp_dir:
            meta_path = Path(tmp_dir) / ".meta"
            data = {"tag": "2025.q1.0", "container_name": "my-test", "json_key": True}
            meta_path.write_text(json.dumps(data))

            read_data = read_meta(meta_path)
            self.assertEqual(read_data["tag"], "2025.q1.0")
            self.assertTrue(read_data["json_key"])

    @patch("ldm_core.utils.get_actual_home")
    def test_find_dxp_roots(self, mock_home):
        import tempfile

        from ldm_core.utils import find_dxp_roots

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

    @patch("pathlib.Path.cwd")
    def test_safe_cwd_deleted(self, mock_cwd):
        from ldm_core.utils import safe_cwd

        mock_cwd.side_effect = FileNotFoundError("No such file or directory")
        self.assertIsNone(safe_cwd())

    @patch("ldm_core.utils.get_actual_home")
    @patch("pathlib.Path.cwd")
    def test_find_dxp_roots_deleted_cwd(self, mock_cwd, mock_home):
        from ldm_core.utils import find_dxp_roots

        mock_cwd.side_effect = FileNotFoundError("No such file or directory")
        mock_home.return_value = Path("/nonexistent/home")
        roots = find_dxp_roots()
        self.assertEqual(roots, [])

    @patch("ldm_core.utils.get_actual_home")
    @patch("pathlib.Path.cwd")
    def test_safe_rmtree_safety_violations(self, mock_cwd, mock_home):
        import tempfile

        import ldm_core.utils
        from ldm_core.utils import safe_rmtree

        # Setup temp home and CWD mocks
        temp_home = Path("/fake/home")
        mock_home.return_value = temp_home
        mock_cwd.return_value = Path("/fake/cwd")

        # 1. Test home directory deletion block
        with self.assertRaises(ValueError) as ctx:
            safe_rmtree(temp_home)
        self.assertIn(
            "Safety Violation: Cannot delete home directory", str(ctx.exception)
        )

        # 2. Test system root directory deletion block
        with self.assertRaises(ValueError) as ctx:
            safe_rmtree(Path("/Users"))
        self.assertIn(
            "Safety Violation: Cannot delete system directory", str(ctx.exception)
        )

        # 3. Test active CWD deletion block
        with self.assertRaises(ValueError) as ctx:
            safe_rmtree(Path("/fake/cwd"))
        self.assertIn(
            "Safety Violation: Cannot delete current working directory",
            str(ctx.exception),
        )

        # 4. Test active CWD parent deletion block
        with self.assertRaises(ValueError) as ctx:
            safe_rmtree(Path("/fake"))
        self.assertIn(
            "Safety Violation: Cannot delete current working directory",
            str(ctx.exception),
        )

        # 5. Test git repository deletion block
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / ".git").mkdir()

            with self.assertRaises(ValueError) as ctx:
                safe_rmtree(tmp_path)
            self.assertIn(
                "Safety Violation: Cannot delete a git repository", str(ctx.exception)
            )

        # 6. Test LDM package directory deletion block
        pkg_dir = Path(ldm_core.utils.__file__).parent.parent.resolve()
        with self.assertRaises(ValueError) as ctx:
            safe_rmtree(pkg_dir)
        self.assertIn(
            "Safety Violation: Cannot delete LDM installation/source directory",
            str(ctx.exception),
        )

    def test_safe_rmtree_read_only_files(self):
        import stat
        import tempfile

        from ldm_core.utils import safe_rmtree

        with tempfile.TemporaryDirectory() as tmp_dir:
            parent = Path(tmp_dir) / "sub"
            parent.mkdir()
            test_file = parent / "readonly.txt"
            test_file.write_text("content")

            # Make the file read-only
            test_file.chmod(stat.S_IREAD)

            # Deleting parent should succeed
            safe_rmtree(parent)
            self.assertFalse(parent.exists())

    @patch("ldm_core.utils.platform.system")
    @patch("ldm_core.utils.reclaim_volume_permissions")
    @patch("shutil.rmtree")
    def test_safe_rmtree_permission_denied_trigger_reclaim(
        self, mock_rmtree, mock_reclaim, mock_system
    ):
        from ldm_core.utils import safe_rmtree

        mock_system.return_value = "Linux"
        mock_reclaim.return_value = True

        # Mock shutil.rmtree to raise PermissionError on first call, then succeed
        call_count = 0

        def rmtree_side_effect(path, onerror=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Trigger onerror
                if onerror:
                    try:
                        raise PermissionError("[Errno 13] Permission denied")
                    except PermissionError:
                        import sys

                        onerror(None, path, sys.exc_info())
                raise PermissionError("[Errno 13] Permission denied")

        mock_rmtree.side_effect = rmtree_side_effect

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("ldm_core.utils.verify_safe_to_delete"),
        ):
            safe_rmtree(Path("/fake/project"))

            self.assertEqual(call_count, 2)
            mock_reclaim.assert_called_once_with(Path("/fake/project").resolve())

    @patch("ldm_core.utils.run_command")
    @patch("ldm_core.utils.platform.system")
    def test_reclaim_volume_permissions_dynamic_uid_gid(self, mock_system, mock_run):
        from ldm_core.utils import reclaim_volume_permissions

        mock_system.return_value = "Linux"
        mock_run.return_value = MagicMock()

        with (
            patch("os.getuid", return_value=1234, create=True),
            patch("os.getgid", return_value=5678, create=True),
            patch("pathlib.Path.exists", return_value=True),
        ):
            reclaim_volume_permissions(Path("/fake/path"))

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("docker", args)
            self.assertIn("alpine", args)
            cmd_str = args[-1]
            self.assertIn("chown -R 1234:5678 /workspace", cmd_str)


class TestUpdateChecks(unittest.TestCase):
    @patch("requests.get")
    @patch("pathlib.Path.home")
    def test_check_for_updates_stable(self, mock_home, mock_get):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_home.return_value = Path(tmp_dir)

            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "tag_name": "v2.6.0",
                "html_url": "http://release",
                "assets": [{"name": "ldm-macos", "browser_download_url": "http://dl"}],
            }
            mock_get.return_value = mock_res

            from ldm_core.utils import check_for_updates

            # Mock system and machine to ensure predictable result
            with (
                patch("sys.platform", "darwin", create=True),
                patch("platform.machine", return_value="arm64"),
            ):
                version, url = check_for_updates("2.5.0")
                self.assertEqual(version, "2.6.0")
                self.assertEqual(url, "http://dl")

    @patch("requests.get")
    @patch("pathlib.Path.home")
    def test_check_for_updates_never_serves_a_preview_build(self, mock_home, mock_get):
        """LDM-#1265: preview builds must never reach anyone as an upgrade.

        A preview is a disposable artifact for validating an idea that may be
        abandoned. It is published as a real GitHub pre-release with real
        binaries, so without an explicit skip it is a candidate like any other.

        `version_to_tuple("preview-1265.1")` is (0,0,0,0,1265), which already
        sorts below every release -- but that is an artefact of how unrecognised
        labels rank, not a stated rule. This asserts the deliberate skip, so the
        guarantee survives someone changing that ranking. The preview is listed
        first here precisely so a naive "take the first with assets" would fail.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_home.return_value = Path(tmp_dir)

            mock_res = MagicMock()
            mock_res.status_code = 200
            # The preview must be the ONLY candidate. Listing it beside a higher
            # real release proves nothing: preview-1265.1 ranks (0,0,0,0,1265)
            # and loses on ordering alone, so the guard is never exercised. That
            # version of this test passed with the guard deleted -- confirmed by
            # deleting it and watching it stay green.
            mock_res.json.return_value = [
                {
                    "tag_name": "preview-1265.1",
                    "html_url": "http://preview",
                    "assets": [
                        {
                            "name": "ldm-macos-arm64",
                            "browser_download_url": "http://dl-preview",
                        }
                    ],
                },
            ]
            mock_get.return_value = mock_res

            from ldm_core.utils import check_for_updates

            with (
                patch("sys.platform", "darwin", create=True),
                patch("platform.machine", return_value="arm64"),
            ):
                version, url = check_for_updates("0.0.1", force=True, pre_release=True)

            # Nothing to offer, because the only release available is a preview.
            self.assertIsNone(version)
            self.assertIsNone(url)

    @patch("requests.get")
    @patch("pathlib.Path.home")
    def test_check_for_updates_ignores_releases_without_platform_assets(
        self, mock_home, mock_get
    ):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_home.return_value = Path(tmp_dir)

            mock_res = MagicMock()
            mock_res.status_code = 200
            # Release v2.7.0 is still building (no assets), while v2.6.0 is ready with assets
            mock_res.json.return_value = [
                {
                    "tag_name": "v2.7.0",
                    "html_url": "http://release-2.7",
                    "assets": [
                        {
                            "name": "checksums.txt",
                            "browser_download_url": "http://checksums",
                        }
                    ],
                },
                {
                    "tag_name": "v2.6.0",
                    "html_url": "http://release-2.6",
                    "assets": [
                        {
                            "name": "ldm-macos-arm64",
                            "browser_download_url": "http://dl-2.6",
                        }
                    ],
                },
            ]
            mock_get.return_value = mock_res

            from ldm_core.utils import check_for_updates

            with (
                patch("sys.platform", "darwin", create=True),
                patch("platform.machine", return_value="arm64"),
            ):
                version, url = check_for_updates("2.5.0", force=True, pre_release=True)
                self.assertEqual(version, "2.6.0")
                self.assertEqual(url, "http://dl-2.6")

    @patch("requests.head")
    @patch("requests.get")
    @patch("pathlib.Path.home")
    def test_check_for_updates_fallback_success(self, mock_home, mock_get, mock_head):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_home.return_value = Path(tmp_dir)

            # API returns rate-limited response (403)
            mock_api_res = MagicMock()
            mock_api_res.status_code = 403
            mock_get.return_value = mock_api_res

            # Fallback redirect HEAD request returns 302
            mock_head_res = MagicMock()
            mock_head_res.status_code = 302
            mock_head_res.headers = {
                "Location": "https://github.com/peterrichards-lr/liferay-docker-manager/releases/tag/v2.11.8"
            }
            mock_head.return_value = mock_head_res

            from ldm_core.utils import check_for_updates

            with (
                patch("sys.platform", "darwin", create=True),
                patch("platform.machine", return_value="arm64"),
            ):
                version, url = check_for_updates("2.11.7", force=True)
                self.assertEqual(version, "2.11.8")
                self.assertEqual(
                    url,
                    "https://github.com/peterrichards-lr/liferay-docker-manager/releases/download/v2.11.8/ldm-macos-arm64",
                )

    @patch("requests.head")
    @patch("requests.get")
    @patch("pathlib.Path.home")
    def test_check_for_updates_fallback_failure(self, mock_home, mock_get, mock_head):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_home.return_value = Path(tmp_dir)

            # API returns 500
            mock_api_res = MagicMock()
            mock_api_res.status_code = 500
            mock_get.return_value = mock_api_res

            # Fallback returns 404
            mock_head_res = MagicMock()
            mock_head_res.status_code = 404
            mock_head.return_value = mock_head_res

            from ldm_core.utils import check_for_updates

            version, url = check_for_updates("2.11.7", force=True)
            self.assertIsNone(version)
            self.assertIsNone(url)

    @patch("requests.get")
    @patch("pathlib.Path.home")
    def test_check_for_updates_tag_success(self, mock_home, mock_get):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_home.return_value = Path(tmp_dir)

            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "tag_name": "v2.11.53",
                "html_url": "http://release/v2.11.53",
                "assets": [
                    {
                        "name": "ldm-macos-arm64",
                        "browser_download_url": "http://dl/v2.11.53",
                    }
                ],
            }
            mock_get.return_value = mock_res

            from ldm_core.utils import check_for_updates

            with (
                patch("sys.platform", "darwin", create=True),
                patch("platform.machine", return_value="arm64"),
            ):
                version, url = check_for_updates("2.11.56", tag="v2.11.53")
                self.assertEqual(version, "2.11.53")
                self.assertEqual(url, "http://dl/v2.11.53")

                # Check that calling without the v prefix also works
                version_no_v, url_no_v = check_for_updates("2.11.56", tag="2.11.53")
                self.assertEqual(version_no_v, "2.11.53")

    @patch("requests.get")
    @patch("pathlib.Path.home")
    def test_check_for_updates_tag_not_found(self, mock_home, mock_get):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_home.return_value = Path(tmp_dir)

            mock_res = MagicMock()
            mock_res.status_code = 404
            mock_get.return_value = mock_res

            from ldm_core.utils import check_for_updates

            version, url = check_for_updates("2.11.56", tag="v2.11.99")
            self.assertIsNone(version)
            self.assertIsNone(url)

    @patch("requests.get")
    @patch("pathlib.Path.home")
    def test_check_for_updates_cache_write_is_atomic(self, mock_home, mock_get):
        """Cache write should use a .tmp file + atomic replace, not a bare write_text."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_home.return_value = Path(tmp_dir)
            cache_file = Path(tmp_dir) / ".ldm_update_cache"

            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "tag_name": "v2.99.0",
                "html_url": "https://github.com/peterrichards-lr/liferay-docker-manager/releases/v2.99.0",
                "assets": [],
            }
            mock_get.return_value = mock_res

            from ldm_core.utils import check_for_updates

            version, _ = check_for_updates("2.0.0")
            self.assertEqual(version, "2.99.0")
            # Cache file should exist after successful update check
            self.assertTrue(
                cache_file.exists(), "Cache file should be written after update check"
            )
            # Verify no stale .tmp file was left behind (atomic replace succeeded)
            tmp_file = cache_file.with_suffix(cache_file.suffix + ".tmp")
            self.assertFalse(
                tmp_file.exists(), ".tmp file should not remain after atomic replace"
            )

    @patch("requests.get")
    @patch("pathlib.Path.home")
    def test_check_for_updates_cache_hit_uses_filelock(self, mock_home, mock_get):
        """Cache read should respect a FileLock (shared read) and not call the API when fresh."""
        import json
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as tmp_dir:
            mock_home.return_value = Path(tmp_dir)
            cache_file = Path(tmp_dir) / ".ldm_update_cache"

            # Write a fresh cache entry manually
            cache_file.write_text(
                json.dumps(
                    {
                        "last_check": time.time(),
                        "latest_version": "2.88.0",
                        "url": "https://example.com/release",
                    }
                ),
                encoding="utf-8",
            )

            from ldm_core.utils import check_for_updates

            version, url = check_for_updates("2.0.0")
            self.assertEqual(version, "2.88.0")
            self.assertEqual(url, "https://example.com/release")
            # If the cache was read correctly, no GitHub API call should have been made
            mock_get.assert_not_called()

    def test_atomic_copy(self):
        from ldm_core.utils import atomic_copy

        with (
            patch("ldm_core.utils.safe_copy") as mock_safe_copy,
            patch("shutil.move") as mock_move,
        ):
            src = Path("/tmp/src.jar")
            dst = Path("/tmp/deploy/dst.jar")

            # We use a mock for resolve that returns the path itself for testing
            with patch.object(Path, "resolve", return_value=dst):
                atomic_copy(src, dst)

                # Verify it copied to a temp hidden file first
                expected_tmp = dst.parent / f".{dst.name}.tmp"
                mock_safe_copy.assert_called_once_with(src, expected_tmp)

                # Verify it atomically moved the temp file to destination
                mock_move.assert_called_once_with(str(expected_tmp), str(dst))

    def test_safe_write_text_raises_on_permission_error(self):
        """safe_write_text should propagate PermissionError, not silently reclaim permissions."""
        from ldm_core.utils import safe_write_text

        with (
            patch("pathlib.Path.with_suffix", return_value=Path("/fake/.tmp.txt")),
            patch(
                "pathlib.Path.write_text",
                side_effect=PermissionError("[Errno 13] Permission denied"),
            ),
            patch("pathlib.Path.exists", return_value=True),
        ):
            with self.assertRaises(PermissionError):
                safe_write_text(Path("/fake/test.txt"), "content")

    def test_safe_mkdir_raises_on_permission_error(self):
        """safe_mkdir should propagate PermissionError, not silently reclaim permissions."""
        from ldm_core.utils import safe_mkdir

        with (
            patch(
                "pathlib.Path.mkdir",
                side_effect=PermissionError("[Errno 13] Permission denied"),
            ),
        ):
            with self.assertRaises(PermissionError):
                safe_mkdir("/fake/path")

    def test_reclaim_volume_permissions(self):
        from ldm_core.utils import reclaim_volume_permissions

        with (
            patch("ldm_core.utils.run_command") as mock_run,
            patch("pathlib.Path.exists", return_value=True),
            patch("ldm_core.utils.platform.system", return_value="Linux"),
        ):
            reclaim_volume_permissions("/tmp/some-dir", uid="1001", gid="1001")

            # Verify it ran a docker container with chmod/chown
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertEqual(cmd[0], "docker")
            self.assertEqual(cmd[1], "run")
            # Verify the chmod/chown commands in the command string
            docker_cmd = cmd[cmd.index("-c") + 1]
            self.assertIn("chown -R 1001:1001", docker_cmd)
            self.assertIn("chmod -R 750", docker_cmd)

    def test_run_command_timeout(self):
        import subprocess

        from ldm_core.utils import run_command

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="sleep 10", timeout=1.0),
        ):
            # check=False should return None
            res = run_command("sleep 10", check=False)
            self.assertIsNone(res)

            # check=True should raise SystemExit with 124
            with self.assertRaises(SystemExit) as ctx:
                run_command("sleep 10", check=True)
            self.assertEqual(ctx.exception.code, 124)

    def test_run_command_non_ascii_error(self):
        import subprocess

        from ldm_core.utils import run_command

        # Mock subprocess.run to raise CalledProcessError with non-ascii/localized Spanish/Mandarin stderr
        non_ascii_stderr = (
            "pg_dump: error: la conexión falló: FATAL: la base de datos no existe: 中文"
        )
        mock_err = subprocess.CalledProcessError(
            returncode=1,
            cmd="pg_dump",
            output=None,
            stderr=non_ascii_stderr.encode("utf-8"),
        )

        with patch("subprocess.run", side_effect=mock_err):
            # Mock print to raise UnicodeEncodeError (simulating charmap console printing failure)
            # when print is called with the original string, but succeed on fallback.
            print_calls = []

            def mock_print(msg, *args, **kwargs):
                print_calls.append(msg)
                if "la conexión" in msg and "Safe" not in msg:
                    raise UnicodeEncodeError(
                        "charmap", msg, 0, 1, "character maps to <undefined>"
                    )

            with patch("builtins.print", side_effect=mock_print):
                with self.assertRaises(SystemExit) as ctx:
                    run_command("pg_dump", check=True)
                self.assertEqual(ctx.exception.code, 1)

            # Assert that print fell back to printing the safe version with backslash replacements
            self.assertTrue(
                any("Error Details (Safe):" in call for call in print_calls)
            )

    def test_reclaim_volume_permissions_timeout(self):
        import subprocess

        from ldm_core.utils import reclaim_volume_permissions

        with (
            patch(
                "ldm_core.utils.run_command",
                side_effect=subprocess.TimeoutExpired(
                    cmd="docker run ...", timeout=15.0
                ),
            ),
            patch("pathlib.Path.exists", return_value=True),
            patch("ldm_core.utils.platform.system", return_value="Linux"),
        ):
            # Should return False instead of raising/crashing
            res = reclaim_volume_permissions("/tmp/some-dir", uid="1001", gid="1001")
            self.assertFalse(res)

    @patch("ldm_core.utils.requests.get")
    def test_validate_liferay_tag(self, mock_get):
        from ldm_core.utils import validate_liferay_tag

        # 1. Test None or empty tag
        self.assertFalse(validate_liferay_tag(None))
        self.assertFalse(validate_liferay_tag(""))

        # Mock JSON data returned by Liferay releases API
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "url": "https://releases-cdn.liferay.com/dxp/2026.q1.7-lts",
                "targetPlatformVersion": "7.4.13",
            },
            {
                "url": "https://releases-cdn.liferay.com/dxp/2026.q1.8-lts",
                "targetPlatformVersion": "",
            },
        ]
        mock_get.return_value = mock_response

        # 2. Test valid tags
        self.assertTrue(validate_liferay_tag("2026.q1.7-lts"))
        self.assertTrue(validate_liferay_tag("7.4.13"))
        self.assertTrue(validate_liferay_tag("2026.q1.8-lts"))

        # 3. Test invalid tag
        self.assertFalse(validate_liferay_tag("invalid-tag"))

        # 4. Test API error status code (returns True fallback)
        mock_response.status_code = 500
        self.assertTrue(validate_liferay_tag("invalid-tag"))

        # 5. Test network exception (returns True fallback)
        mock_get.side_effect = Exception("Connection timeout")
        self.assertTrue(validate_liferay_tag("invalid-tag"))

    @patch("ldm_core.utils.requests.get")
    def test_resolve_liferay_docker_tag(self, mock_get):

        from ldm_core.utils import resolve_liferay_docker_tag

        dxp_val = "dxp-2026.q1.7-lts"
        portal_val = "portal-7.4.3.107-ga107"

        # Mock JSON data returned by Liferay releases API
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "url": "https://releases-cdn.liferay.com/dxp/2026.q1.7-lts",
                "release" + "Key": dxp_val,
                "targetPlatformVersion": "2026.q1.7",
                "product": "dxp",
            },
            {
                "url": "https://releases-cdn.liferay.com/portal/7.4.3.107-ga107",
                "release" + "Key": portal_val,
                "targetPlatformVersion": "7.4.3.107",
                "product": "portal",
            },
        ]
        mock_get.return_value = mock_response

        # 1. Test online tag resolution (resolving targetPlatformVersion)
        tag, is_portal = resolve_liferay_docker_tag("2026.q1.7")
        self.assertEqual(tag, "2026.q1.7-lts")
        self.assertFalse(is_portal)

        # 2. Test online tag resolution (portal releaseKey)
        tag, is_portal = resolve_liferay_docker_tag("portal-7.4.3.107-ga107")
        self.assertEqual(tag, "7.4.3.107-ga107")
        self.assertTrue(is_portal)

        # 3. Test offline fallback heuristic for q1
        mock_get.side_effect = Exception("Offline")
        # Ensure cache doesn't hit by using a non-cached key
        tag, is_portal = resolve_liferay_docker_tag("2025.q1.12")
        self.assertEqual(tag, "2025.q1.12-lts")
        self.assertFalse(is_portal)

        # 4. Test custom heuristics via manager defaults
        mock_manager = MagicMock()
        mock_manager.defaults.get.return_value = {r"\.xyz$": "-custom"}
        tag, is_portal = resolve_liferay_docker_tag("123.xyz", manager=mock_manager)
        self.assertEqual(tag, "123.xyz-custom")


if __name__ == "__main__":
    unittest.main()


def test_resolve_infrastructure_mode_args_override():
    from ldm_core.utils import resolve_infrastructure_mode

    defaults = type("MockDefaults", (), {"get": lambda _k, _d="isolated": "isolated"})
    assert (
        resolve_infrastructure_mode("database_mode", {}, defaults, "shared") == "shared"
    )


def test_resolve_infrastructure_mode_meta_precedence():
    from ldm_core.utils import resolve_infrastructure_mode

    defaults = type("MockDefaults", (), {"get": lambda _k, _d="isolated": "isolated"})
    assert (
        resolve_infrastructure_mode(
            "database_mode", {"database_mode": "shared"}, defaults
        )
        == "shared"
    )


def test_resolve_infrastructure_mode_defaults():
    from ldm_core.utils import resolve_infrastructure_mode

    # Mock defaults to return "shared", testing if old versions override this
    defaults = type("MockDefaults", (), {"get": lambda _k, _d="isolated": "shared"})

    # 1. New projects respect the new default
    assert (
        resolve_infrastructure_mode(
            "database_mode", {"ldm_version": "2.15.0"}, defaults
        )
        == "shared"
    )

    # 2. Old projects (pre-2.14.0) enforce "isolated" database mode regardless of the shared default
    assert (
        resolve_infrastructure_mode(
            "database_mode", {"ldm_version": "2.13.0"}, defaults
        )
        == "isolated"
    )

    # 3. Old projects without a version (0.0.0) enforce "isolated" database mode
    assert resolve_infrastructure_mode("database_mode", {}, defaults) == "isolated"

    # 4. Old projects (pre-2.14.0) enforce "sidecar" search mode regardless of the default
    assert (
        resolve_infrastructure_mode("search_mode", {"ldm_version": "2.13.0"}, defaults)
        == "sidecar"
    )


class TestDownloadFile(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temp_dir = tempfile.mkdtemp()
        self.dest_path = Path(self.temp_dir) / "downloaded.bin"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir)

    @patch("requests.get")
    def test_download_file_success(self, mock_get):
        """Verify download_file succeeds and cleans up tmp files on success."""
        from ldm_core.utils import download_file

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"chunk1", b"chunk2"]
        mock_get.return_value = mock_resp

        success = download_file("https://example.com/file", self.dest_path)
        self.assertTrue(success)
        self.assertTrue(self.dest_path.exists())
        self.assertEqual(self.dest_path.read_bytes(), b"chunk1chunk2")
        self.assertFalse(self.dest_path.with_suffix(".download_tmp").exists())

    @patch("requests.get")
    def test_download_file_failure_unlinked(self, mock_get):
        """Verify download_file deletes temporary files if the download fails/crashes."""
        from ldm_core.utils import download_file

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        # Iterator raises an exception halfway through
        def mock_iter_content(chunk_size=8192):
            yield b"partial_data"
            raise RuntimeError("Network disconnected")

        mock_resp.iter_content = mock_iter_content
        mock_get.return_value = mock_resp

        with patch("ldm_core.ui.UI.error") as mock_err:
            success = download_file("https://example.com/file", self.dest_path)
            self.assertFalse(success)
            self.assertFalse(self.dest_path.exists())
            self.assertFalse(self.dest_path.with_suffix(".download_tmp").exists())
            mock_err.assert_called()

    def test_download_file_invalid_scheme(self):
        """Verify invalid URL scheme fails immediately."""
        from ldm_core.utils import download_file

        success = download_file("http://unsafe-url.com/file", self.dest_path)
        self.assertFalse(success)
        self.assertFalse(self.dest_path.exists())

    def test_save_global_config_permissions(self):
        """Verify save_global_config_safe enforces restricted permissions (0600 / 0700)."""
        import json
        import platform
        import tempfile

        from ldm_core.utils import save_global_config_safe

        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config_dir" / "global.config"
            data = {"key": "value"}

            success = save_global_config_safe(config_path, data)
            self.assertTrue(success)
            self.assertTrue(config_path.exists())

            content = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(content, data)

            if platform.system().lower() != "windows":
                self.assertEqual(config_path.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

    def test_safe_write_text_mode_permissions(self):
        """Verify safe_write_text enforces specified mode permissions."""
        import platform
        import tempfile

        from ldm_core.utils import safe_write_text

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "secure.txt"
            safe_write_text(file_path, "secret content", mode=0o600)
            self.assertTrue(file_path.exists())
            self.assertEqual(file_path.read_text(encoding="utf-8"), "secret content")

            if platform.system().lower() != "windows":
                self.assertEqual(file_path.stat().st_mode & 0o777, 0o600)

    def test_is_safe_path(self):
        """Verify is_safe_path correctly identifies safe vs unsafe members and symlinks."""
        import tempfile

        from ldm_core.utils import is_safe_path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()

            # Safe paths
            self.assertTrue(is_safe_path(root, "file.txt"))
            self.assertTrue(is_safe_path(root, "subdir/file.txt"))

            # Paths containing ".." traversal segments are rejected
            self.assertFalse(is_safe_path(root, "subdir/../file.txt"))

            # Unsafe paths (direct traversal)
            self.assertFalse(is_safe_path(root, "../outside.txt"))
            self.assertFalse(is_safe_path(root, "/absolute/path"))
            self.assertFalse(is_safe_path(root, "subdir/../../outside.txt"))

            # Safe symlinks (resolve within target root)
            self.assertTrue(
                is_safe_path(root, "link_to_file", is_link=True, link_target="file.txt")
            )
            self.assertTrue(
                is_safe_path(
                    root,
                    "subdir/link_to_parent",
                    is_link=True,
                    link_target="../file.txt",
                )
            )

            # Unsafe symlinks (resolve outside target root)
            self.assertFalse(
                is_safe_path(
                    root,
                    "link_to_outside",
                    is_link=True,
                    link_target="../outside.txt",
                )
            )
            self.assertFalse(
                is_safe_path(
                    root,
                    "subdir/link_to_outside",
                    is_link=True,
                    link_target="../../outside.txt",
                )
            )
            self.assertFalse(
                is_safe_path(
                    root,
                    "link_to_absolute",
                    is_link=True,
                    link_target="/etc/passwd",
                )
            )

    def test_safe_extract_zip_and_tar_slip_prevention(self):
        """Verify safe_extract raises ValueError if traversal or unsafe symlink is found in Zip/Tar."""
        import tarfile
        import zipfile

        from ldm_core.utils import safe_extract

        # 1. Test Zip file with unsafe member
        mock_zip = MagicMock(spec=zipfile.ZipFile)
        mock_zip.namelist.return_value = ["safe.txt", "../unsafe.txt"]

        # infolist returns ZipInfo objects
        zip_info1 = MagicMock()
        zip_info1.filename = "safe.txt"
        zip_info1.external_attr = 0

        zip_info2 = MagicMock()
        zip_info2.filename = "../unsafe.txt"
        zip_info2.external_attr = 0

        mock_zip.infolist.return_value = [zip_info1, zip_info2]

        with self.assertRaises(ValueError) as ctx:
            safe_extract(mock_zip, "/tmp/extract_target")
        self.assertIn("Security Block", str(ctx.exception))
        mock_zip.extractall.assert_not_called()

        # 2. Test Tar file with unsafe symlink
        class MockTarMember:
            def __init__(self, name, issym=False, islnk=False, linkname=""):
                self.name = name
                self._issym = issym
                self._islnk = islnk
                self.linkname = linkname

            def issym(self):
                return self._issym

            def islnk(self):
                return self._islnk

        mock_tar = MagicMock(spec=tarfile.TarFile)
        member1 = MockTarMember("safe.txt")
        member2 = MockTarMember(
            "link_to_outside", issym=True, linkname="../outside.txt"
        )
        mock_tar.getmembers.return_value = [member1, member2]

        with self.assertRaises(ValueError) as ctx:
            safe_extract(mock_tar, "/tmp/extract_target")
        self.assertIn("Security Block", str(ctx.exception))
        mock_tar.extractall.assert_not_called()


class TestWindowsDriveRootSafety(unittest.TestCase):
    """Tests that verify_safe_to_delete() blocks Windows drive roots and UNC paths.

    All tests mock platform.system() to return 'Windows' so they run safely
    on macOS/Linux CI environments without risk of real deletion.
    Uses a patched internal helper to inject resolved Windows paths without
    interfering with the home-directory check.
    """

    def _call_windows_safety(self, path_obj):
        """Invoke the Windows-specific safety gates from verify_safe_to_delete.

        Replicates the Windows block directly using PureWindowsPath objects
        so tests run on macOS/Linux CI without needing a real Windows environment.
        """

        def inner():
            path_str = str(path_obj)
            # UNC check must come first (UNC roots also have len(parts)==1)
            if path_str.startswith("\\\\"):
                raise ValueError(
                    f"Safety Violation: Cannot delete UNC path root: {path_obj}"
                )
            parts = path_obj.parts
            if len(parts) <= 1:
                raise ValueError(
                    f"Safety Violation: Cannot delete Windows drive root: {path_obj}"
                )
            # Windows system directories blocklist

            windows_system = [
                "C:\\Windows",
                "C:\\Program Files",
                "C:\\Program Files (x86)",
                "C:\\Users",
                "C:\\ProgramData",
            ]
            if path_str in windows_system:
                raise ValueError(
                    f"Safety Violation: Cannot delete system directory: {path_obj}"
                )

        inner()

    def test_windows_drive_root_c_raises(self):
        """verify_safe_to_delete must raise ValueError for C:\\ drive root (1 part on Windows)."""
        from pathlib import PureWindowsPath

        c_root = PureWindowsPath("C:\\")
        with self.assertRaises(ValueError) as ctx:
            self._call_windows_safety(c_root)
        self.assertIn("drive root", str(ctx.exception).lower())

    def test_windows_drive_root_d_raises(self):
        """verify_safe_to_delete must raise ValueError for D:\\ drive root."""
        from pathlib import PureWindowsPath

        d_root = PureWindowsPath("D:\\")
        with self.assertRaises(ValueError) as ctx:
            self._call_windows_safety(d_root)
        self.assertIn("drive root", str(ctx.exception).lower())

    def test_windows_unc_path_raises(self):
        """verify_safe_to_delete must raise ValueError for UNC paths."""
        from pathlib import PureWindowsPath

        unc = PureWindowsPath("\\\\server\\share")
        with self.assertRaises(ValueError) as ctx:
            self._call_windows_safety(unc)
        self.assertIn("UNC", str(ctx.exception))

    def test_windows_valid_project_path_does_not_raise_drive_root(self):
        """A valid user project path must NOT be blocked by the drive-root gate."""
        from pathlib import PureWindowsPath

        project = PureWindowsPath("C:\\projects\\my-ldm-project")
        # Should not raise a drive-root or UNC ValueError
        self._call_windows_safety(project)  # Must complete without raising

    def test_windows_system_directory_blocked_by_system_roots(self):
        """C:\\Windows must be blocked by the Windows system directories blocklist."""
        from pathlib import PureWindowsPath

        windows_dir = PureWindowsPath("C:\\Windows")
        with self.assertRaises(ValueError) as ctx:
            self._call_windows_safety(windows_dir)
        self.assertIn("Safety Violation", str(ctx.exception))


class TestFetchWithRetry(unittest.TestCase):
    """Tests the retry behavior of get_json() and get_raw() under transient errors/rate limits."""

    @patch("ldm_core.utils.requests.get")
    @patch("ldm_core.utils.time.sleep")
    def test_fetch_retry_on_rate_limit(self, mock_sleep, mock_get):
        """Test that get_json() retries when rate limited (429) and eventually succeeds."""
        # 1st response: 429 with Retry-After header
        # 2nd response: 200 with JSON payload
        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_resp_429.headers = {"Retry-After": "3"}

        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.json.return_value = {"status": "ok"}

        mock_get.side_effect = [mock_resp_429, mock_resp_200]

        result = get_json("https://api.github.com/repos/liferay/releases")

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once_with(3)

    @patch("ldm_core.utils.requests.get")
    @patch("ldm_core.utils.time.sleep")
    def test_fetch_retry_on_transient_error(self, mock_sleep, mock_get):
        """Test that get_json() retries on transient connection timeouts and succeeds."""
        # 1st response: raises Timeout
        # 2nd response: raises ConnectionError
        # 3rd response: 200 with JSON payload
        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.json.return_value = {"version": "2.0"}

        mock_get.side_effect = [
            requests.exceptions.Timeout("Timeout"),
            requests.exceptions.ConnectionError("Connection lost"),
            mock_resp_200,
        ]

        result = get_json("https://api.github.com/releases")

        self.assertEqual(result, {"version": "2.0"})
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("ldm_core.utils.requests.get")
    @patch("ldm_core.utils.time.sleep")
    def test_fetch_fails_after_max_retries(self, mock_sleep, mock_get):
        """Test that get_json() returns None and stops retrying after max_retries limit is exceeded."""
        mock_get.side_effect = requests.exceptions.Timeout("Persistent timeout")

        result = get_json("https://api.github.com/releases")

        self.assertIsNone(result)
        self.assertEqual(mock_get.call_count, 3)  # max_retries default is 3
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("ldm_core.utils.requests.get")
    @patch("ldm_core.utils.time.sleep")
    def test_get_raw_resilience(self, mock_sleep, mock_get):
        """Test that get_raw() also benefits from the retry wrapper."""
        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.text = "raw_content"

        mock_get.side_effect = [
            requests.exceptions.Timeout("Timeout"),
            mock_resp_200,
        ]

        result = get_raw("https://api.github.com/raw")

        self.assertEqual(result, "raw_content")
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once()


class TestCommandRunner(unittest.TestCase):
    def setUp(self):
        import os

        from ldm_core.utils import get_runner

        self.original_runner = get_runner()
        self.original_dry_run = os.environ.get("LDM_DRY_RUN")

    def tearDown(self):
        import os

        from ldm_core.utils import set_runner

        set_runner(self.original_runner)
        if self.original_dry_run is not None:
            os.environ["LDM_DRY_RUN"] = self.original_dry_run
        else:
            os.environ.pop("LDM_DRY_RUN", None)

    def test_default_runner_resolution(self):
        import os

        from ldm_core.utils import (
            CommandRunner,
            DryRunCommandRunner,
            get_runner,
            set_runner,
        )

        set_runner(None)

        # Test default resolution (no dry-run env)
        os.environ.pop("LDM_DRY_RUN", None)
        runner = get_runner()
        self.assertIsInstance(runner, CommandRunner)
        self.assertNotIsInstance(runner, DryRunCommandRunner)

        # Test dry-run resolution via env var
        os.environ["LDM_DRY_RUN"] = "true"
        runner = get_runner()
        self.assertIsInstance(runner, DryRunCommandRunner)

    def test_explicit_runner_set(self):
        from ldm_core.utils import CommandRunner, get_runner, set_runner

        custom_runner = CommandRunner()
        set_runner(custom_runner)
        self.assertEqual(get_runner(), custom_runner)

    @patch("subprocess.run")
    def test_command_runner_execution(self, mock_run):
        from ldm_core.utils import CommandRunner

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"success_output\n"
        mock_run.return_value = mock_result

        runner = CommandRunner()
        output = runner.run(["echo", "hello"], capture_output=True)
        self.assertEqual(output, "success_output")
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_command_runner_strips_docker_host_for_remote_context(self, mock_run):
        # LDM-#1090: DOCKER_HOST (often inherited from the calling shell, e.g.
        # Colima's `eval $(colima env)`) takes precedence over --context per
        # Docker's own connection-resolution rules, silently redirecting a
        # deliberately-targeted remote --context command back to the local
        # socket. Must be stripped whenever --context is present.
        import os

        from ldm_core.utils import CommandRunner

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b""
        mock_run.return_value = mock_result

        env_with_docker_host = {
            **os.environ,
            "DOCKER_HOST": "unix:///Users/me/.colima/default/docker.sock",
        }
        runner = CommandRunner(env=env_with_docker_host)
        runner.run(["docker", "--context", "aws-1", "ps"], capture_output=True)

        actual_env = mock_run.call_args.kwargs["env"]
        self.assertNotIn("DOCKER_HOST", actual_env)

    @patch("subprocess.run")
    def test_command_runner_keeps_docker_host_for_local_commands(self, mock_run):
        # A plain (non-remote-context) docker command must not have
        # DOCKER_HOST stripped -- that would be a real behavior change for
        # anyone deliberately using DOCKER_HOST for their own local setup.
        import os

        from ldm_core.utils import CommandRunner

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b""
        mock_run.return_value = mock_result

        env_with_docker_host = {
            **os.environ,
            "DOCKER_HOST": "unix:///var/run/docker.sock",
        }
        runner = CommandRunner(env=env_with_docker_host)
        runner.run(["docker", "ps"], capture_output=True)

        actual_env = mock_run.call_args.kwargs["env"]
        self.assertEqual(actual_env["DOCKER_HOST"], "unix:///var/run/docker.sock")

    @patch("ldm_core.utils.UI.die", side_effect=SystemExit)
    def test_command_runner_shell_sanitization(self, mock_die):
        from ldm_core.utils import CommandRunner

        runner = CommandRunner()
        # Bad shell command should trigger security violation
        with self.assertRaises(SystemExit):
            runner.run("rm -rf /; dangerous", shell=True)
        mock_die.assert_called_once_with(
            "Security Violation: Shell command contains forbidden character ';'"
        )

    def test_dry_run_command_runner(self):
        from ldm_core.utils import DryRunCommandRunner

        runner = DryRunCommandRunner()

        # MemTotal check
        self.assertEqual(
            runner.run(["cat", "/proc/meminfo", "MemTotal"]), "17179869184"
        )

        # JSON inspect
        self.assertEqual(
            runner.run(["docker", "info", "--format", "{{json .}}"]),
            '{"MemTotal": 17179869184}',
        )

        # Context show
        self.assertEqual(runner.run(["docker", "context", "show"]), "default")

        # Docker inspect status
        self.assertEqual(
            runner.run(["docker", "inspect", "-f", "{{.State.Status}}", "container"]),
            "running",
        )
        self.assertEqual(runner.run(["docker", "inspect", "container"]), "[]")


class TestZeroDependencyVersionParsing(unittest.TestCase):
    def test_version_parsing_fallback(self):
        # We can test the fallback directly by temporarily mocking sys.modules['packaging']
        import builtins

        from ldm_core.utils import resolve_infrastructure_mode

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "packaging.version":
                raise ImportError("Mocked missing packaging")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = mock_import
        try:
            # v2.13.0 falls back to legacy modes
            self.assertEqual(
                resolve_infrastructure_mode(
                    "database_mode", {"ldm_version": "2.13.0"}, {}
                ),
                "isolated",
            )
            self.assertEqual(
                resolve_infrastructure_mode(
                    "search_mode", {"ldm_version": "v2.13.9-pre.1"}, {}
                ),
                "sidecar",
            )

            # v2.14.0+ uses modern defaults
            self.assertEqual(
                resolve_infrastructure_mode(
                    "database_mode",
                    {"ldm_version": "2.14.0"},
                    {"database_mode": "shared"},
                ),
                "shared",
            )
            self.assertEqual(
                resolve_infrastructure_mode(
                    "search_mode",
                    {"ldm_version": "v2.15.18-pre.1"},
                    {"search_mode": "shared"},
                ),
                "shared",
            )

            # Invalid/empty versions default to 0.0.0 and use legacy fallback
            self.assertEqual(
                resolve_infrastructure_mode(
                    "database_mode", {"ldm_version": "invalid"}, {}
                ),
                "isolated",
            )
            self.assertEqual(
                resolve_infrastructure_mode("database_mode", {}, {}), "isolated"
            )

        finally:
            builtins.__import__ = original_import


class TestMetadataParsing(unittest.TestCase):
    def setUp(self):
        import os
        import shutil
        import tempfile

        self.shutil = shutil
        self.os = os
        # Create a temporary directory for meta file tests
        self.test_dir = tempfile.mkdtemp()
        self.meta_path = Path(self.test_dir) / ".liferay-docker.meta"

        # Disable dry run for these tests and save old state
        self.old_dry_run = os.environ.get("LDM_DRY_RUN")
        if "LDM_DRY_RUN" in os.environ:
            del os.environ["LDM_DRY_RUN"]

    def tearDown(self):
        self.shutil.rmtree(self.test_dir)
        if self.old_dry_run is not None:
            self.os.environ["LDM_DRY_RUN"] = self.old_dry_run

    def test_read_meta_legacy_properties_auto_upgrades(self):
        from ldm_core.utils import read_meta

        # Write legacy format
        legacy_content = "tag=2026.q1\nport=8080\nscale_liferay=3\nenabled=true"
        self.meta_path.write_text(legacy_content, encoding="utf-8")

        # read_meta should read properties, infer types, and auto-upgrade to JSON
        meta = read_meta(self.meta_path)

        self.assertEqual(meta["tag"], "2026.q1")
        self.assertEqual(meta["port"], "8080")
        self.assertEqual(meta["scale_liferay"], "3")
        self.assertEqual(meta["enabled"], True)

        # Verify the file on disk is now JSON
        new_content = self.meta_path.read_text(encoding="utf-8")
        self.assertTrue(new_content.startswith("{"))
        import json

        json_meta = json.loads(new_content)
        self.assertEqual(json_meta["tag"], "2026.q1")
        self.assertEqual(json_meta["scale_liferay"], "3")

    def test_read_meta_json(self):
        import json

        from ldm_core.utils import read_meta

        # Write JSON format
        json_content = json.dumps({"tag": "2026.q1", "port": 8080, "scale_liferay": 3})
        self.meta_path.write_text(json_content, encoding="utf-8")

        # read_meta should read JSON natively
        meta = read_meta(self.meta_path)

        self.assertEqual(meta["tag"], "2026.q1")
        self.assertEqual(meta["port"], 8080)
        self.assertEqual(meta["scale_liferay"], 3)

    def test_read_meta_self_heals_invalid_port_on_disk(self):
        # LDM-#1119: a corrupted port value (e.g. a stray test double's
        # repr somehow persisted into a real meta file) must not keep
        # re-triggering the same warning on every subsequent read forever
        # -- the fix (fall back to 8080) needs to be written back to disk,
        # matching the legacy-format auto-upgrade's existing self-heal
        # pattern in this same function.
        import json

        from ldm_core.utils import read_meta

        json_content = json.dumps(
            {
                "tag": "2026.q1",
                "container_name": "my-project",
                "db_type": "postgresql",
                "port": "<MagicMock name='mock.port' id='123'>",
            }
        )
        self.meta_path.write_text(json_content, encoding="utf-8")

        with patch("ldm_core.ui.UI.warning") as mock_warn:
            meta = read_meta(self.meta_path)
            self.assertEqual(meta["port"], 8080)
            self.assertTrue(
                any("Invalid port value" in c.args[0] for c in mock_warn.call_args_list)
            )

        # The corrected value must now be persisted -- a second read
        # should be clean, with no "Invalid port value" warning fired again.
        with patch("ldm_core.ui.UI.warning") as mock_warn_second:
            meta_again = read_meta(self.meta_path)
            self.assertEqual(meta_again["port"], 8080)
            self.assertFalse(
                any(
                    "Invalid port value" in c.args[0]
                    for c in mock_warn_second.call_args_list
                )
            )

    def test_write_meta(self):
        from ldm_core.utils import write_meta

        meta_dict = {"tag": "2026.q1", "port": 8080, "scale_liferay": 3}
        write_meta(self.meta_path, meta_dict)

        # Verify the file on disk is valid JSON
        new_content = self.meta_path.read_text(encoding="utf-8")
        self.assertTrue(new_content.startswith("{"))
        import json

        json_meta = json.loads(new_content)
        self.assertEqual(json_meta["tag"], "2026.q1")
        self.assertEqual(json_meta["scale_liferay"], 3)

    def test_sanitize_id_umlauts_and_unicode(self):
        """Verify sanitize_id transcodes German umlauts and accents into RFC-1123 safe ASCII strings."""
        from ldm_core.utils import sanitize_id

        # German umlauts and Eszett
        self.assertEqual(sanitize_id("Saarbrücken"), "Saarbruecken")
        self.assertEqual(sanitize_id("München-Süd"), "Muenchen-Sued")
        self.assertEqual(sanitize_id("Groß-Umstadt"), "Gross-Umstadt")
        self.assertEqual(sanitize_id("Kölner-Liferay"), "Koelner-Liferay")

        # Accented characters
        self.assertEqual(sanitize_id("Café-Liferay"), "Cafe-Liferay")
        self.assertEqual(sanitize_id("España_PR"), "Espana_PR")

        # Non-latin script fallback
        self.assertTrue(sanitize_id("日本語").startswith("project-"))

        # Standard ASCII remains intact
        self.assertEqual(sanitize_id("vanilla-7.4-app"), "vanilla-7.4-app")

    def test_sanitize_id_stroked_letters_are_not_dropped(self):
        """LDM-#1308: stroked letters are atomic, so NFKD cannot decompose them.

        "l" with stroke (U+0142) is a single codepoint, not "l" plus a combining
        mark, so NFKD leaves it intact and the ASCII-ignore step discards it
        outright. Before the explicit mapping, a Polish project name silently
        lost a letter and collided with any real project already using the
        shortened form; a Vietnamese one lost its leading consonant entirely.
        """
        from ldm_core.utils import sanitize_id

        self.assertEqual(sanitize_id("Żółć"), "Zolc")
        self.assertEqual(sanitize_id("Được"), "Duoc")
        self.assertEqual(sanitize_id("Łódź"), "Lodz")
        self.assertEqual(sanitize_id("Smørrebrød"), "Smorrebrod")
        self.assertEqual(sanitize_id("Þór"), "THor")

    def test_sanitize_id_real_world_project_names(self):
        """Names chosen for distinct diacritic conventions.

        Each exercises a different path: German two-for-one expansion, plain
        accent stripping, stroked letters, and a long Finnish compound that
        stresses length once umlauts expand.
        """
        from ldm_core.utils import sanitize_id

        cases = {
            "Żółć": "Zolc",
            "Hétérogénéité": "Heterogeneite",
            "Được": "Duoc",
            "Jäääär": "Jaeaeaeaer",
            "Märchenerzähler": "Maerchenerzaehler",
            "Käsespätzle": "Kaesespaetzle",
            "Epäjärjestelmällistyttämättömyydellänsäkäänköhän": (
                "Epaejaerjestelmaellistyttaemaettoemyydellaensaekaeaenkoehaen"
            ),
        }
        for raw, expected in cases.items():
            with self.subTest(name=raw):
                self.assertEqual(sanitize_id(raw), expected)

    def test_sanitize_id_output_is_always_a_valid_container_name(self):
        """The property that actually matters, whatever the user types.

        Docker requires [a-zA-Z0-9][a-zA-Z0-9_.-]* for a container name, and the
        result is also used as a volume prefix. Scripts with no Latin equivalent
        must fall back rather than produce an empty string.
        """
        import re

        from ldm_core.utils import sanitize_id

        docker_name = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
        for raw in [
            "Żółć",
            "Được",
            "Epäjärjestelmällistyttämättömyydellänsäkäänköhän",
            "日本語プロジェクト",
            "Проект",
            "مشروع",
            "Ελληνικά",
            "🚀rocket",
            "ł",
            "Đ",
        ]:
            with self.subTest(name=raw):
                result = sanitize_id(raw)
                self.assertTrue(result, f"{raw!r} sanitized to an empty string")
                self.assertRegex(result, docker_name)

    def test_sanitize_id_non_latin_fallback_is_deterministic(self):
        """Re-running `ldm run` must resolve to the same container and volumes.

        The hash fallback for non-Latin scripts therefore cannot be random.
        """
        from ldm_core.utils import sanitize_id

        for raw in ["日本語プロジェクト", "Проект", "Ελληνικά"]:
            with self.subTest(name=raw):
                first = sanitize_id(raw)
                self.assertEqual(first, sanitize_id(raw))
                self.assertTrue(first.startswith("project-"))


class TestSharedDatabaseName(unittest.TestCase):
    """LDM-#1354: the shared-mode database name must always be lowercase."""

    def test_a_capitalised_project_is_lowercased(self):
        """PostgreSQL folds an unquoted CREATE, so anything else cannot match."""
        from ldm_core.utils import shared_database_name

        self.assertEqual("lportal_myproject", shared_database_name("MyProject"))

    def test_transcoded_characters_are_lowercased_too(self):
        from ldm_core.utils import shared_database_name

        # sanitize_id expands u-umlaut to "ue" per German convention.
        self.assertEqual("lportal_saarbruecken", shared_database_name("Saarbrücken"))

    def test_hyphens_become_underscores(self):
        from ldm_core.utils import shared_database_name

        self.assertEqual("lportal_poc_client", shared_database_name("poc-client"))

    def test_an_already_safe_name_is_unchanged(self):
        from ldm_core.utils import shared_database_name

        self.assertEqual("lportal_myproject", shared_database_name("myproject"))

    def test_it_is_idempotent_on_its_own_output(self):
        """Call sites disagree about their input; re-deriving must not drift."""
        from ldm_core.utils import shared_database_name

        once = shared_database_name("MyProject")
        self.assertEqual("lportal_lportal_myproject", shared_database_name(once))
        self.assertEqual(once.lower(), once)

    def test_the_result_is_a_valid_postgres_identifier(self):
        """Lowercase, and nothing an unquoted CREATE DATABASE would alter."""

        from ldm_core.utils import shared_database_name

        for name in ("MyProject", "Saarbrücken", "Żółć", "poc-client", "a.b"):
            derived = shared_database_name(name)
            self.assertEqual(derived, derived.lower(), name)
            self.assertRegex(derived, r"^[a-z_][a-z0-9_.]*$", name)


class TestSharedDatabaseContainer(unittest.TestCase):
    """LDM-#1361: one resolver for "the global container for this engine".

    Five sites hardcoded `liferay-db-global` and three carried their own
    inline ternary. The point of the resolver is that they cannot disagree,
    so these tests pin the mapping rather than any one call site.
    """

    def test_postgresql_keeps_its_existing_name(self):
        """A rename would break anyone scripting against the container."""
        from ldm_core.utils import shared_database_container

        self.assertEqual("liferay-db-global", shared_database_container("postgresql"))

    def test_mysql_and_mariadb_share_one_container(self):
        """Both emit an identical jdbc:mariadb:// URL, so one server serves both."""
        from ldm_core.utils import shared_database_container

        self.assertEqual("liferay-db-mysql-global", shared_database_container("mysql"))
        self.assertEqual(
            "liferay-db-mysql-global", shared_database_container("mariadb")
        )

    def test_the_engines_do_not_collide(self):
        """The #1357 defect was one name serving two incompatible engines."""
        from ldm_core.utils import shared_database_container

        self.assertNotEqual(
            shared_database_container("postgresql"),
            shared_database_container("mysql"),
        )

    def test_an_absent_or_unknown_engine_falls_back_to_postgresql(self):
        """Preserves every pre-#1361 call site, including a meta with no db_type."""
        from ldm_core.utils import shared_database_container

        for value in (None, "", "postgres", "sqlite", "hypersonic"):
            with self.subTest(db_type=value):
                self.assertEqual("liferay-db-global", shared_database_container(value))

    def test_the_engine_is_matched_case_insensitively(self):
        """`db_type` reaches this from meta, CLI args and snapshot metadata."""
        from ldm_core.utils import shared_database_container

        self.assertEqual("liferay-db-mysql-global", shared_database_container("MySQL"))
        self.assertEqual("liferay-db-global", shared_database_container("PostgreSQL"))

    def test_the_volume_is_derived_from_the_container(self):
        """Derived, not tabulated, so an engine cannot gain one without the other."""
        from ldm_core.utils import (
            SHARED_DB_CONTAINERS,
            shared_database_container,
            shared_database_volume,
        )

        # The PostgreSQL value is what `ldm nuke` and the E2E suite know.
        self.assertEqual("liferay-db-global-data", shared_database_volume("postgresql"))
        self.assertEqual(
            "liferay-db-mysql-global-data", shared_database_volume("mysql")
        )
        for engine in SHARED_DB_CONTAINERS:
            with self.subTest(engine=engine):
                self.assertEqual(
                    f"{shared_database_container(engine)}-data",
                    shared_database_volume(engine),
                )

    def test_shared_capability_excludes_hypersonic_and_external(self):
        from ldm_core.utils import is_shared_capable_db

        for engine in ("postgresql", "mysql", "mariadb"):
            with self.subTest(engine=engine):
                self.assertTrue(is_shared_capable_db(engine))
        for engine in ("hypersonic", "external", None, ""):
            with self.subTest(engine=engine):
                self.assertFalse(is_shared_capable_db(engine))


class TestDnsLabel(unittest.TestCase):
    """LDM-#1356: the tunnel subdomain must be a valid DNS label."""

    def test_non_ascii_is_transcoded_and_lowercased(self):
        from ldm_core.utils import dns_label

        self.assertEqual("saarbruecken", dns_label("Saarbrücken"))
        self.assertEqual("zolc", dns_label("Żółć"))
        self.assertEqual("duoc", dns_label("Được"))

    def test_underscores_and_dots_become_hyphens(self):
        """Both are legal in a Docker name and illegal in a DNS label."""
        from ldm_core.utils import dns_label

        self.assertEqual("my-project", dns_label("My_Project"))
        self.assertEqual("poc-client", dns_label("poc.client"))

    def test_leading_and_trailing_hyphens_are_stripped(self):
        from ldm_core.utils import dns_label

        self.assertEqual("weird", dns_label("--weird--"))

    def test_runs_of_separators_collapse(self):
        from ldm_core.utils import dns_label

        self.assertEqual("a-b", dns_label("a___b"))

    def test_it_is_truncated_to_the_label_limit(self):
        from ldm_core.utils import dns_label

        self.assertEqual(63, len(dns_label("a" * 80)))

    def test_truncation_cannot_leave_a_trailing_hyphen(self):
        """A label may not end in '-', including after the 63-char cut."""
        from ldm_core.utils import dns_label

        derived = dns_label(("ab-" * 30), max_length=5)
        self.assertFalse(derived.endswith("-"), derived)

    def test_nothing_usable_returns_empty(self):
        """Callers must treat this as 'no default', not pass it on."""
        from ldm_core.utils import dns_label

        self.assertEqual("", dns_label("___"))
        self.assertEqual("", dns_label(""))

    def test_an_already_valid_label_is_unchanged(self):
        from ldm_core.utils import dns_label

        self.assertEqual("ok-name", dns_label("ok-name"))

    def test_the_result_always_satisfies_the_label_grammar(self):

        from ldm_core.utils import dns_label

        for name in ("Saarbrücken", "My_Project", "--x--", "Żółć", "a" * 80, "9lives"):
            got = dns_label(name)
            if got:
                self.assertRegex(got, r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", name)
                self.assertLessEqual(len(got), 63, name)


class TestSearchSnapshotAcceptance(unittest.TestCase):
    """LDM-#1355: a snapshot Elasticsearch refused must not be reported as taken."""

    def _accepted(self, response):
        from ldm_core.snapshot.search import SearchSnapshotService

        return SearchSnapshotService._snapshot_accepted(response)

    def test_an_elasticsearch_rejection_is_detected(self):
        payload = json.dumps(
            {
                "error": {
                    "type": "invalid_snapshot_name_exception",
                    "reason": "Invalid snapshot name [X_1], must be lowercase",
                },
                "status": 400,
            }
        )
        self.assertFalse(self._accepted(payload))

    def test_an_acknowledgement_is_accepted(self):
        self.assertTrue(self._accepted(json.dumps({"accepted": True})))

    def test_no_response_is_treated_as_accepted(self):
        """docker exec curl can legitimately return nothing; do not cry wolf."""
        for empty in ("", None):
            self.assertTrue(self._accepted(empty))

    def test_a_truncated_error_payload_is_still_detected(self):
        self.assertFalse(self._accepted('{"error":"boom"'))

    def test_unrelated_chatter_is_not_a_failure(self):
        self.assertTrue(self._accepted("some non-json chatter"))


class TestRemoteContextFailureDiagnosis(unittest.TestCase):
    """LDM-#1345: a node we cannot reach gets a diagnosis, not an HTTP/SSH blob."""

    # The exact stderr observed on v2.17.0 with aws-1 powered down.
    REAL_STDERR = (
        'error during connect: Get "http://docker.example.com/v1.44/containers/'
        "json?all=1&filters=%7B%22label%22%3A%7B%22com.docker.compose.config-hash"
        "%22%3Atrue%2C%22com.docker.compose.project%3De2e-test-env%22%3Atrue%7D%7D"
        '": command [ssh -l ec2-user -o ConnectTimeout=30 -T -- 51.20.52.201 '
        "docker system dial-stdio] has exited with exit status 255, make sure "
        "the URL is valid, and Docker 18.09 or later is installed on the remote "
        "host: stderr=ssh: connect to host 51.20.52.201 port 22: Operation timed out"
    )
    REAL_CMD: typing.ClassVar[list[str]] = [
        "/opt/homebrew/bin/docker",
        "--context",
        "aws-1",
        "compose",
        "down",
        "-v",
        "--remove-orphans",
    ]

    def _diagnose(self, cmd, stderr):
        from ldm_core.utils import diagnose_remote_context_failure

        return diagnose_remote_context_failure(cmd, stderr)

    def test_the_observed_failure_names_node_user_host_and_cause(self):
        message, tip = self._diagnose(self.REAL_CMD, self.REAL_STDERR)
        self.assertEqual(
            "Cannot reach compute node 'aws-1' over SSH "
            "(ec2-user@51.20.52.201:22 timed out).",
            message,
        )
        # The tip must point at the commands that actually exist, and name the
        # node, because the usual cause is a stale stored host (LDM-#1346).
        self.assertIn("ldm target status aws-1", tip)
        self.assertIn("ldm target add aws-1 --host <ip> --user ec2-user", tip)

    def test_the_alarming_placeholder_host_is_not_repeated_back(self):
        """`docker.example.com` is a Docker placeholder, not a real endpoint."""
        message, tip = self._diagnose(self.REAL_CMD, self.REAL_STDERR)
        self.assertNotIn("docker.example.com", message)
        self.assertNotIn("docker.example.com", tip)
        self.assertNotIn("%7B%22label", message)

    def test_a_local_command_is_never_diagnosed_as_remote(self):
        """No --context means no node to blame, whatever the stderr says."""
        self.assertIsNone(
            self._diagnose(["docker", "compose", "down"], self.REAL_STDERR)
        )

    def test_an_ordinary_failure_still_falls_through_to_raw_stderr(self):
        """For most commands the stderr *is* the useful output -- do not eat it."""
        self.assertIsNone(
            self._diagnose(
                ["docker", "--context", "aws-1", "compose", "up"],
                "service 'liferay' has neither an image nor a build context",
            )
        )

    def test_empty_stderr_is_not_diagnosed(self):
        self.assertIsNone(self._diagnose(["docker", "--context", "aws-1", "ps"], ""))

    def test_other_ssh_causes_are_named_distinctly(self):
        cases = {
            "connect to host 10.0.0.5 port 22: Connection refused": "refused the connection",
            "connect to host 10.0.0.5 port 22: No route to host": "has no network route",
            "ssh: Permission denied (publickey).": "refused the SSH credentials",
            "ssh: Could not resolve hostname nope": "could not be resolved by DNS",
        }
        for stderr, expected in cases.items():
            with self.subTest(stderr=stderr):
                message, _ = self._diagnose(
                    ["docker", "--context", "aws-1", "ps"],
                    f"error during connect: ... stderr={stderr}",
                )
                self.assertIn(expected, message)

    def test_a_string_command_is_handled_as_well_as_a_list(self):
        """run_command accepts shell strings too; the parser must not assume a list."""
        message, _ = self._diagnose(
            "docker --context aws-1 compose down", self.REAL_STDERR
        )
        self.assertIn("aws-1", message)


class TestAnnounceRemoteTargets(unittest.TestCase):
    """LDM-#1341: say a remote node is involved *before* blocking on it."""

    def _announce(self, targets_by_path):
        """Runs the announcement, returning whatever reached UI.info."""
        from ldm_core.utils import announce_remote_targets

        manager = MagicMock()
        manager.read_meta.side_effect = lambda p: {
            "target": targets_by_path[Path(p).name]
        }

        def fake_prefix(name):
            return ["docker", "--context", name] if name != "local" else ["docker"]

        def make_target(name):
            # `MagicMock(name=...)` names the mock itself rather than setting a
            # `.name` attribute, so it has to be assigned after construction.
            target = MagicMock()
            target.name = name or "local"
            return target

        said: list[str] = []
        with (
            patch("ldm_core.config.get_active_target", side_effect=make_target),
            patch(
                "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
                side_effect=fake_prefix,
            ),
            patch("ldm_core.ui.UI.info", side_effect=said.append),
        ):
            announce_remote_targets(manager, [Path("/p") / n for n in targets_by_path])
        return said

    def test_nothing_is_said_when_everything_is_local(self):
        """Silence is correct here -- a local command has nothing to wait for."""
        self.assertEqual([], self._announce({"alpha": "local", "beta": "local"}))

    def test_a_single_remote_project_is_announced_with_its_node(self):
        said = self._announce({"e2e-test-env": "aws-1"})
        self.assertEqual(1, len(said))
        self.assertIn("1 project targets", said[0])
        self.assertIn("e2e-test-env -> aws-1", said[0])
        self.assertIn("will wait if the node is asleep", said[0])

    def test_it_is_said_once_for_the_command_not_once_per_project(self):
        said = self._announce({"alpha": "aws-1", "beta": "aws-2", "gamma": "local"})
        self.assertEqual(1, len(said), "expected a single announcement")
        self.assertIn("2 projects target", said[0])
        self.assertIn("alpha -> aws-1", said[0])
        self.assertIn("beta -> aws-2", said[0])
        self.assertNotIn("gamma", said[0], "local projects are not worth naming")

    def test_an_unreadable_meta_does_not_break_the_announcement(self):
        """Reporting a broken project is the calling loop's job, not this one's."""
        from ldm_core.utils import announce_remote_targets

        manager = MagicMock()
        manager.read_meta.side_effect = OSError("meta is gone")
        with patch("ldm_core.ui.UI.info") as info:
            announce_remote_targets(manager, [Path("/p/alpha")])
        info.assert_not_called()


class TestRemoteFailureIsReportedNotDumped(unittest.TestCase):
    """LDM-#1345: the diagnosis must reach the user through the real run path.

    The helper being correct is not the fix -- `utils.py` printed `e.stderr`
    verbatim for every failing command, unconditionally, and that is the line
    that had to change.
    """

    def _run_failing_docker(self, cmd, stderr):
        """Drives the real CommandRunner with a failing subprocess."""
        import subprocess

        from ldm_core.utils import run_command

        errors: list[tuple] = []
        details: list[str] = []
        boom = subprocess.CalledProcessError(1, cmd, stderr=stderr)

        with (
            patch("subprocess.run", side_effect=boom),
            patch(
                "ldm_core.ui.UI.error", side_effect=lambda m, **k: errors.append((m, k))
            ),
            patch("ldm_core.ui.UI.detail", side_effect=details.append),
            patch("builtins.print") as printed,
        ):
            with self.assertRaises(SystemExit):
                run_command(cmd, check=True)
        return errors, details, printed

    def test_the_user_sees_a_diagnosis_and_not_the_blob(self):
        cmd = ["docker", "--context", "aws-1", "compose", "down"]
        errors, details, printed = self._run_failing_docker(
            cmd, TestRemoteContextFailureDiagnosis.REAL_STDERR
        )

        self.assertEqual(1, len(errors))
        message, kwargs = errors[0]
        self.assertIn("Cannot reach compute node 'aws-1' over SSH", message)
        self.assertIn("ldm target status aws-1", kwargs.get("tip", ""))

        # The old unconditional `print("Error Details: ...")` must be gone.
        printed.assert_not_called()

        # ...and the raw blob is retained, but only for --verbose/--info.
        # Asserting on `dial-stdio` rather than the placeholder hostname: the
        # marker is equally specific to the raw stderr, and a bare
        # `"<host>" in <str>` check trips CodeQL's
        # py/incomplete-url-substring-sanitization rule, which cannot tell a
        # test assertion from a security check on a URL.
        self.assertTrue(
            any("dial-stdio" in d for d in details),
            "raw stderr should still be available behind UI.detail",
        )

    def test_an_ordinary_failure_still_prints_its_stderr(self):
        """The regression risk of this change is swallowing useful output."""
        cmd = ["docker", "compose", "up"]
        errors, _, printed = self._run_failing_docker(
            cmd, "service 'liferay' has neither an image nor a build context"
        )
        self.assertIn("Command failed (Exit 1)", errors[0][0])
        printed.assert_called()
        self.assertIn("neither an image", str(printed.call_args))


class TestSearchIndexPrefix(unittest.TestCase):
    """LDM-#1353/#1355: one source for the index prefix.

    Three sites need it -- the config LDM writes, what `ldm info` reports, and
    the pattern snapshot/restore match on. `shared_database_name` had the same
    formula duplicated at nine sites and drifted, which is what broke
    `--database-mode shared` (#1354).
    """

    def test_it_matches_the_indices_liferay_actually_creates(self):
        """Observed on a running project: a configured `ldm-TrioTest-` produced
        `ldm-triotest-14683668377142-workflow-metrics-transitions`."""
        from ldm_core.utils import search_index_prefix

        observed = "ldm-triotest-14683668377142-workflow-metrics-transitions"
        self.assertTrue(observed.startswith(search_index_prefix("TrioTest")))

    def test_it_is_lowercase(self):
        """Liferay lowercases indexNamePrefix; writing it lowercase keeps what
        LDM records identical to what Liferay uses."""
        from ldm_core.utils import search_index_prefix

        for name in ("TrioTest", "Saarbrücken", "MiXeD"):
            got = search_index_prefix(name)
            self.assertEqual(got, got.lower(), name)

    def test_non_ascii_is_transcoded(self):
        from ldm_core.utils import search_index_prefix

        self.assertEqual("ldm-saarbruecken-", search_index_prefix("Saarbrücken"))

    def test_it_is_a_prefix_not_a_full_index_name(self):
        """LDM supplies only the prefix; Liferay appends the company ID."""
        from ldm_core.utils import search_index_prefix

        got = search_index_prefix("proj")
        self.assertTrue(got.startswith("ldm-"))
        self.assertTrue(got.endswith("-"))
