import os
import shutil
import sys
import time
import zipfile

import requests

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import get_actual_home, safe_move

# Reference for reliable mocking in tests
exists_fn = os.path.exists


class AssetHandler(BaseHandler):
    """Specialized handler for 'Offline First' asset management (Seeds, Samples)."""

    def __init__(self, args=None):
        super().__init__(args)

    def _fetch_seed(self, tag, db_type, search_mode, paths):
        """Discovers and downloads a pre-warmed seed from GitHub Releases with Offline-First logic."""
        from ldm_core.constants import SEED_VERSION

        tag_name = "seeded-states"
        seed_filename = f"seeded-{tag}-{db_type}-{search_mode}-v{SEED_VERSION}.tar.gz"
        repo_url = "https://github.com/peterrichards-lr/liferay-docker-manager"
        download_url = f"{repo_url}/releases/download/{tag_name}/{seed_filename}"

        UI.info(f"Checking for pre-warmed seed: {UI.CYAN}{seed_filename}{UI.COLOR_OFF}")

        headers = {}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        actual_home = get_actual_home()
        cache_dir = actual_home / ".ldm" / "seeds"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_seed = cache_dir / seed_filename

        tmp_path = None
        if os.path.exists(cached_seed):
            UI.info(f"Using cached seed: {seed_filename}")
            tmp_path = cached_seed
        else:
            if not UI.confirm(
                f"Project seed not found in cache. Download pre-warmed {tag} seed?",
                "Y",
            ):
                return False

            try:
                head_res = requests.head(download_url, allow_redirects=True, timeout=10)

                if head_res.status_code != 200:
                    api_url = f"https://api.github.com/repos/peterrichards-lr/liferay-docker-manager/releases/tags/{tag_name}"
                    api_res = requests.get(api_url, headers=headers, timeout=10)

                    if api_res.status_code != 200:
                        api_url = "https://api.github.com/repos/peterrichards-lr/liferay-docker-manager/releases"
                        api_res = requests.get(api_url, headers=headers, timeout=10)

                    if api_res.status_code == 200:
                        data = api_res.json()
                        releases = data if isinstance(data, list) else [data]
                        target_release = next(
                            (
                                r
                                for r in releases
                                if r.get("tag_name") == tag_name
                                or r.get("name") == tag_name
                            ),
                            None,
                        )
                        if target_release:
                            asset = next(
                                (
                                    a
                                    for a in target_release.get("assets", [])
                                    if a.get("name") == seed_filename
                                ),
                                None,
                            )
                            if asset:
                                download_url = asset.get("browser_download_url")
                            else:
                                UI.warning(f"No seed found for tag: {tag_name}")
                                return False
                        else:
                            UI.warning(f"Release not found for tag: {tag_name}")
                            return False
                    else:
                        UI.warning(
                            f"Failed to fetch release info: HTTP {api_res.status_code}"
                        )
                        return False

                temp_download = cache_dir / f"{seed_filename}.download"
                try:
                    response = requests.get(
                        download_url, headers=headers, stream=True, timeout=30
                    )
                    response.raise_for_status()
                    total_size = int(response.headers.get("content-length", 0))

                    downloaded = 0
                    with open(temp_download, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192 * 1024):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total_size:
                                    percent = int((downloaded / total_size) * 100)
                                    sys.stdout.write(
                                        f"\rDownloading: [{percent}%] {UI.format_size(downloaded)} / {UI.format_size(total_size)}"
                                    )
                                    sys.stdout.flush()
                    print()
                    os.replace(temp_download, cached_seed)
                    tmp_path = cached_seed
                    UI.success(f"Seed cached: {seed_filename}")
                except Exception as e:
                    if temp_download.exists():
                        temp_download.unlink()
                    UI.warning(f"Seed download failed: {e}")
                    return False
            except Exception as e:
                UI.warning(f"LDM is working offline or seed is unreachable ({e})")
                return False

        if not tmp_path or not tmp_path.exists():
            return False

        try:
            from ldm_core.handlers.snapshot import SnapshotHandler

            handler = SnapshotHandler(self.args)
            self.verify_runtime_environment(paths)

            if getattr(self.args, "no_osgi_seed", False):
                UI.debug("User opted out of OSGi state seeding.")

            UI.info("Bootstrapping project from seed...")
            handler._extract_snapshot_archive(tmp_path, paths)

            success_msg = "Project bootstrapped from seed."
            if not getattr(self.args, "no_osgi_seed", False):
                success_msg = "Project bootstrapped from seed (including OSGi state)."
            UI.success(
                f"{success_msg} {UI.WHITE}(Saved ~15m of initialization time){UI.COLOR_OFF}"
            )
            return True
        except Exception as e:
            UI.warning(f"Failed to extract bootstrap seed: {e}")
            UI.info("Continuing with fresh/vanilla initialization...")
            return True

    def _ensure_seeded(self, tag, db_type, paths):
        """Helper to ensure a project is bootstrapped from a seed if available and appropriate."""
        if getattr(self.args, "no_seed", False):
            return False

        sidecar_flag = getattr(self.args, "sidecar", False)
        search_mode = (
            "sidecar"
            if sidecar_flag or self.parse_version(tag) < (2025, 1, 0)
            else "shared"
        )

        seed_start = time.time()
        if self._fetch_seed(tag, db_type or "hypersonic", search_mode, paths):
            if self.verbose:
                duration_str = UI.format_duration(time.time() - seed_start)
                UI.debug(f"Seed fetch & extraction took: {duration_str}")
            return True
        return False

    def download_samples(self, version, destination):
        """Downloads and extracts the samples pack from GitHub with Offline-First logic."""
        repo_url = "https://github.com/peterrichards-lr/liferay-docker-manager"

        actual_home = get_actual_home()
        cache_dir = actual_home / ".ldm" / "references" / "samples"
        cache_dir.mkdir(parents=True, exist_ok=True)

        cached_zip = cache_dir / f"samples_{version}.zip"

        if os.path.exists(cached_zip):
            UI.info(f"Using cached samples: {cached_zip.name}")
            temp_zip = cached_zip
        else:
            urls = [
                f"{repo_url}/releases/download/v{version}/samples.zip",
                f"{repo_url}/releases/latest/download/samples.zip",
            ]

            temp_zip = cache_dir / f"samples_{version}.zip.download"
            success = False
            last_error = None

            for url in urls:
                try:
                    if success:
                        break

                    UI.debug(f"Attempting to download samples from: {url}")
                    response = requests.get(
                        url, headers={"User-Agent": "ldm-cli"}, timeout=15, stream=True
                    )
                    if response.status_code != 200:
                        continue

                    with open(temp_zip, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)

                    success = True
                except Exception as e:
                    last_error = e
                    continue

            if not success:
                UI.warning(
                    f"LDM is working offline or samples are unreachable ({last_error or '404'})"
                )
                UI.error(
                    "The '--samples' workflow requires a download if assets are not cached."
                )
                if temp_zip.exists():
                    temp_zip.unlink()
                return False

            os.replace(temp_zip, cached_zip)
            temp_zip = cached_zip
            UI.success(f"Samples cached: {cached_zip.name}")

        try:
            UI.info("Extracting samples...")
            destination.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(temp_zip, "r") as zip_ref:
                extract_temp = cache_dir / f"temp_extract_{version}"
                if extract_temp.exists():
                    shutil.rmtree(extract_temp)
                extract_temp.mkdir(parents=True)
                zip_ref.extractall(extract_temp)

                source_root = extract_temp
                inner_samples = extract_temp / "samples"
                if inner_samples.exists() and inner_samples.is_dir():
                    source_root = inner_samples

                for item in source_root.iterdir():
                    target = destination / item.name
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            os.remove(target)
                    safe_move(str(item), str(target))

                shutil.rmtree(extract_temp)

            UI.success("Sample pack ready.")
            return True
        except Exception as e:
            UI.error(f"Failed to extract samples: {e}")
            return False
