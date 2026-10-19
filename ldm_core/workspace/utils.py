import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    pass
from ldm_core.ui import UI
from ldm_core.utils import sanitize_id

# LDM-#2175: the port is OPTIONAL. A client extension fronted by a proxy on :80
# declares `http://localhost/o/oauth2/redirect` -- which the substring guard
# `"localhost" in uri` admits and a mandatory `:\d+` could never touch. The URI
# was appended unchanged and `modified` left unset, so the `buildTimestamp`
# bump was skipped too and the zip never repacked. In silence.
_LOCALHOST_URL = re.compile(r"https?://localhost(?::\d+)?")

# The only block types this rewrite claims, and so is answerable for.
_OAUTH_BLOCK_TYPES = ("oAuthApplicationUserAgent", "oAuthApplicationHeadlessServer")
_OAUTH_URL_FIELDS = (".serviceAddress", "homePageURL")
_OAUTH_URL_SETTINGS = (".serviceAddress=", "homePageURL=", "redirectURIs=")


def _rewrite_localhost(value: str, external_url: str) -> str:
    """Swap a `localhost` origin for the externally reachable one.

    The replacement is a callable rather than a template so that a `\\` or a
    `\\g` arriving in `external_url` is inserted literally instead of being
    read as a backreference.
    """
    return _LOCALHOST_URL.sub(lambda _match: external_url, value)


def _urls_left_on_localhost(data: dict) -> list[str]:
    """Values this rewrite is answerable for that still point at localhost.

    Each guard and the action behind it are written separately, so a guard can
    admit a value its action cannot change. That is a silent no-op, and it is
    how LDM-#2175 went unnoticed -- the regex was only how it happened that
    time. This asks the one question no guard can ask about itself: did the
    rewrite actually leave anything behind?
    """
    stranded: list[str] = []
    for block in data.values():
        if not isinstance(block, dict) or block.get("type") not in _OAUTH_BLOCK_TYPES:
            continue
        candidates = [block.get(field, "") for field in _OAUTH_URL_FIELDS]
        candidates += block.get("redirectURIs") or []
        candidates += [
            setting
            for setting in block.get("typeSettings") or []
            if isinstance(setting, str)
            and any(marker in setting for marker in _OAUTH_URL_SETTINGS)
        ]
        stranded += [
            value
            for value in candidates
            if isinstance(value, str) and "localhost" in value
        ]
    return stranded


def cmd_init(self, project_id=None):
    """Scaffolds a project without starting it."""
    UI.detail(f"Initializing project shell: {project_id or 'interactively'}")
    self.manager.runtime.cmd_run(project_id, no_up=True)
    UI.success("Initialization complete. You can now run 'ldm doctor' or 'ldm run'.")


def _ensure_stopped(self, project_name, project_path=None):
    """Ensures that the project is not running, stopping it if requested or possible."""
    from ldm_core.docker_service import DockerService

    c_name = project_name
    if project_path and hasattr(project_path, "exists") and project_path.exists():
        meta = self.manager.read_meta(project_path)
        # LDM-#1512: this is passed to DockerService.stop()/is_running(),
        # so it must be the transcoded name the daemon actually holds.
        c_name = sanitize_id(meta.get("container_name") or project_name)

    def stop_container():
        if project_path and hasattr(project_path, "exists") and project_path.exists():
            try:
                self.manager.runtime.cmd_stop(project_id=project_name)
                return
            except Exception:
                pass
        DockerService.stop(c_name)

    if DockerService.is_running(c_name):
        if getattr(self.manager.args, "leave_running", False):
            UI.die(
                f"Project '{project_name}' is currently running. `--leave-running` was specified, so the project remains running. Aborting import."
            )
        elif (
            getattr(self.manager.args, "stop_running", False)
            or self.manager.non_interactive
        ):
            UI.detail(f"Stopping running project '{project_name}' automatically...")
            stop_container()
        elif UI.confirm(
            f"Project '{project_name}' is currently running. Stop it before continuing?",
            "Y",
        ):
            stop_container()
        else:
            UI.die("Import aborted. Cannot modify a running project's foundation.")


def _rewrite_oauth_urls_in_zip(  # noqa: C901, PLR0912, PLR0915
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

    # LDM-#1985: the port is back, and is now CORRECT.
    #
    # This URL is written INTO the extension's own OAuth configuration, inside
    # the deployed artifact, so a wrong one is baked in.
    #
    # It used to be wrong. `meta[port_<ext_name>]` is the port the resolver
    # assigned, and the composer then rewrote 8080 to 28080 before publishing
    # it -- so this wrote `:8080` for a container listening on 28080. LDM-#1973
    # removed the port entirely to escape that, which was right only where a
    # Traefik proxy exists to serve the bare subdomain; a non-SSL project has
    # none (LDM-#1985).
    #
    # With the rewrite deleted, the resolved port IS the published port, so
    # reading it here is accurate again. SSL keeps the bare subdomain, because
    # there the proxy fronts it and nothing is published.
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
                if isinstance(block, dict) and block.get("type") in _OAUTH_BLOCK_TYPES:
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
                            new_uri = _rewrite_localhost(uri, external_url)
                            new_redirects.append(new_uri)
                            if new_uri != uri:
                                modified = True
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
                                new_setting = _rewrite_localhost(setting, external_url)
                                if new_setting != setting:
                                    ts[i] = new_setting
                                    modified = True

            if "localhost" not in external_url:
                stranded = _urls_left_on_localhost(data)
                if stranded:
                    UI.warning(
                        f"These OAuth URLs in '{ext_name}' still point at "
                        f"localhost after the rewrite to {external_url}: "
                        + ", ".join(stranded)
                    )

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

                # Overwrite original zip. Use shutil.move to handle cross-device
                # moves across different volume mount points (e.g. /tmp -> /Volumes/...)
                shutil.move(temp_zip_path, zip_path)
                UI.detail(
                    f"  + Dynamically rewrote OAuth profile URLs in {zip_path.name}"
                )

        except Exception as e:
            UI.warning(
                f"Failed to modify client-extension.yaml for OAuth rewriting in {zip_path.name}: {e}"
            )
