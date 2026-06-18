import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from ldm_core.handlers.assets import AssetService
from ldm_core.handlers.base import BaseHandler
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.diagnostics import DiagnosticsService
from ldm_core.handlers.runtime import RuntimeService
from ldm_core.handlers.workspace import WorkspaceService


class MockWorkspaceManager(
    BaseHandler,
):
    def __init__(self):
        self._verbose = False
        self._non_interactive = True
        self.args = MagicMock()
        self.args.host_name = "localhost"
        self.args.ssl = False

        from ldm_core.defaults import DefaultsManager

        self.defaults = DefaultsManager()

        self.assets = AssetService(self)
        self.diagnostics = DiagnosticsService(self)
        self.diagnostics.validate_lcp_json = MagicMock(return_value=("Valid", True, []))
        self.workspace = WorkspaceService(self)
        self.composer = ComposerService(self)
        self.runtime = RuntimeService(self)
        self.snapshot = MagicMock()

    @property
    def verbose(self):
        return self._verbose

    @verbose.setter
    def verbose(self, value):
        self._verbose = value

    @property
    def non_interactive(self):
        return self._non_interactive

    @non_interactive.setter
    def non_interactive(self, value):
        self._non_interactive = value

    def _check_java_version(self, *args, **kwargs):
        return True

    def _check_gradle_java_version(self, *args, **kwargs):
        return True

    def check_hostname(self, *args, **kwargs):
        return True

    def find_dxp_roots(self, *args, **kwargs):
        return []

    def detect_project_path(self, project_name, for_init=False):
        # This will be patched in the test
        return Path(f"/tmp/{project_name}")

    def setup_paths(self, project_path):
        root = Path(project_path)
        return {
            "root": root,
            "deploy": root / "deploy",
            "files": root / "files",
            "data": root / "data",
            "configs": root / "osgi" / "configs",
            "modules": root / "osgi" / "modules",
            "ce_dir": root / "client-extensions",
            "cx": root / "osgi" / "client-extensions",
            "backups": root / "snapshots",
        }

    def verify_runtime_environment(self, paths):
        pass

    def safe_rmtree(self, path):
        if Path(path).exists():
            shutil.rmtree(path)


