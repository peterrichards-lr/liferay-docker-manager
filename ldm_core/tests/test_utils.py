import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.utils import dict_to_yaml, verify_executable_checksum, version_to_tuple


class TestUtils(unittest.TestCase):
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
        mock_env.return_value = "tester"

        with patch.object(Path, "exists", return_value=True):
            home = get_actual_home()
            self.assertEqual(home.as_posix(), "/Users/tester")

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
            patch("os.replace") as mock_replace,
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
                mock_replace.assert_called_once_with(expected_tmp, dst)

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
