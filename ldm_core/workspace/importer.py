import os
import shutil
import tarfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from ldm_core.constants import VERSION
from ldm_core.utils import (
    calculate_sha256,
    is_within_root,
)


def _handle_dry_run(self, source_path, should_project_name):
    from ldm_core.utils import UI

    project_name = should_project_name
    if not project_name:
        parsed = self._parse_github_repo(source_path)
        if parsed:
            project_name = parsed[1]
        else:
            project_name = source_path.split("/")[-1]
            if project_name.endswith(".git"):
                project_name = project_name[:-4]
            if project_name.endswith(".zip") or project_name.endswith(".ldmp"):
                project_name = project_name.split(".")[0]

    if not project_name:
        project_name = "demo-project"

    UI.info(
        f"{UI.BYELLOW}[DRY RUN] Would import workspace:{UI.COLOR_OFF} {source_path} -> project: {project_name}"
    )

    project_path = self.manager.detect_project_path(project_name)
    project_meta = {
        "project_name": project_name,
        "container_name": project_name,
        "port": "8080",
        "ssl": "false",
        "host_name": "localhost",
        "tag": "2026.q1.4-lts",
        "db_type": "postgresql",
        "ldm_version": VERSION,
    }
    self.manager.write_meta(project_path, project_meta)
    return project_name


def _download_remote_archive(
    self,
    source_path,
    is_init_from,
    should_project_name,
    should_clone_only,
    should_no_run,
):
    import requests

    from ldm_core.utils import UI

    temp_dir = (
        Path.cwd() / ".ldm_temp" / f"download_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    )
    temp_dir.mkdir(parents=True, exist_ok=True)
    archive_name = (
        source_path.split("?")[0].split("#")[0].split("/")[-1] or "download.ldmp"
    )
    archive_name = Path(archive_name).name
    local_path = (temp_dir / archive_name).resolve()

    if not is_within_root(local_path, temp_dir):
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        UI.die("Security Violation: Invalid remote archive path.")

    UI.info(f"Downloading remote archive: {source_path}...")
    try:
        response = requests.get(source_path, stream=True, timeout=30)
        response.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        UI.die(f"Failed to download remote archive: {e}")

    # Download signature if enabled
    verify_enabled = getattr(self.manager.args, "verify", True)
    if verify_enabled:
        sha_url = source_path + ".sha256"
        try:
            sha_resp = requests.get(sha_url, timeout=10)
            if sha_resp.status_code == 200:
                sha_name = f"{local_path.name}.sha256"
                sha_path = (temp_dir / sha_name).resolve()
                if is_within_root(sha_path, temp_dir):
                    sha_path.write_text(sha_resp.text.strip())
                    UI.info("Downloaded checksum signature.")
                else:
                    UI.warning(
                        "Security Warning: Signature file containment check failed."
                    )
        except Exception:
            pass

    try:
        return self.cmd_import(
            str(local_path),
            is_init_from=is_init_from,
            is_internal=True,
            project_id=should_project_name,
            clone_only=should_clone_only,
            no_run=should_no_run,
        )
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def _check_github_release_for_package(self, owner, repo, source_path):
    import requests

    from ldm_core.utils import UI, get_github_token

    github_token = get_github_token()
    has_ldmp = False
    ldmp_asset = None
    sha_asset = None

    headers = {}
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        api_resp = requests.get(api_url, headers=headers, timeout=20)
        if api_resp.status_code == 200:
            release_data = api_resp.json()
            assets = release_data.get("assets", [])
            for asset in assets:
                name = asset.get("name", "")
                if name.endswith(".ldmp"):
                    ldmp_asset = asset
                elif name.endswith(".ldmp.sha256"):
                    sha_asset = asset
            if ldmp_asset and sha_asset:
                asset_size = ldmp_asset.get("size", 0)
                if asset_size > 10240:
                    has_ldmp = True
                else:
                    UI.die(
                        f"Remote LDM package '{ldmp_asset.get('name')}' is too small ({asset_size} bytes) "
                        f"and appears to be empty/vanilla. To clone the workspace code directly, please use: 'ldm clone {source_path}'"
                    )
        elif api_resp.status_code == 403:
            UI.warning(
                "GitHub API rate limit exceeded. Falling back to standard git clone. (Set GITHUB_TOKEN to avoid this)"
            )
        else:
            UI.debug(f"GitHub API returned {api_resp.status_code}")
    except Exception as e:
        UI.debug(f"GitHub Release API query failed: {e}")

    return has_ldmp, ldmp_asset, sha_asset, github_token


def _download_ldm_package_assets(
    self, ldmp_asset, sha_asset, github_token, temp_pkg_dir
):
    import requests

    from ldm_core.utils import UI

    ldmp_name = Path(ldmp_asset["name"]).name  # type: ignore[index]
    sha_name = Path(sha_asset["name"]).name  # type: ignore[index]

    ldmp_path = (temp_pkg_dir / ldmp_name).resolve()
    sha_path = (temp_pkg_dir / sha_name).resolve()

    if not is_within_root(ldmp_path, temp_pkg_dir) or not is_within_root(
        sha_path, temp_pkg_dir
    ):
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir)
        UI.die("Security Violation: Invalid package asset name.")

    UI.info(f"Downloading LDM package: {ldmp_name}...")
    try:
        headers_dl = {"Accept": "application/octet-stream"}
        if github_token:
            headers_dl["Authorization"] = f"token {github_token}"

        dl_url = ldmp_asset["url"]  # type: ignore[index]
        r = requests.get(dl_url, headers=headers_dl, stream=True, timeout=60)
        r.raise_for_status()
        with open(ldmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        dl_sha_url = sha_asset["url"]  # type: ignore[index]
        r_sha = requests.get(dl_sha_url, headers=headers_dl, timeout=20)
        r_sha.raise_for_status()
        sha_path.write_text(r_sha.text.strip())
    except Exception as e:
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir)
        UI.die(f"Failed to download LDM Package assets: {e}")

    actual_sha = calculate_sha256(ldmp_path)
    expected_sha = sha_path.read_text().strip().split()[0]

    if actual_sha != expected_sha:
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir)
        UI.die("Security Violation: SHA-256 verification failed.")

    UI.success("LDM package checksum verified successfully.")
    return ldmp_path