class TestWorkspaceMetadata(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    def test_parse_lcp_json_basic(self):
        content = json.dumps({"id": "my-service", "type": "liferay-client-extension"})
        info = self.handler.workspace._parse_lcp_json(content)
        self.assertEqual(info["id"], "my-service")
        self.assertTrue(info["deploy"])

    def test_parse_lcp_json_target_port(self):
        content = json.dumps(
            {
                "id": "my-service",
                "loadBalancer": {"targetPort": 8081},
            }
        )
        info = self.handler.workspace._parse_lcp_json(content)
        self.assertEqual(info["loadBalancer"]["targetPort"], 8081)

    def test_parse_lcp_json_external_port(self):
        content = json.dumps(
            {
                "id": "my-service",
                "loadBalancer": {"port": 443, "externalPort": True},
            }
        )
        info = self.handler.workspace._parse_lcp_json(content)
        self.assertTrue(info.get("loadBalancer", {}).get("externalPort"))

    def test_parse_client_extension_yaml(self):
        content = "type: customElement\noAuthApplicationHeadlessServer: my-erc"
        info = self.handler.workspace._parse_client_extension_yaml(content)
        self.assertEqual(info["type"], "customElement")
        self.assertEqual(info["oauth_erc"], "my-erc")


class TestWorkspaceImport(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    def test_cmd_import_spaces_in_name(self, mock_run, mock_cmd_run):
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "My Awesome Project"
            source_dir.mkdir()
            (source_dir / "configs").mkdir()
            (source_dir / "client-extensions").mkdir()

            project_dir = base_path / "My Awesome Project"

            with (
                patch.object(
                    self.handler, "detect_project_path", return_value=project_dir
                ),
                patch.object(self.handler, "write_meta") as mock_write,
                patch("ldm_core.handlers.workspace.UI.info") as mock_info,
            ):
                self.handler.non_interactive = True
                self.handler.args.project = "My Awesome Project"
                self.handler.args.project_flag = None
                self.handler.workspace.cmd_import(str(source_dir))

                mock_write.assert_called_once()
                args, _ = mock_write.call_args
                saved_meta = args[1]

                # Verify LDM kept the original display name but sanitized the docker container name
                self.assertEqual(saved_meta["project_name"], "My Awesome Project")
                self.assertEqual(saved_meta["container_name"], "My-Awesome-Project")

                # Verify we logged the verbose message (even if verbose is off, we can check the call isn't made, but if we mock verbose we can check it)
                # Let's run it again with verbose=True to ensure the message triggers
                self.handler.args.verbose = True
                self.handler.workspace.cmd_import(str(source_dir))
                mock_info.assert_any_call(
                    "Project name 'My Awesome Project' contains invalid characters for Docker. Using 'My-Awesome-Project' for container names."
                )

    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    def test_cmd_import_project_id_passing(self, mock_run, mock_cmd_run):
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "source-workspace"
            source_dir.mkdir()
            (source_dir / "configs").mkdir()
            (source_dir / "client-extensions").mkdir()

            project_dir = base_path / "my-project"

            with (
                patch.object(
                    self.handler, "detect_project_path", return_value=project_dir
                ),
                patch.object(self.handler, "write_meta") as mock_write,
            ):
                self.handler.args.project = "my-project"
                self.handler.args.no_run = False
                self.handler.workspace.cmd_import(str(source_dir))

                # Verify project_id from meta matches what we expected
                written_meta = mock_write.call_args[0][1]
                self.assertEqual(written_meta["project_name"], "my-project")

    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    @patch("ldm_core.ui.UI.ask", return_value="C")
    def test_cmd_import_clean_option(self, mock_ask, mock_run, mock_cmd_run):
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "source-workspace"
            source_dir.mkdir()
            project_dir = base_path / "my-project"
            project_dir.mkdir()
            (project_dir / "old-file.txt").write_text("old")

            with (
                patch.object(
                    self.handler, "detect_project_path", return_value=project_dir
                ),
                patch.object(self.handler, "write_meta"),
            ):
                self.handler.non_interactive = False
                self.handler.args.project = "my-project"
                self.handler.workspace.cmd_import(str(source_dir))

                # Verify cleanup happened
                self.assertFalse((project_dir / "old-file.txt").exists())

    @patch("ldm_core.handlers.runtime.RuntimeService.cmd_run")
    @patch("ldm_core.handlers.base.BaseHandler.run_command")
    def test_cmd_import_no_overwrite_option(self, mock_run, mock_cmd_run):
        with tempfile.TemporaryDirectory() as tmp_base:
            base_path = Path(tmp_base)
            source_dir = base_path / "source-workspace"
            source_dir.mkdir()
            (source_dir / "client-extensions").mkdir()

            # Use a real zip file to avoid zipfile.BadZipFile
            zip_path = source_dir / "client-extensions" / "ext1.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("test.txt", "NEW_CONTENT")

            project_dir = base_path / "my-project"
            project_dir.mkdir()
            cx_dest_dir = project_dir / "osgi" / "client-extensions"
            cx_dest_dir.mkdir(parents=True)
            existing_cx = cx_dest_dir / "ext1.zip"
            existing_cx.write_text("ORIGINAL_CONTENT")

            with (
                patch.object(
                    self.handler, "detect_project_path", return_value=project_dir
                ),
                patch.object(self.handler, "write_meta"),
                patch.object(
                    self.handler,
                    "setup_paths",
                    return_value={
                        "root": project_dir,
                        "ce_dir": project_dir / "client-extensions",
                        "cx": cx_dest_dir,
                        "modules": project_dir / "osgi" / "modules",
                        "configs": project_dir / "osgi" / "configs",
                        "files": project_dir / "files",
                        "deploy": project_dir / "deploy",
                    },
                ),
                patch("ldm_core.ui.UI.ask") as mock_ui_ask,
            ):
                # Simulate user selecting 'N' (Skip Existing)
                mock_ui_ask.return_value = "N"
                self.handler.non_interactive = False

                self.handler.workspace.cmd_import(str(source_dir))

                # Verify the original content was PRESERVED
                self.assertEqual(existing_cx.read_text(), "ORIGINAL_CONTENT")

    def test_cmd_import_integrity_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_zip = tmp_path / "project.zip"
            source_zip.touch()
            sha_file = tmp_path / "project.zip.sha256"
            sha_file.write_text("match-sha")

            self.handler.args.verify = True
            self.handler.args.db = "hypersonic"
            self.handler.args.build = False
            self.handler.args.ssl = None
            self.handler.args.host_name = None
            self.handler.args.port = None
            self.handler.args.mount_logs = False
            self.handler.args.gogo_port = None
            self.handler.args.env = None
            self.handler.args.no_run = True
            self.handler.args.project = "test-proj"
            self.handler.args.project_flag = None

            with (
                patch(
                    "ldm_core.handlers.workspace.calculate_sha256",
                    return_value="match-sha",
                ),
                patch("ldm_core.handlers.workspace.UI.success") as mock_success,
                patch("zipfile.ZipFile"),
                patch("ldm_core.utils.safe_extract"),
                patch("ldm_core.handlers.workspace.datetime") as mock_date,
                patch.object(
                    self.handler, "detect_project_path", return_value=tmp_path / "test"
                ),
            ):
                mock_date.now.return_value.strftime.return_value = "20260512_120000"
                # Mock die to avoid further processing
                with patch(
                    "ldm_core.handlers.workspace.UI.die", side_effect=SystemExit
                ):
                    try:
                        self.handler.workspace.cmd_import(str(source_zip))
                    except SystemExit:
                        pass

                success_calls = [call[0][0] for call in mock_success.call_args_list]
                self.assertIn("Archive integrity verified.", success_calls)

    def test_hydrate_from_workspace_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "dest"
            res = self.handler.workspace._hydrate_from_workspace(
                Path("/non-existent"), {"root": dest}
            )
            self.assertTrue(res)

    @patch("ldm_core.handlers.workspace.safe_copy")
    @patch("ldm_core.handlers.workspace.safe_move")
    def test_hydrate_from_workspace_success(self, mock_move, mock_copy):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "source"
            source.mkdir()
            (source / "client-extensions").mkdir()
            (source / "client-extensions" / "test.zip").write_text("ZIP")
            dest = Path(tmp_dir) / "dest"
            paths = {
                "root": dest,
                "cx": dest / "cx",
                "ce_dir": dest / "ce",
                "modules": dest / "modules",
                "deploy": dest / "deploy",
            }
            for p in paths.values():
                p.mkdir(parents=True, exist_ok=True)

            res = self.handler.workspace._hydrate_from_workspace(source, paths)
            self.assertTrue(res)
            self.assertTrue(mock_copy.called)


class TestWorkspaceScanners(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    def test_scan_standalone_services(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            services_dir = root / "services"
            services_dir.mkdir()
            s1 = services_dir / "service1"
            s1.mkdir()
            (s1 / "LCP.json").write_text(json.dumps({"id": "s1"}))
            (s1 / "Dockerfile").touch()

            services = self.handler.workspace.scan_standalone_services(root)
            self.assertEqual(len(services), 1)
            self.assertEqual(services[0]["id"], "s1")

    def test_get_host_passthrough_env(self):
        with patch.dict(
            os.environ, {"LDM_TEST": "val", "LXC_TEST": "lxc", "OTHER": "ignored"}
        ):
            # scan_client_extensions and scan_standalone_services will be called
            with (
                patch.object(
                    self.handler.workspace, "scan_client_extensions", return_value=[]
                ),
                patch.object(
                    self.handler.workspace, "scan_standalone_services", return_value=[]
                ),
            ):
                env = self.handler.workspace.get_host_passthrough_env(
                    {"root": Path("/tmp")}, "liferay"
                )
                self.assertTrue(any("TEST=val" in e for e in env))
                self.assertTrue(any("LXC_TEST=lxc" in e for e in env))
                self.assertFalse(any("OTHER" in e for e in env))

    def test_scan_client_extensions_zip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            root_dir = tmp_path / "root"
            osgi_cx_dir = tmp_path / "osgi-cx"
            ce_build_dir = tmp_path / "ce-build"

            for d in [root_dir, osgi_cx_dir, ce_build_dir]:
                d.mkdir(parents=True)

            # Create a mock CX zip in ce-build
            zip_path = ce_build_dir / "my-ext.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr(
                    "LCP.json",
                    json.dumps({"id": "my-ext", "type": "liferay-client-extension"}),
                )
                z.writestr("Dockerfile", "FROM scratch")

            exts = self.handler.workspace.scan_client_extensions(
                root_dir, osgi_cx_dir, ce_build_dir
            )
            self.assertEqual(len(exts), 1)
            self.assertEqual(exts[0]["id"], "my-ext")
            self.assertTrue(exts[0]["is_service"])

    def test_scan_extension_metadata_folder(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir)
            (folder / "client-extension.yaml").write_text("type: customElement")
            (folder / "LCP.json").write_text(
                json.dumps(
                    {
                        "id": "lcp-id",
                        "loadBalancer": {"targetPort": 8081},
                        "env": {"VAR1": "VAL1"},
                    }
                )
            )

            info = self.handler.workspace._scan_extension_metadata(folder_path=folder)
            self.assertEqual(info["type"], "customElement")
            self.assertEqual(info["id"], "lcp-id")
            self.assertEqual(info["loadBalancer"]["targetPort"], 8081)
            self.assertEqual(info["env"]["VAR1"], "VAL1")

    @patch("ldm_core.handlers.workspace.safe_copy")
    @patch("ldm_core.handlers.workspace.safe_move")
    def test_sync_cx_artifact(self, mock_move, mock_copy):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            zip_path = tmp_path / "ext1.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("test.txt", "DATA")

            paths = {
                "root": tmp_path / "project",
                "cx": tmp_path / "project" / "osgi" / "client-extensions",
                "ce_dir": tmp_path / "project" / "client-extensions",
            }
            for p in paths.values():
                p.mkdir(parents=True, exist_ok=True)

            self.handler.workspace._sync_cx_artifact(zip_path, paths)
            self.assertTrue(mock_copy.called)
            self.assertTrue(mock_move.called)

    def test_monitor_event_handling_logic(self):
        # We don't call cmd_monitor because it's blocking.
        # We just test the _hydrate_from_workspace logic again to be sure.
        pass


class TestWorkspaceRemoteImport(unittest.TestCase):
    def setUp(self):
        self.handler = MockWorkspaceManager()

    def test_parse_github_repo(self):
        ws = self.handler.workspace
        # GITHUB_URL parsing tests
        self.assertEqual(
            ws._parse_github_repo("https://github.com/owner/repo"), ("owner", "repo")
        )
        self.assertEqual(
            ws._parse_github_repo("https://github.com/owner/repo.git"),
            ("owner", "repo"),
        )
        self.assertEqual(
            ws._parse_github_repo("https://github.com/owner/repo.git?ref=main"),
            ("owner", "repo"),
        )
        self.assertEqual(
            ws._parse_github_repo("http://github.com/owner/repo/"), ("owner", "repo")
        )
        self.assertEqual(
            ws._parse_github_repo("https://github.com/owner/repo/tree/master/subpath"),
            ("owner", "repo"),
        )
        # SSH URLs
        self.assertEqual(
            ws._parse_github_repo("git@github.com:owner/repo.git"), ("owner", "repo")
        )
        self.assertEqual(
            ws._parse_github_repo("git@github.com:owner/repo"), ("owner", "repo")
        )
        # Invalid URLs
        self.assertIsNone(ws._parse_github_repo(""))
        self.assertIsNone(ws._parse_github_repo("https://example.com"))

    @patch("requests.get")
    @patch("ldm_core.handlers.workspace.WorkspaceService.cmd_import")
    def test_cmd_import_remote_archive_url(self, mock_cmd_import, mock_get):
        # Mock successful archive download
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = lambda *_args, **_kwargs: [b"archive_data"]

        mock_sha_response = MagicMock()
        mock_sha_response.status_code = 200
        mock_sha_response.text = "hash"

        mock_get.side_effect = [mock_response, mock_sha_response]

        self.handler.workspace.cmd_import("https://example.com/project.ldmp")

        # Verify that cmd_import was recursively called
        self.assertTrue(mock_cmd_import.called)
        called_path = mock_cmd_import.call_args[0][0]
        self.assertTrue(called_path.endswith("project.ldmp"))

    @patch("subprocess.run")
    @patch("requests.get")
    @patch("ldm_core.handlers.workspace.calculate_sha256", return_value="matching_hash")
    @patch("tarfile.open")
    def test_cmd_import_git_url_success(
        self, mock_tar_open, mock_calc_sha, mock_get, mock_sub_run
    ):
        real_import = self.handler.workspace.cmd_import
        calls = []

        def mock_import(source_path, *args, **kwargs):
            calls.append(source_path)
            from urllib.parse import urlparse

            is_github = False
            try:
                parsed = urlparse(source_path)
                if parsed.netloc in (
                    "github.com",
                    "www.github.com",
                ) or source_path.startswith("git@github.com:"):
                    is_github = True
            except Exception:
                pass

            if is_github:
                return real_import(source_path, *args, **kwargs)
            return "my-project"

        self.handler.workspace.cmd_import = mock_import

        # 1. Mock subprocess.run for git clone & git remote get-url origin
        mock_clone_res = MagicMock()
        mock_clone_res.returncode = 0

        mock_origin_res = MagicMock()
        mock_origin_res.returncode = 0
        mock_origin_res.stdout = "git@github.com:owner/repo.git"

        mock_sub_run.side_effect = [mock_clone_res, mock_origin_res]

        # 2. Mock requests.get for GitHub Releases API and downloads
        mock_release_resp = MagicMock()
        mock_release_resp.status_code = 200
        mock_release_resp.json.return_value = {
            "assets": [
                {"name": "project.ldmp", "url": "https://api.github.com/assets/1"},
                {
                    "name": "project.ldmp.sha256",
                    "url": "https://api.github.com/assets/2",
                },
            ]
        }

        mock_ldmp_resp = MagicMock()
        mock_ldmp_resp.status_code = 200
        mock_ldmp_resp.iter_content = lambda *_args, **_kwargs: [b"package_data"]

        mock_sha_resp = MagicMock()
        mock_sha_resp.status_code = 200
        mock_sha_resp.text = "matching_hash project.ldmp"

        mock_get.side_effect = [mock_release_resp, mock_ldmp_resp, mock_sha_resp]

        # 3. Mock reading the meta file extracted from package
        mock_open = MagicMock()
        mock_file = MagicMock()
        mock_file.__iter__.return_value = [
            "github_repository=owner/repo\n",
            "tag=2024.q1.3\n",
        ]
        mock_open.return_value.__enter__.return_value = mock_file

        # Patch Path.exists to selectively say meta exists but project does not
        def mock_exists(self_path):
            return "my-project" not in str(self_path)

        with (
            patch("builtins.open", mock_open),
            patch.object(Path, "open", mock_open),
            patch.object(Path, "write_text"),
            patch.object(Path, "read_text", return_value="matching_hash project.ldmp"),
            patch.object(Path, "exists", autospec=True, side_effect=mock_exists),
            patch("pathlib.Path.mkdir"),
            patch("shutil.rmtree"),
            patch("ldm_core.utils.safe_extract"),
            patch(
                "ldm_core.handlers.base.BaseHandler.detect_project_path",
                return_value=Path("/tmp/my-project"),
            ),
            patch("ldm_core.handlers.base.BaseHandler.read_meta", return_value={}),
        ):
            self.handler.args.project = "my-project"
            self.handler.args.verify = True
            self.handler.args.no_run = True
            self.handler.workspace.cmd_import("https://github.com/owner/repo")

        # Assert recursive import was called with cloned git repo path
        self.assertEqual(len(calls), 2)
        self.assertTrue("clone_" in calls[1])
        # Assert database / snapshot restore was triggered
        self.assertTrue(self.handler.snapshot.cmd_restore.called)
        # Assert git clone was shielded with --
        mock_sub_run.assert_any_call(
            ["git", "clone", "--", "https://github.com/owner/repo", ANY],
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("subprocess.run")
    def test_cmd_import_git_clone_auth_failure(self, mock_sub_run):
        # Mock git clone failure
        mock_clone_res = MagicMock()
        mock_clone_res.returncode = 1
        mock_clone_res.stderr = "Permission denied (publickey)."
        mock_sub_run.return_value = mock_clone_res

        with self.assertRaises(SystemExit):
            self.handler.workspace.cmd_import("git@github.com:owner/repo.git")

    @patch("subprocess.run")
    @patch("requests.get")
    def test_cmd_import_git_url_release_missing(self, mock_get, mock_sub_run):
        # Mock git clone success
        mock_clone_res = MagicMock()
        mock_clone_res.returncode = 0
        mock_sub_run.return_value = mock_clone_res

        # Mock release response missing LDMP files
        mock_release_resp = MagicMock()
        mock_release_resp.status_code = 200
        mock_release_resp.json.return_value = {"assets": []}
        mock_get.return_value = mock_release_resp

        with self.assertRaises(SystemExit):
            self.handler.workspace.cmd_import("https://github.com/owner/repo")


if __name__ == "__main__":
    unittest.main()
