import os
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    pass
from ldm_core.ui import UI


def cmd_init(self, project_id=None):
    """Scaffolds a project without starting it."""
    UI.info(f"Initializing project shell: {project_id or 'interactively'}")
    self.manager.runtime.cmd_run(project_id, no_up=True)
    UI.success("Initialization complete. You can now run 'ldm doctor' or 'ldm run'.")


def _ensure_stopped(self, project_name, project_path):
    """Ensures that the project is not running, stopping it if requested or possible."""
    if not project_path.exists():
        return

    from ldm_core.docker_service import DockerService

    meta = self.manager.read_meta(project_path)
    c_name = meta.get("container_name") or project_name
    if DockerService.is_running(c_name):
        if getattr(self.manager.args, "leave_running", False):
            UI.die(
                f"Project '{project_name}' is currently running. `--leave-running` was specified, so the project remains running. Aborting import."
            )
        elif (
            getattr(self.manager.args, "stop_running", False)
            or self.manager.non_interactive
        ):
            UI.info(f"Stopping running project '{project_name}' automatically...")
            self.manager.runtime.cmd_stop(project_id=project_name)
        elif UI.confirm(
            f"Project '{project_name}' is currently running. Stop it before continuing?",
            "Y",
        ):
            self.manager.runtime.cmd_stop(project_id=project_name)
        else:
            UI.die("Import aborted. Cannot modify a running project's foundation.")


def _rewrite_oauth_urls_in_zip(
    self,
    zip_path: Path,
    host_name: str,
    ext_name: str,
    root_dir: Path | None = None,
):
    if not host_name:
        return

    import tempfile

    from ldm_core.utils import safe_extract

    meta: dict = {}
    if root_dir is not None:
        meta = self.manager.read_meta(root_dir) or {}
    ssl_enabled = str(meta.get("ssl", "false")).lower() == "true"
    protocol = "https" if ssl_enabled else "http"

    if host_name == "localhost":
        if ssl_enabled:
            external_url = "https://localhost"
        else:
            port = meta.get(f"port_{ext_name}") or "8080"
            external_url = f"http://localhost:{port}"
    elif ssl_enabled:
        external_url = f"https://{ext_name}.{host_name}"
    else:
        port = meta.get(f"port_{ext_name}")
        if port and port not in ["80", "443", 80, 443]:
            external_url = f"http://{ext_name}.{host_name}:{port}"
        else:
            external_url = f"http://{ext_name}.{host_name}"

    modified = False
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                safe_extract(zip_ref, tmp_path)
        except Exception as e:
            UI.warning(f"Failed to extract zip for OAuth rewriting: {e}")
            return

        config_file = None
        is_json = False
        if (tmp_path / "client-extension.yaml").exists():
            config_file = tmp_path / "client-extension.yaml"
        else:
            json_files = list(tmp_path.glob("*.client-extension-config.json"))
            if json_files:
                config_file = json_files[0]
                is_json = True

        if not config_file:
            return

        try:
            content = config_file.read_text(encoding="utf-8")
            if is_json:
                import json

                data = json.loads(content)
            else:
                data = yaml.safe_load(content)

            if not isinstance(data, dict):
                return

            for _key, block in data.items():
                if isinstance(block, dict) and block.get("type") in [
                    "oAuthApplicationUserAgent",
                    "oAuthApplicationHeadlessServer",
                ]:
                    service_address = block.get(".serviceAddress", "")
                    if "localhost" in service_address:
                        block[".serviceAddress"] = f"{ext_name}.{host_name}"
                        modified = True

                    if block.get(".serviceScheme") == "http" and protocol == "https":
                        block[".serviceScheme"] = "https"
                        modified = True

                    hp_url = block.get("homePageURL", "")
                    if "localhost" in hp_url:
                        block["homePageURL"] = external_url
                        modified = True

                    redirects = block.get("redirectURIs", [])
                    if redirects and isinstance(redirects, list):
                        new_redirects = []
                        for uri in redirects:
                            if "localhost" in uri:
                                new_uri = re.sub(
                                    r"https?://localhost:\d+", external_url, uri
                                )
                                new_redirects.append(new_uri)
                                if new_uri != uri:
                                    modified = True
                            else:
                                new_redirects.append(uri)
                        block["redirectURIs"] = new_redirects

                    ts = block.get("typeSettings", [])
                    if isinstance(ts, list):
                        for i, setting in enumerate(ts):
                            if (
                                setting.startswith(".serviceAddress=")
                                and "localhost" in setting
                            ):
                                ts[i] = f".serviceAddress={ext_name}.{host_name}"
                                modified = True
                            elif (
                                setting.startswith(".serviceScheme=")
                                and protocol == "https"
                            ):
                                ts[i] = ".serviceScheme=https"
                                modified = True
                            elif (
                                setting.startswith("homePageURL=")
                                and "localhost" in setting
                            ):
                                ts[i] = f"homePageURL={external_url}"
                                modified = True
                            elif "redirectURIs=" in setting and "localhost" in setting:
                                ts[i] = re.sub(
                                    r"https?://localhost:\d+", external_url, setting
                                )
                                modified = True

            if modified:
                import time

                current_ts = int(time.time() * 1000)
                for _key, block in data.items():
                    if isinstance(block, dict) and "buildTimestamp" in block:
                        # Bump the timestamp to force Liferay to re-evaluate it over the snapshot DB
                        block["buildTimestamp"] = current_ts

                if is_json:
                    import json

                    config_file.write_text(
                        json.dumps(data, indent=2),
                        encoding="utf-8",
                    )
                else:

                    class NoAliasDumper(yaml.SafeDumper):
                        def ignore_aliases(self, data):
                            return True

                    config_file.write_text(
                        yaml.dump(data, Dumper=NoAliasDumper, sort_keys=False),
                        encoding="utf-8",
                    )

                temp_zip_path = tmp_path / "repacked.zip"
                with zipfile.ZipFile(
                    temp_zip_path, "w", zipfile.ZIP_DEFLATED
                ) as new_zip:
                    for root, _dirs, files in os.walk(tmp_path):
                        for file in files:
                            if file == "repacked.zip":
                                continue
                            file_path = Path(root) / file
                            arcname = file_path.relative_to(tmp_path)
                            new_zip.write(file_path, arcname)

                # Atomically overwrite the original zip.  Writing to a
                # temp file inside the same TemporaryDirectory and then
                # calling Path.replace() ensures that an interruption
                # (SIGINT, OOM, disk-full) never leaves the workspace in a
                # state where the original archive is gone but the
                # replacement has not yet landed.
                temp_zip_path.replace(zip_path)
                UI.detail(
                    f"  + Dynamically rewrote OAuth profile URLs in {zip_path.name}"
                )

        except Exception as e:
            UI.warning(
                f"Failed to modify client-extension.yaml for OAuth rewriting in {zip_path.name}: {e}"
            )