def _extract_ldm_package(self, ldmp_path, temp_extract_dir, temp_pkg_dir):
    from ldm_core.utils import UI

    UI.info("Extracting LDM package...")
    try:
        with tarfile.open(ldmp_path, "r:gz") as tar:
            from ldm_core.utils import safe_extract

            safe_extract(tar, temp_extract_dir)
    except Exception as e:
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir)
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        UI.die(f"Failed to extract LDM package: {e}")


def _verify_ldm_package_manifest(self, temp_extract_dir, temp_pkg_dir, owner, repo):
    from ldm_core.utils import UI

    manifest_file = temp_extract_dir / "meta"
    if not manifest_file.exists():
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir)
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        UI.die("Invalid LDM Package: Missing manifest 'meta' file.")

    manifest = self.manager.read_meta(temp_extract_dir) or {}
    db_type = manifest.get("db_type")
    if db_type and db_type not in [
        "postgresql",
        "mysql",
        "mariadb",
        "hypersonic",
    ]:
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir)
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        UI.die(f"Unsupported database type '{db_type}' in LDM package manifest.")

    github_repo_manifest = manifest.get("github_repository")
    if not github_repo_manifest:
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir)
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        UI.die("Security Violation: Manifest is missing 'github_repository' attribute.")

    if github_repo_manifest.lower() != f"{owner}/{repo}".lower():  # type: ignore[union-attr]
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir)
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        UI.die("Security Violation: Repository origin mismatch.")

    return manifest


def _hydrate_ldm_package_to_project(
    self, project_path, project_name, manifest, temp_extract_dir
):
    from ldm_core.utils import UI

    paths = self.manager.setup_paths(project_path)
    for p in [v for v in paths.values() if isinstance(v, Path) and not v.suffix]:
        p.mkdir(parents=True, exist_ok=True)
    self.manager.verify_runtime_environment(paths)

    project_meta = self.manager.read_meta(project_path) or {}
    if "tag" in manifest:
        project_meta["tag"] = manifest["tag"]
    if "db_type" in manifest:
        project_meta["db_type"] = manifest["db_type"]

    from ldm_core.utils import sanitize_id

    safe_container_name = sanitize_id(project_name)

    final_host_name = (
        getattr(self.manager.args, "host_name", None)
        or manifest.get("host_name")
        or project_meta.get("host_name")
        or "localhost"
    )
    ssl_arg = getattr(self.manager.args, "ssl", None)
    if ssl_arg is not None:
        final_ssl = str(ssl_arg).lower()
    elif getattr(self.manager.args, "host_name", None) is not None:
        final_ssl = str(final_host_name != "localhost").lower()
    else:
        manifest_ssl = manifest.get("ssl")
        if manifest_ssl is not None:
            final_ssl = str(manifest_ssl).lower()
        else:
            final_ssl = str(project_meta.get("ssl") or "false").lower()

    project_meta.update(
        {
            "project_name": project_name,
            "container_name": safe_container_name,
            "port": str(
                getattr(self.manager.args, "port", None)
                or project_meta.get("port")
                or 8080
            ),
            "ssl": final_ssl,
            "host_name": final_host_name,
            "last_run": datetime.now().isoformat(),
            "restored_from_package": "true",
            "package_includes_database": str(
                manifest.get("includes_database", "false")
            ).lower(),
        }
    )
    self.manager.write_meta(project_path, project_meta)

    UI.info("Restoring database and volume assets from LDM package...")
    self.manager._skip_git_check = True
    try:
        self.manager.snapshot.cmd_restore(
            project_name, backup_dir=temp_extract_dir, no_run=True
        )
    finally:
        if hasattr(self.manager, "_skip_git_check"):
            delattr(self.manager, "_skip_git_check")
    UI.success(f"Project created at: {project_path}")


def _import_ldm_package(
    self,
    ldmp_asset,
    sha_asset,
    owner,
    repo,
    github_token,
    should_project_name,
    should_no_run,
):
    from ldm_core.utils import UI

    project_name = should_project_name
    if not project_name:
        project_name = repo
        if self.manager.non_interactive:
            UI.info(f"Using default project name: {project_name}")
        else:
            project_name = UI.ask("Project Name", project_name)

    project_path = self.manager.detect_project_path(project_name, for_init=True)
    self.manager.check_uncommitted_changes(project_path)
    self._ensure_stopped(project_name, project_path)

    temp_pkg_dir = (
        Path.cwd() / ".ldm_temp" / f"package_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    )
    temp_pkg_dir.mkdir(parents=True, exist_ok=True)

    try:
        ldmp_path = _download_ldm_package_assets(
            self, ldmp_asset, sha_asset, github_token, temp_pkg_dir
        )

        temp_extract_dir = (
            Path.cwd()
            / ".ldm_temp"
            / f"extract_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        )
        temp_extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            _extract_ldm_package(self, ldmp_path, temp_extract_dir, temp_pkg_dir)
            manifest = _verify_ldm_package_manifest(
                self, temp_extract_dir, temp_pkg_dir, owner, repo
            )
            _hydrate_ldm_package_to_project(
                self, project_path, project_name, manifest, temp_extract_dir
            )

            if not should_no_run and manifest.get("includes_database") in [
                True,
                "true",
            ]:
                self.manager.runtime.cmd_run(project_id=project_name, is_restart=True)
        finally:
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)
    finally:
        if temp_pkg_dir.exists():
            shutil.rmtree(temp_pkg_dir)

    return project_name


def _resolve_clone_project_name(self, source_path, parsed, should_project_name):
    from ldm_core.utils import UI

    project_name = should_project_name
    if not project_name:
        if parsed:
            project_name = parsed[1]
        else:
            project_name = source_path.split("/")[-1]
            if project_name.endswith(".git"):
                project_name = project_name[:-4]

        if self.manager.non_interactive:
            UI.info(f"Using default project name: {project_name}")
        else:
            project_name = UI.ask("Project Name", project_name)
    return project_name


def _execute_git_clone(self, source_path, temp_git_dir):
    import os
    import subprocess

    from ldm_core.utils import UI

    UI.info(f"Cloning remote repository: {source_path}...")
    if source_path.startswith("git@"):
        UI.detail("Using SSH protocol for clone. Assumes SSH agent or key is loaded.")
    elif source_path.startswith("https://"):
        from urllib.parse import urlparse

        is_github = False
        try:
            parsed_url = urlparse(source_path)
            if parsed_url.netloc in ("github.com", "www.github.com"):
                is_github = True
        except Exception:
            pass

        if (
            is_github
            and "GITHUB_TOKEN" not in os.environ
            and "GITHUB_PAT" not in os.environ
        ):
            UI.info(
                "Note: GITHUB_TOKEN/GITHUB_PAT environment variable is not set. If this is a private repository, cloning may fail."
            )

    try:
        res = subprocess.run(
            ["git", "clone", "--", source_path, str(temp_git_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            stderr = res.stderr or ""
            if (
                "Permission denied (publickey)" in stderr
                or "Repository not found" in stderr
            ):
                if source_path.startswith("git@"):
                    UI.die(
                        f"Git clone failed: Authentication error.\nDetails: {stderr.strip()}\nGuide: Please configure your SSH keys."
                    )
                else:
                    UI.die(
                        f"Git clone failed: Authentication error.\nDetails: {stderr.strip()}\nGuide: Please export a valid GITHUB_TOKEN or GITHUB_PAT."
                    )
            else:
                UI.die(f"Git clone failed:\n{stderr.strip()}")
    except Exception as e:
        UI.die(f"Failed to execute git clone: {e}")


def _clone_remote_repository(
    self, source_path, should_project_name, should_clone_only, should_no_run, parsed
):
    from ldm_core.utils import UI

    if not should_clone_only:
        UI.die(
            "No compiled LDM Package (.ldmp) found in GitHub Releases. To clone the workspace code directly, please use: 'ldm clone <repository-url>'"
        )

    project_name = _resolve_clone_project_name(
        self, source_path, parsed, should_project_name
    )

    project_path = self.manager.detect_project_path(project_name, for_init=True)
    self.manager.check_uncommitted_changes(project_path)
    self._ensure_stopped(project_name, project_path)

    temp_git_dir = (
        Path.cwd() / ".ldm_temp" / f"clone_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    )
    temp_git_dir.mkdir(parents=True, exist_ok=True)

    try:
        _execute_git_clone(self, source_path, temp_git_dir)
        self.cmd_import(
            str(temp_git_dir),
            is_init_from=False,
            is_internal=True,
            project_id=project_name,
            clone_only=should_clone_only,
            no_run=True,
        )
    finally:
        if temp_git_dir.exists():
            shutil.rmtree(temp_git_dir)

    if not should_no_run:
        self.manager.runtime.cmd_run(project_id=project_name, is_restart=True)

    return project_name


def cmd_import(
    self,
    source_path,
    is_init_from=False,
    is_internal=False,
    project_id=None,
    clone_only=None,
    no_run=None,
):
    from ldm_core.utils import UI

    # Resolve parameters to avoid mutating manager.args directly
    should_project_name = (
        project_id
        or getattr(self.manager.args, "project", None)
        or getattr(self.manager.args, "project_flag", None)
    )
    should_clone_only = (
        clone_only
        if clone_only is not None
        else getattr(self.manager.args, "clone_only", False)
    )
    should_no_run = (
        no_run if no_run is not None else getattr(self.manager.args, "no_run", False)
    )

    is_remote = (
        source_path.startswith(("http://", "https://", "git@")) or "://" in source_path
    )
    if not is_remote:
        try:
            source_p = Path(source_path).resolve()
            if (
                source_p.is_dir()
                and not is_init_from
                and not is_internal
                and not self.manager.non_interactive
            ):
                UI.die(
                    "Error: To integrate a local Liferay Workspace, please use: 'ldm link <workspace-path>'"
                )
        except Exception:
            pass

    is_dry_run = os.environ.get("LDM_DRY_RUN", "").lower() == "true"
    if is_dry_run:
        return _handle_dry_run(self, source_path, should_project_name)

    # Remote URL check (http://, https://, git@)
    if source_path.startswith(("http://", "https://", "git@")):
        clean_url = source_path.split("?")[0].split("#")[0].lower()
        is_archive_url = any(
            clean_url.endswith(suffix)
            for suffix in [".zip", ".tgz", ".gz", ".tar", ".ldmp"]
        )

        if is_archive_url:
            return _download_remote_archive(
                self,
                source_path,
                is_init_from,
                should_project_name,
                should_clone_only,
                should_no_run,
            )
        # Git URL / GitHub Repo URL
        parsed = self._parse_github_repo(source_path)

        has_ldmp = False
        ldmp_asset = None
        sha_asset = None
        owner, repo = None, None
        github_token = None

        if parsed and not should_clone_only:
            owner, repo = parsed
            has_ldmp, ldmp_asset, sha_asset, github_token = (
                _check_github_release_for_package(self, owner, repo, source_path)
            )

        if has_ldmp:
            return _import_ldm_package(
                self,
                ldmp_asset,
                sha_asset,
                owner,
                repo,
                github_token,
                should_project_name,
                should_no_run,
            )

        return _clone_remote_repository(
            self,
            source_path,
            should_project_name,
            should_clone_only,
            should_no_run,
            parsed,
        )

    # --- Delegate to ImportPipeline for local file/dir execution ---
    from ldm_core.pipelines.import_pipeline import (
        ImportPipeline,
        ImportPipelineContext,
    )

    project_name = should_project_name

    context = ImportPipelineContext(
        manager=self.manager,
        source_path=source_path,
        project_name=project_name,
        is_init_from=is_init_from,
        no_run=should_no_run,
    )
    pipeline = ImportPipeline()
    pipeline.run(context)

    return context.get("project_name")


def cmd_link(self, source_path):
    """Initialize project with a persistent link to a source workspace and start monitoring."""
    from ldm_core.utils import UI

    try:
        source = Path(source_path).resolve()
        is_valid_dir = source.exists() and source.is_dir()
    except Exception:
        is_valid_dir = False

    if not is_valid_dir:
        UI.die(
            "Error: Source path to link must be a local Liferay Workspace directory."
        )

    project_name = self.cmd_import(str(source), is_init_from=True)
    self.cmd_monitor(str(source), project_id=project_name)


def cmd_clone(self, source_path):
    """Clone a remote Git repository workspace and initialize it."""
    from ldm_core.utils import UI

    is_remote = (
        source_path.startswith(("http://", "https://", "git@")) or "://" in source_path
    )
    if not is_remote:
        UI.die("Error: Source path to clone must be a valid Git repository URL.")

    return self.cmd_import(source_path, clone_only=True)


def cmd_init_from(self, source_path):
    """Deprecated: Initialize project from workspace."""
    from ldm_core.utils import UI

    UI.warning(
        "Deprecation Warning: 'ldm init-from' is deprecated. Please use: 'ldm link <workspace-path>' instead."
    )
    return self.cmd_link(source_path)


def _parse_github_repo(self, url: str) -> tuple[str, str] | None:
    if not url:
        return None
    url = url.strip().split("?")[0].split("#")[0]

    # Handle SSH format: git@github.com:owner/repo.git
    if url.startswith("git@github.com:"):
        path = url.split("git@github.com:", 1)[1]
        if path.endswith(".git"):
            path = path[:-4]
        subparts = [p for p in path.split("/") if p]
        if len(subparts) >= 2:
            return subparts[0], subparts[1]
        return None

    # Handle HTTP/HTTPS format: https://github.com/owner/repo or https://github.com/owner/repo/tree/master
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.hostname in (
            "github.com",
            "www.github.com",
        ):
            path = parsed.path
            if path.endswith(".git"):
                path = path[:-4]
            subparts = [p for p in path.split("/") if p]
            if len(subparts) >= 2:
                return subparts[0], subparts[1]
    except Exception:
        pass
    return None


def cmd_validate(self, project_id=None):
    """Runs the Pre-Flight Client Extension Analyzer against the workspace."""
    root = self.manager.detect_project_path(project_id)
    if not root:
        return
    from ldm_core.handlers.validation import ClientExtensionAnalyzer

    if ClientExtensionAnalyzer.analyze_workspace(root):
        from ldm_core.utils import UI

        UI.success("Validation passed. Client extensions appear structurally sound.")
    else:
        from ldm_core.utils import UI

        UI.error("Validation failed. See warnings above.")
