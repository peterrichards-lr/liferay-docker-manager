import contextlib
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from ldm_core.docker_service import DockerService
from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI
from ldm_core.utils import (
    ProjectLock,
    get_actual_home,
    get_compose_cmd,
    open_browser,
    strip_ansi,
)


class RuntimeService(BaseHandler):
    """Service for container lifecycle and orchestration."""

    def __init__(self, manager=None):
        super().__init__(manager.args if manager else None)
        self.manager = manager

    def _generate_keycloak_realm(self, project_root):
        """Dynamically generates the keycloak-realm.json to avoid tracking secrets in git."""
        import json

        from ldm_core.utils import safe_write_text

        realm_data = {
            "realm": "liferay",
            "enabled": True,
            "users": [
                {
                    "username": "test",
                    "enabled": True,
                    "email": "test@liferay.com",
                    "firstName": "Test",
                    "lastName": "Test",
                    "credentials": [
                        {"type": "password", "value": "test", "temporary": False}
                    ],
                }
            ],
            "clients": [
                {
                    "clientId": "liferay-client",
                    "enabled": True,
                    "clientAuthenticatorType": "client-secret",
                    "secret": "secret",  # pragma: allowlist secret
                    "redirectUris": ["*"],
                    "webOrigins": ["*"],
                    "publicClient": False,
                    "protocol": "openid-connect",
                }
            ],
        }

        safe_write_text(
            project_root / "keycloak-realm.json", json.dumps(realm_data, indent=2)
        )

    def cmd_run(
        self,
        project_id=None,
        is_restart=False,
        no_up=None,
        browser=None,
        **kwargs,
    ):
        """Main entry point for starting or updating a project stack."""
        from ldm_core.pipelines.run import RunPipelineContext, create_run_pipeline

        pipeline = create_run_pipeline()
        context = RunPipelineContext(
            self.manager,
            project_id=project_id,
            is_restart=is_restart,
            no_up=no_up,
            browser=browser,
            **kwargs,
        )
        return pipeline.run(context)

    def cmd_reseed(self, project_id=None):
        """Triggers a re-bootstrap of the project from a fresh seed."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return None
        project_meta = self.manager.read_meta(root)
        tag = project_meta.get("tag")
        db_type = project_meta.get("db_type")

        default_shared = (
            "true" if self.manager.parse_version(tag) >= (2025, 1, 0) else "false"
        )
        use_shared = (
            str(project_meta.get("use_shared_search", default_shared)).lower() == "true"
        )
        if not use_shared and self.manager.parse_version(tag) >= (2025, 2, 0):
            use_shared = True
        search_mode = "shared" if use_shared else "sidecar"

        if not tag:
            UI.die("Project missing tag metadata. Cannot reseed.")

        is_dry_run = getattr(self.manager, "dry_run", False)
        if is_dry_run:
            UI.info(f"Reseed {root.name} from {tag} ({db_type}/{search_mode})...")
            UI.info(
                f"  {UI.BYELLOW}- [Dry Run] Would reset project stack (cmd_reset all).{UI.COLOR_OFF}"
            )
            UI.info(
                f"  {UI.BYELLOW}- [Dry Run] Would fetch and extract new seed for tag: {tag}.{UI.COLOR_OFF}"
            )
            up_flag = getattr(self.manager.args, "up", False)
            if up_flag:
                UI.info(
                    f"  {UI.BYELLOW}- [Dry Run] Would start the project containers (cmd_run).{UI.COLOR_OFF}"
                )
            UI.success(
                f"[Dry Run] Project {root.name} reseed completed (no changes made)."
            )
            return True

        if UI.confirm(
            f"Reseed {root.name} from {tag} ({db_type}/{search_mode})? ALL LOCAL DATA WILL BE LOST.",
            "N",
        ):
            self.cmd_reset(root.name, target="all")
            paths = self.manager.setup_paths(root)
            if self.manager.assets._fetch_seed(tag, db_type, search_mode, paths):
                UI.success("Reseed complete.")
                up_flag = getattr(self.manager.args, "up", False)
                if up_flag or (
                    not self.manager.non_interactive
                    and UI.confirm("Do you want to start the project now?", "Y")
                ):
                    self.cmd_run(project_id)
                else:
                    UI.info(
                        f"Run {UI.CYAN}ldm run {root.name}{UI.COLOR_OFF} to start the project."
                    )
            else:
                UI.error("Reseed failed.")
        return None

    def _scan_for_expected_deployables(self, root_path):  # noqa: C901, PLR0912
        """Scans workspace deploy and client-extensions paths for deployable targets.

        Returns a dict of {bundle_symbolic_name_or_cx_id: expected_state}
        """
        import zipfile

        import yaml

        targets = {}

        # 1. Scan configs/common/deploy and deploy directories
        deploy_dirs = [
            root_path / "configs" / "common" / "deploy",
            root_path / "deploy",
        ]

        for d in deploy_dirs:
            if not d.exists() or not d.is_dir():
                continue
            for item in d.glob("*"):
                if item.suffix.lower() in [".jar", ".war"]:
                    try:
                        with zipfile.ZipFile(item) as z:
                            try:
                                manifest_content = z.read(
                                    "META-INF/MANIFEST.MF"
                                ).decode("utf-8", errors="ignore")
                                # Unfold manifest lines
                                unfolded_lines = []
                                for line in manifest_content.splitlines():
                                    if line.startswith(" ") and unfolded_lines:
                                        unfolded_lines[-1] += line[1:]
                                    else:
                                        unfolded_lines.append(line)

                                symbolic_name = None
                                is_fragment = False
                                for line in unfolded_lines:
                                    if line.startswith("Bundle-SymbolicName:"):
                                        val = line.split(":", 1)[1].strip()
                                        symbolic_name = val.split(";")[0].strip()
                                    elif line.startswith("Fragment-Host:"):
                                        is_fragment = True

                                if symbolic_name:
                                    expected_state = (
                                        "Resolved" if is_fragment else "Active"
                                    )
                                    targets[symbolic_name] = expected_state
                            except KeyError:
                                pass
                    except Exception as e:
                        UI.debug(f"Failed to scan manifest for {item.name}: {e}")

        # 2. Scan client-extensions directory
        cx_dir = root_path / "client-extensions"
        if cx_dir.exists() and cx_dir.is_dir():
            for item in cx_dir.glob("*"):
                if item.is_dir():
                    yaml_file = item / "client-extension.yaml"
                    if yaml_file.exists():
                        try:
                            with open(yaml_file) as f:
                                cx_yaml = yaml.safe_load(f)
                                if cx_yaml and isinstance(cx_yaml, dict):
                                    for key, val in cx_yaml.items():
                                        if isinstance(val, dict):
                                            targets[key] = "Active"
                        except Exception as e:
                            UI.debug(
                                f"Failed to parse client-extension.yaml in {item.name}: {e}"
                            )

        return targets

    @staticmethod
    def _validate_fragment_overrides(data, file_path):
        """Statically validate the structure of a fragment-overrides.json payload.

        Expected format is a top-level dictionary where:
        - Every key is a non-empty string (the fragment key).
        - Every value is a dictionary (the configuration payload sent to the
          Headless Page API).

        The legacy format (a JSON list) and any other type are rejected.

        Returns:
            list[str]: A (possibly empty) list of human-readable error messages.
                       An empty list means the data is valid.
        """
        errors = []
        if isinstance(data, list):
            errors.append(
                f"{file_path.name}: root element is a list — this is the legacy "
                "format. Please convert it to a dictionary keyed by fragment key."
            )
            return errors
        if not isinstance(data, dict):
            errors.append(
                f"{file_path.name}: root element must be a JSON object (dict), "
                f"got {type(data).__name__}."
            )
            return errors
        for key, value in data.items():
            if not isinstance(key, str) or not key.strip():
                errors.append(
                    f"{file_path.name}: key {key!r} is not a valid non-empty string."
                )
            if not isinstance(value, dict):
                errors.append(
                    f"{file_path.name}: value for key {key!r} must be a dict "
                    f"(fragment config payload), got {type(value).__name__}."
                )
        return errors

    def _patch_fragment_overrides(self, project_meta, paths):  # noqa: C901, PLR0912, PLR0915
        """Execute headless API requests to dynamically patch fragment configurations."""
        import base64
        import json
        import os
        import string
        import urllib.error
        import urllib.request

        overrides_file = paths["root"] / "configs" / "fragment-overrides.json"
        if not overrides_file.exists():
            overrides_file = paths["root"] / ".ldm" / "fragment-overrides.json"

        if not overrides_file.exists():
            return

        dxp_version = self.manager.parse_version(project_meta.get("tag", ""))
        if dxp_version < (2025, 1, 0):
            UI.warning(
                "fragment-overrides.json found, but DXP version is < 2025.Q1. Headless Page API not supported. Skipping patches."
            )
            return

        try:
            with open(overrides_file) as f:
                overrides = json.load(f)
        except Exception as e:
            UI.warning(f"Failed to read fragment-overrides.json: {e}")
            return

        if not overrides:
            return

        # --- Schema validation ---
        validation_errors = self._validate_fragment_overrides(overrides, overrides_file)
        if validation_errors:
            for err in validation_errors:
                UI.warning(err)
            if self.manager.non_interactive:
                on_failure = getattr(self.manager.args, "on_validation_failure", "die")
                if on_failure == "ignore":
                    UI.warning(
                        "fragment-overrides.json validation failed — continuing "
                        "(--on-validation-failure=ignore)."
                    )
                else:
                    UI.die(
                        "fragment-overrides.json validation failed. Use "
                        "--on-validation-failure=ignore to override.",
                        exit_code=1,
                    )
            elif not UI.confirm(
                "fragment-overrides.json has validation errors. Continue anyway?",
                "N",
            ):
                return

        UI.info("Executing dynamic Headless API fragment configuration patches...")

        # Determine exposed port and API client
        container_name = project_meta.get("liferay_container_name") or project_meta.get(
            "container_name"
        )

        admin_email = self.manager.config.get_global_config().get(
            "admin_email", "test@liferay.com"
        )
        admin_pass = self.manager.config.get_global_config().get(
            "admin_password", "test"
        )

        lfr_port = "8080"
        try:
            inspect_output = self.manager.run_command(
                ["docker", "port", container_name, "8080"],
                check=False,
                capture_output=True,
            )
            if inspect_output and ":" in inspect_output:
                lfr_port = inspect_output.split(":")[-1].strip()
        except Exception as e:
            UI.debug(
                f"Could not inspect mapped port for container '{container_name}': {e}. "
                "Defaulting to port 8080 — OAuth redirects may be incorrect."
            )

        # 1. Build expansion dictionary
        expansion_env = os.environ.copy()

        host_name = project_meta.get("host_name", "localhost")
        is_ssl = str(project_meta.get("ssl", "False")).lower() == "true"

        share_enabled = (
            str(project_meta.get("share", "false")).lower() == "true"
            or str(project_meta.get("expose", "false")).lower() == "true"
            or getattr(self.manager.args, "share", False)
        )

        if share_enabled and self.manager.defaults:
            tunnel_subdomain = self.manager.defaults.get("lfr_tunnel_subdomain")
            if tunnel_subdomain:
                host_name = f"{tunnel_subdomain}.lfr.cloud"
                is_ssl = True

        expansion_env["LDM_HOST_NAME"] = host_name
        expansion_env["LDM_PROJECT_ID"] = project_meta.get(
            "project_name", paths["root"].name
        )
        expansion_env["LDM_SSL_ENABLED"] = "true" if is_ssl else "false"
        expansion_env["LDM_HTTP_SCHEME"] = "https" if is_ssl else "http"
        if host_name != "localhost":
            if share_enabled:
                ext_base_url = (
                    f"https://{host_name}" if is_ssl else f"http://{host_name}"
                )
            else:
                proxy_ports = self.manager.infra.get_proxy_ports()
                if is_ssl:
                    port_suffix = (
                        f":{proxy_ports['https']}"
                        if proxy_ports.get("https", 443) != 443
                        else ""
                    )
                    ext_base_url = f"https://{host_name}{port_suffix}"
                else:
                    port_suffix = (
                        f":{proxy_ports['http']}"
                        if proxy_ports.get("http", 80) != 80
                        else ""
                    )
                    ext_base_url = f"http://{host_name}{port_suffix}"
        else:
            ext_base_url = f"http://localhost:{lfr_port}"

        expansion_env["LDM_BASE_URL"] = ext_base_url

        project_name = project_meta.get("project_name", paths["root"].name)
        svc_prefix = f"http://{project_name}-"

        # Extract Docker environment variables (which contain LIFERAY_ROUTES_*)
        if container_name:
            try:
                inspect_output = self.manager.run_command(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{range .Config.Env}}{{println .}}{{end}}",
                        container_name,
                    ],
                    check=False,
                    capture_output=True,
                )
                if inspect_output:
                    for line in inspect_output.splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            if k.startswith(
                                "LIFERAY_ROUTES_CLIENT_EXTENSION_"
                            ) and v.startswith(svc_prefix):
                                ext_id_and_port = v[len(svc_prefix) :]
                                parts = ext_id_and_port.split(":")
                                ext_id = parts[0]
                                ext_port = parts[1] if len(parts) > 1 else "8080"

                                # Add absolute direct Traefik URL for explicit bypass overrides
                                ext_k = k.replace(
                                    "LIFERAY_ROUTES_CLIENT_EXTENSION_",
                                    "LIFERAY_EXTERNAL_URL_CLIENT_EXTENSION_",
                                )
                                if host_name != "localhost":
                                    scheme = "https" if is_ssl else "http"
                                    expansion_env[ext_k] = (
                                        f"{scheme}://{ext_id}.{host_name}"
                                    )
                                else:
                                    expansion_env[ext_k] = (
                                        f"http://localhost:{ext_port}"
                                    )

                            expansion_env[k] = v
            except Exception as e:
                UI.warning(
                    f"Client extension environment variable expansion failed: {e}\n"
                    "Routes and OAuth URLs for client extensions may not resolve correctly."
                )

        def expand_vars(obj):
            if isinstance(obj, str):
                res = string.Template(obj).safe_substitute(expansion_env)
                if res != obj:
                    import re

                    # Look for ${VAR} syntax
                    for match in re.findall(r"\$\{([^}]+)\}", obj):
                        if match in expansion_env:
                            UI.detail(
                                f"  + Resolved token ${{{match}}} -> {expansion_env[match]}"
                            )
                    # Look for $VAR syntax
                    for match in re.findall(r"\$([a-zA-Z_][a-zA-Z0-9_]*)", obj):
                        if match in expansion_env:
                            UI.detail(
                                f"  + Resolved token ${match} -> {expansion_env[match]}"
                            )
                return res
            if isinstance(obj, dict):
                return {k: expand_vars(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [expand_vars(i) for i in obj]
            return obj

        overrides = expand_vars(overrides)
        auth_string = f"{admin_email}:{admin_pass}"
        auth_b64 = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth_b64}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        import ssl

        def api_request(method, path, payload=None):
            url = f"{ext_base_url}{path}"
            req = urllib.request.Request(url, headers=headers, method=method)
            if payload:
                req.data = json.dumps(payload).encode("utf-8")

            import ipaddress
            from urllib.parse import urlparse

            ctx = ssl.create_default_context()
            parsed_url = urlparse(url)
            host = parsed_url.hostname or "localhost"

            is_loopback = False
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = host.lower() in ("localhost", "127.0.0.1", "::1")

            if is_loopback:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

            try:
                with urllib.request.urlopen(req, context=ctx) as response:  # nosec B310
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as e:
                # 404 indicates feature flag missing or page not found
                if e.code == 404:
                    return None
                UI.warning(f"Headless API {method} {path} failed: {e.code} {e.reason}")
                return None
            except Exception as e:
                UI.warning(f"Headless API request failed: {e}")
                return None

        # 3. Fetch Sites and Patch (with retry to wait for OSGi JAX-RS and Site Initializer)
        import time

        max_retries = 12
        patched_count = 0

        for attempt in range(max_retries):
            sites_data = api_request("GET", "/o/headless-delivery/v1.0/sites")
            if not sites_data or "items" not in sites_data:
                UI.info(
                    f"Waiting for Headless API to become ready (attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(5)
                continue

            for site in sites_data["items"]:
                site_id = site["id"]

                pages_data = api_request(
                    "GET", f"/o/headless-delivery/v1.0/sites/{site_id}/site-pages"
                )
                if not pages_data or "items" not in pages_data:
                    continue

                for page in pages_data["items"]:
                    page_def = page.get("pageDefinition")
                    if not page_def:
                        continue

                    def patch_fragments(element, page_name):
                        nonlocal patched_count
                        if element.get("type") == "Fragment":
                            frag_key = (
                                element.get("definition", {})
                                .get("fragmentConfig", {})
                                .get("fragmentKey")
                            )
                            if frag_key in overrides:
                                element_id = element.get("id")
                                if element_id:
                                    patch_payload = {
                                        "definition": {"config": overrides[frag_key]}
                                    }
                                    res = api_request(
                                        "PATCH",
                                        f"/o/headless-delivery/v1.0/page-elements/{element_id}",
                                        payload=patch_payload,
                                    )
                                    if res:
                                        UI.success(
                                            f"  -> Patched configuration for fragment '{frag_key}' on page '{page_name}'"
                                        )
                                        patched_count += 1

                        for child in element.get("pageElements", []):
                            patch_fragments(child, page_name)

                    if "pageElement" in page_def:
                        patch_fragments(page_def["pageElement"], page.get("name"))

            if patched_count > 0:
                break

            UI.info(
                f"Waiting for Site Initializer to populate fragment pages (attempt {attempt + 1}/{max_retries})..."
            )
            time.sleep(5)

        if patched_count > 0:
            UI.success(
                f"Successfully applied {patched_count} fragment configuration overrides."
            )
        else:
            UI.warning("No matching fragments found on any site pages after waiting.")

    def cmd_wait(  # noqa: C901, PLR0912, PLR0915
        self,
        project_id=None,
        timeout=None,
        wait_for_deployables=False,
        wait_for_bundles=None,
        stream_status=False,
        stream_logs=False,
    ):
        """Block execution until project is fully ready (HTTP 200/302)."""
        if timeout is None:
            timeout = 900

        root = self.manager.detect_project_path(project_id)
        if not root:
            return None
        meta = self.manager.read_meta(root)
        host_name = meta.get("host_name", "localhost")

        container_name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or root.name
        )

        log_proc = None
        if stream_logs:
            import subprocess
            import sys

            log_proc = subprocess.Popen(
                ["docker", "logs", "-f", container_name],
                stdout=sys.stdout,
                stderr=sys.stderr,
            )

        def _die_with_logs(msg):
            if log_proc:
                log_proc.terminate()
            import subprocess
            import sys

            UI.error(
                f"Timeout exhausted. Dumping last 200 lines of logs for {container_name}:"
            )
            subprocess.run(
                ["docker", "logs", "--tail", "200", container_name],
                stdout=sys.stderr,
                stderr=sys.stderr,
                check=False,
            )
            UI.die(msg)

        # 1. Wait for Container/Log Readiness
        if not self._wait_for_ready(
            meta,
            host_name,
            timeout=timeout,
            stream_status=stream_status,
            stream_logs=stream_logs,
        ):
            _die_with_logs(
                f"Project '{project_id}' failed to become ready within {timeout}s."
            )

        # Determine target expected deployables
        expected_targets = {}
        if wait_for_deployables:
            expected_targets.update(self._scan_for_expected_deployables(root))
        if wait_for_bundles:
            for b in wait_for_bundles.split(","):
                expected_targets[b.strip()] = "Active"

        # 2. Wait for HTTP Availability
        UI.info(
            f"Verifying HTTP accessibility for {UI.CYAN}{host_name}{UI.COLOR_OFF}..."
        )
        ssl_enabled = self.manager.composer._is_ssl_active(host_name, meta)
        port = meta.get("port", 8080)
        protocol = "https" if ssl_enabled else "http"

        # LDM-388: Use explicit IP for local checks to avoid CI IPv6 quirks
        target_host = "127.0.0.1" if host_name == "localhost" else host_name
        url = f"{protocol}://{target_host}"
        if not ssl_enabled and port != 80:
            url += f":{port}"

        phase_start = time.time()
        import requests

        http_ready = False
        while time.time() - phase_start < timeout:
            try:
                # Use a short timeout for the request itself
                response = requests.get(url, timeout=5, verify=False)  # nosec B501
                if response.status_code in [200, 302]:
                    UI.success(
                        f"Project '{project_id}' is responding to HTTP ({response.status_code})."
                    )
                    http_ready = True
                    break
            except Exception as e:
                UI.debug(f"HTTP readiness check failed (will retry): {e}")
            time.sleep(2)

        if not http_ready:
            _die_with_logs(
                f"Project '{project_id}' is running but HTTP {url} is not responding correctly."
            )

        # 2b. Wait for Deployables (OSGi & Client Extensions) if any targets exist
        if expected_targets:
            UI.info(
                f"Waiting for {len(expected_targets)} deployable targets to be fully active..."
            )
            container_name = (
                meta.get("liferay_container_name")
                or meta.get("container_name")
                or root.name
            )

            # Wait for deploy directory inside container to clear
            UI.info("Checking deploy directory queue status...")
            deploy_clear = False
            deploy_start = time.time()
            while time.time() - deploy_start < timeout:
                try:
                    res = DockerService.exec(
                        container_name,
                        ["ls", "/opt/liferay/deploy"],
                        check=False,
                    )
                    if res:
                        files = [f.strip() for f in res.splitlines() if f.strip()]
                        deployables = [
                            f for f in files if f.endswith((".jar", ".zip", ".war"))
                        ]
                        if not deployables:
                            deploy_clear = True
                            break
                    else:
                        deploy_clear = True
                        break
                except Exception as e:
                    UI.debug(f"Deploy directory check failed (will retry): {e}")
                time.sleep(2)

            if not deploy_clear:
                UI.warning(
                    "Deploy directory queue did not clear, proceeding to Gogo console verification..."
                )

            # Wait for targets via Gogo Shell
            UI.info("Verifying target OSGi bundle and Client Extension states...")
            gogo_ready = False
            gogo_start = time.time()
            while time.time() - gogo_start < timeout:
                try:
                    res = DockerService.exec(
                        container_name,
                        ["sh", "-c", "echo 'lb -s' | telnet localhost 11311"],
                        check=False,
                    )
                    if res and "|" in res:
                        # Parse lb -s output
                        bundles = {}
                        for line in res.splitlines():
                            parts = [p.strip() for p in line.split("|")]
                            if len(parts) >= 4:
                                state = parts[1]
                                sym_name = parts[3]
                                bundles[sym_name] = state

                        satisfied = True
                        stalled_bundles = {}
                        missing_bundles = set()

                        for target, expected in expected_targets.items():
                            # Direct match
                            if target in bundles:
                                if bundles[target] != expected:
                                    satisfied = False
                                    stalled_bundles[target] = bundles[target]
                            else:
                                # Client Extension match: symbolic name contains the target ID and "client.extension"
                                cx_bundle_found = False
                                for sym_name, state in bundles.items():
                                    if (
                                        target in sym_name
                                        and "client.extension" in sym_name
                                    ):
                                        cx_bundle_found = True
                                        if state != expected:
                                            satisfied = False
                                            stalled_bundles[target] = state
                                        break
                                if not cx_bundle_found:
                                    satisfied = False
                                    missing_bundles.add(target)

                        if satisfied:
                            UI.success(
                                "All deployables and client extensions are fully started."
                            )
                            gogo_ready = True
                            break
                        # Periodically identify stalled deployables
                        if time.time() - getattr(self, "_last_stalled_print", 0) > 30:
                            if stalled_bundles:
                                warning_msg = "Still waiting for the following local deployables to become ACTIVE:\n"
                                for t, s in stalled_bundles.items():
                                    warning_msg += f"  - {t} (Currently: {s})\n"
                                UI.warning(warning_msg.strip())
                            self._last_stalled_print = time.time()

                        # Fail-Fast for completely missing bundles after 120s
                        if missing_bundles and (time.time() - gogo_start > 120):
                            err_msg = "Fail-Fast: The following required bundles never appeared in the OSGi container (missing from deploy/osgi folders):\n"
                            for t in missing_bundles:
                                err_msg += f"  - {t}\n"
                            _die_with_logs(err_msg.strip())

                    elif res:
                        # Gogo console responded but not with the bundle table (e.g. error/command not found)
                        break
                except Exception as e:
                    UI.debug(f"Gogo shell query failed: {e}")
                time.sleep(3)

            if not gogo_ready:
                UI.warning(
                    "Some deployable targets did not reach active state via Gogo console verification."
                )

        # 3. Wait for System to become Idle (CPU Drop)
        UI.info("Waiting for background initialization to complete (CPU Idle)...")
        idle_checks = 0
        consecutive_required = 3
        cpu_threshold = 15.0  # Consider < 15% CPU to be "idle" for Liferay

        phase_start = time.time()
        while time.time() - phase_start < timeout:
            try:
                result = self.manager.run_command(
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{.CPUPerc}}",
                        meta.get("container_name"),
                    ],
                    capture_output=True,
                    check=False,
                )
                if result:
                    cpu_str = result.strip().replace("%", "")
                    try:
                        cpu = float(cpu_str)
                        if cpu < cpu_threshold:
                            idle_checks += 1
                            if idle_checks >= consecutive_required:
                                UI.success(
                                    f"Project '{project_id}' is fully initialized and idle."
                                )
                                if log_proc:
                                    log_proc.terminate()
                                return True
                        else:
                            idle_checks = 0
                    except ValueError:
                        pass
            except Exception as e:
                UI.debug(f"Log milestone scan failed (will retry): {e}")
            time.sleep(2)

        UI.warning(
            f"Project '{project_id}' did not reach an idle state within the timeout, but is responding to HTTP."
        )
        if log_proc:
            log_proc.terminate()
        return True

    def _print_ngrok_url(self, project_id):
        """Fetches and prints the public ngrok URL from the running container."""
        import json

        from ldm_core.ui import UI

        container_name = f"{project_id}-ngrok-1"
        try:
            result = self.manager.run_command(
                [
                    "docker",
                    "exec",
                    container_name,
                    "curl",
                    "-s",
                    "http://localhost:4040/api/tunnels",
                ],
                capture_output=True,
                check=False,
            )
            if result:
                data = json.loads(result)
                for tunnel in data.get("tunnels", []):
                    if tunnel.get("public_url", "").startswith("https://"):
                        public_url = tunnel["public_url"]
                        UI.success(
                            f"🌍 Public ngrok Tunnel Active: {UI.CYAN}{public_url}{UI.COLOR_OFF}"
                        )
                        return
        except Exception as e:
            UI.debug(f"Could not retrieve ngrok public URL: {e}")
        UI.warning("ngrok container is running, but failed to retrieve public URL.")

    def _wait_for_ready(  # noqa: C901, PLR0912, PLR0915
        self,
        project_meta,
        host_name,
        total_start=None,
        timeout=600,
        stream_status=False,
        stream_logs=False,
        browser=None,
    ):
        """Wait for Liferay to become healthy and provide access information."""
        container_name = project_meta.get("container_name")
        project_id = project_meta.get("id")
        root_path = (
            self.manager.detect_project_path(project_id, for_init=True)
            if project_id
            else None
        )
        status_file = (
            root_path / ".liferay-docker" / "startup-status.json" if root_path else None
        )

        milestones = [
            ("OSGi Framework Starting", "OSGi run level"),
            (
                "Spring Web Context Initializing",
                "Initializing Spring root WebApplicationContext",
            ),
            ("Portal Startup Progress", "Starting Liferay"),
            ("Available Contexts Registered", "Available contexts"),
            ("Tomcat Server Ready", "Server startup in"),
        ]
        reached_milestones = set()

        @contextlib.contextmanager
        def null_spinner(msg):
            UI.info(f"[LDM] {msg}")

            class NullSpinner:
                def update(self, m):
                    pass

            yield NullSpinner()

        spinner_ctx = null_spinner if (stream_status or stream_logs) else UI.spinner
        start_time = time.time()
        with spinner_ctx(
            f"Waiting for Liferay to become healthy ({container_name})..."
        ) as spinner:
            last_notified_time = 0
            seen_errors = set()
            while time.time() - start_time < timeout:
                elapsed = time.time() - start_time
                # Notify every 30 seconds (Robust timestamp check)
                if elapsed - last_notified_time >= 30:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    duration_str = UI.format_duration(elapsed)
                    spinner.update(f"Still waiting for Liferay ({duration_str})...")
                    last_notified_time = elapsed
                    UI.detail(
                        f"[{timestamp}] Still waiting for Liferay to become healthy... ({duration_str})"
                    )

                    # Proactive Log Monitoring: Look for ERRORS
                    try:
                        logs = self.manager.run_command(
                            ["docker", "logs", "--tail", "100", container_name],
                            check=False,
                            capture_output=True,
                        )
                        if logs:
                            error_lines = [
                                line.strip()
                                for line in logs.splitlines()
                                if "ERROR" in line.upper()
                                or "FATAL" in line.upper()
                                or "CRITICAL" in line.upper()
                            ]

                            new_error_lines = [
                                line for line in error_lines if line not in seen_errors
                            ]

                            if new_error_lines:
                                seen_errors.update(new_error_lines)
                                UI.warning(
                                    f"LDM detected {len(new_error_lines)} new error(s) in the logs."
                                )
                                # Display the most recent unique error
                                last_unique_error = list(
                                    dict.fromkeys(new_error_lines)
                                )[-1]
                                UI.info(
                                    f"Recent log error: {UI.YELLOW}{last_unique_error[:120]}...{UI.COLOR_OFF}"
                                )

                                # --- Auto-Thaw & Hints Win ---
                                from ldm_core.utils import (
                                    check_troubleshooting_signatures,
                                )

                                advice = None
                                for err_line in reversed(new_error_lines):
                                    advice = check_troubleshooting_signatures(err_line)
                                    if advice:
                                        break

                                if advice:
                                    UI.warning(f"Troubleshooting Advice:\n  {advice}")

                                if (
                                    "ClusterBlockException" in last_unique_error
                                    or "index.blocks.read_only" in last_unique_error
                                ):
                                    UI.warning(
                                        "Detected Elasticsearch disk pressure blocking Liferay startup."
                                    )
                                    if self.manager.infra.thaw_elasticsearch():
                                        UI.success(
                                            "Auto-Thaw successful. Liferay should now proceed."
                                        )
                                    else:
                                        UI.info(
                                            f"💡 {UI.CYAN}Hint:{UI.COLOR_OFF} Your disk is likely full. Run '{UI.WHITE}ldm prune --seeds --samples{UI.COLOR_OFF}' to free space."
                                        )

                                UI.info(
                                    f"Check full logs: {UI.WHITE}ldm logs -f {container_name}{UI.COLOR_OFF}"
                                )
                    except Exception as e:
                        UI.detail(f"Warning checking startup logs context: {e}")

                    last_notified_time = elapsed

                # LDM-385: Enhanced readiness check
                # We look for the Tomcat 'Server startup' log marker as it's often
                # faster/more reliable than the Docker healthcheck in CI.
                ready_by_logs = False
                try:
                    logs = self.manager.run_command(
                        ["docker", "logs", "--tail", "100", container_name],
                        check=False,
                        capture_output=True,
                    )
                    if logs:
                        if (
                            "org.apache.catalina.startup.Catalina.start Server startup in"
                            in logs
                        ):
                            ready_by_logs = True

                        # Milestone tracking
                        latest_milestone = None
                        for title, marker in milestones:
                            if marker in logs:
                                if title not in reached_milestones:
                                    reached_milestones.add(title)
                                    if stream_status:
                                        UI.info(f"[LDM] ⏳ Phase reached: {title}")
                                    else:
                                        UI.detail(
                                            f"Startup Milestone Reached: {UI.CYAN}{title}{UI.COLOR_OFF}"
                                        )
                                        spinner.update(f"Liferay Startup: {title}...")
                                latest_milestone = title

                        if status_file and latest_milestone:
                            try:
                                status_file.parent.mkdir(parents=True, exist_ok=True)
                                status_data = {
                                    "status": "starting",
                                    "latest_milestone": latest_milestone,
                                    "milestones_reached": list(reached_milestones),
                                    "elapsed_seconds": int(elapsed),
                                }
                                with open(status_file, "w") as f:
                                    import json

                                    json.dump(status_data, f, indent=2)
                            except Exception as e:
                                UI.detail(
                                    f"Warning writing milestone status tracking file: {e}"
                                )
                except Exception as e:
                    UI.detail(f"Warning checking log milestones: {e}")

                status = self.manager.run_command(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        "{{.State.Health.Status}}",
                        container_name,
                    ],
                    check=False,
                )

                if status == "healthy" or ready_by_logs:
                    if stream_status:
                        UI.success("[LDM] Liferay is healthy!")

                    # LDM-422: Proactive Search Reindex Monitoring (UX Win)
                    if (
                        str(project_meta.get("reindex_required", "false")).lower()
                        == "true"
                    ):
                        spinner.update("Search reindexing in progress...")
                        reindex_start = time.time()
                        reindex_timeout = 900  # 15 minutes max
                        found_start = False

                        while time.time() - reindex_start < reindex_timeout:
                            try:
                                # Fetch logs to catch the transition
                                reindex_logs = self.manager.run_command(
                                    ["docker", "logs", "--tail", "200", container_name],
                                    check=False,
                                    capture_output=True,
                                )

                                # Phase 1: Detect Start
                                if not found_start and (
                                    "reindexing all" in reindex_logs.lower()
                                ):
                                    spinner.update("Reindexing all search indexes...")
                                    found_start = True

                                # Phase 2: Detect Completion
                                if "reindexing all" in reindex_logs.lower() and (
                                    "completed in" in reindex_logs.lower()
                                    or "finished" in reindex_logs.lower()
                                ):
                                    break

                                # Fallback: Idle CPU check
                                if time.time() - reindex_start > 120:
                                    stats = self.manager.run_command(
                                        [
                                            "docker",
                                            "stats",
                                            "--no-stream",
                                            "--format",
                                            "{{.CPUPerc}}",
                                            container_name,
                                        ],
                                        check=False,
                                        capture_output=True,
                                    )
                                    if (
                                        stats
                                        and float(stats.strip().replace("%", "")) < 5.0
                                    ):
                                        break

                            except Exception as e:
                                UI.detail(f"Warning tracking search reindex: {e}")
                            time.sleep(5)

                        # Clear the flag so we don't wait on future boots
                        project_meta["reindex_required"] = "false"
                        root_path = self.manager.detect_project_path(
                            project_id=project_id, for_init=True
                        )
                        if root_path:
                            self.manager.write_meta(root_path, project_meta)
                    # If we bypassed by logs, wait a tiny bit to ensure the port is truly bound
                    if status != "healthy":
                        time.sleep(2)

                    ts = getattr(self.manager.args, "total_start", None)
                    duration_total = (
                        time.time() - float(ts) if ts else time.time() - start_time
                    )

                    duration_str = UI.format_duration(duration_total)

                    UI.success(f"Liferay is ready! (Total time: {duration_str})")

                    # Execute Headless API patcher for fragment overrides
                    root_path = self.manager.detect_project_path(
                        project_id=project_id, for_init=True
                    )
                    paths = self.manager.setup_paths(root_path)
                    self._patch_fragment_overrides(project_meta, paths)

                    share_enabled = (
                        str(project_meta.get("share", "false")).lower() == "true"
                        or str(project_meta.get("expose", "false")).lower() == "true"
                        or getattr(self.manager.args, "share", False)
                    )
                    proxy_ports = self.manager.infra.get_proxy_ports()
                    active_ssl_port = proxy_ports["https"]

                    access_url = None
                    if share_enabled:
                        share_provider = (
                            project_meta.get("share_provider")
                            or getattr(self.manager.args, "share_provider", None)
                            or "lfr-tunnel"
                        )
                        share_subdomain = (
                            project_meta.get("share_subdomain")
                            or getattr(self.manager.args, "share_subdomain", None)
                            or project_meta.get("project_name")
                            or host_name
                        )
                        if share_provider in ["lfr-tunnel", "lfr-tunnel-docker"]:
                            access_url = self.manager.share.resolve_public_tunnel_url(
                                share_subdomain, project_meta.get("project_name")
                            )

                    if not access_url:
                        # Respect ssl property from args/metadata, defaulting to True if not localhost and ssl not explicitly disabled
                        ssl_arg = getattr(self.manager.args, "ssl", None)
                        use_ssl = (
                            ssl_arg if ssl_arg is not None else host_name != "localhost"
                        )
                        scheme = "https" if use_ssl else "http"

                        access_url = (
                            f"{scheme}://{host_name}"
                            if host_name != "localhost"
                            else f"http://localhost:{project_meta.get('port', 8080)}"
                        )
                        if (
                            host_name != "localhost"
                            and use_ssl
                            and active_ssl_port != 443
                        ):
                            access_url = f"https://{host_name}:{active_ssl_port}"
                        elif (
                            host_name != "localhost"
                            and not use_ssl
                            and project_meta.get("port", 8080) != 80
                        ):
                            access_url = (
                                f"http://{host_name}:{project_meta.get('port', 8080)}"
                            )

                    UI.info(
                        f"Access your instance at: {UI.CYAN}{UI.BOLD}{access_url}{UI.COLOR_OFF}"
                    )
                    is_legacy_expose = (
                        str(project_meta.get("expose", "false")).lower() == "true"
                        and str(project_meta.get("share", "false")).lower() != "true"
                    )
                    if is_legacy_expose:
                        self._print_ngrok_url(project_meta.get("container_name"))

                    if str(project_meta.get("share", "false")).lower() == "true":
                        share_subdomain = project_meta.get(
                            "share_subdomain"
                        ) or project_meta.get("project_name")
                        share_port = project_meta.get("port", 8080)
                        share_provider = (
                            project_meta.get("share_provider") or "lfr-tunnel"
                        )
                        self.manager.share.cmd_start(
                            project_id=project_meta.get("project_name"),
                            subdomain=share_subdomain,
                            ports=str(share_port),
                            provider=share_provider,
                            image=project_meta.get("share_image"),
                            inspector=str(
                                project_meta.get("share_inspector", "false")
                            ).lower()
                            == "true",
                        )

                    UI.info("=== Useful Commands ===")
                    UI.info(
                        f"  {UI.CYAN}ldm logs -f {container_name}{UI.COLOR_OFF}  Tail logs"
                    )
                    UI.info(
                        f"  {UI.CYAN}ldm shell {container_name}{UI.COLOR_OFF}    Enter bash"
                    )
                    UI.info(
                        f"  {UI.CYAN}ldm status {container_name}{UI.COLOR_OFF}   Check health"
                    )
                    UI.info(
                        f"  {UI.CYAN}ldm stop {container_name}{UI.COLOR_OFF}     Stop stack"
                    )
                    UI.info("")

                    should_open_browser = (
                        browser
                        if browser is not None
                        else getattr(self.manager.args, "browser", False)
                    )
                    if should_open_browser:
                        UI.info(f"Launching browser: {access_url}/web/guest/home")
                        open_browser(f"{access_url}/web/guest/home")
                    return True

                # Fail fast if container exited
                container_state = self.manager.get_container_status(container_name)
                if container_state == "exited":
                    UI.error(
                        f"Liferay container '{container_name}' exited unexpectedly."
                    )
                    return False

                time.sleep(5)  # Shorter sleep for more responsive status checks

        UI.error("\nTimed out waiting for Liferay to become healthy.")
        return False

    def cmd_start(self, project_id=None, service=None, all_projects=False):
        """Starts project containers."""
        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id, fatal=False)
            if not root:
                UI.die(
                    "Project not found or not initialized. Please use 'ldm run' to initialize and configure a new project."
                )
            targets = [root]

        if not targets:
            UI.info("No projects found to start.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.info(f"Starting project: {root.name}...")
            with ProjectLock(root):
                cmd = [*compose_base, "start"]
                if service:
                    cmd.append(service)
                self.manager.run_command(cmd, capture_output=capture, cwd=str(root))

    def cmd_stop(self, project_id=None, service=None, all_projects=False):
        """Stops project containers."""
        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets:
            UI.info("No projects found to stop.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.info(f"Stopping project: {root.name}...")
            cmd = [*compose_base, "stop"]
            if service:
                cmd.append(service)
            self.manager.run_command(cmd, capture_output=capture, cwd=str(root))

    def cmd_restart(self, project_id=None, service=None, all_projects=False):
        """Restarts project containers."""
        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets:
            UI.info("No projects found to restart.")
            return

        compose_base = get_compose_cmd()
        capture = not (UI.INFO_MODE or UI.VERBOSE)
        for root in targets:
            UI.info(f"Restarting project: {root.name}...")
            cmd = [*compose_base, "restart"]
            if service:
                cmd.append(service)
            self.manager.run_command(cmd, capture_output=capture, cwd=str(root))

    def cmd_down(  # noqa: C901, PLR0912, PLR0915
        self,
        project_id=None,
        service=None,
        all_projects=False,
        delete=False,
        infra=False,
        clean_hosts=False,
    ):
        """Tears down project containers and volumes."""
        is_dry_run = getattr(self.manager, "dry_run", False)

        if infra:
            if is_dry_run:
                UI.info(
                    f"{UI.BYELLOW}[Dry Run] Would tear down global Traefik infrastructure.{UI.COLOR_OFF}"
                )
            else:
                self.manager.infra.cmd_infra_down()

        targets = []
        if all_projects:
            targets = [r["path"] for r in self.manager.find_dxp_roots()]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                targets = [root]

        if not targets and not infra:
            UI.info("No projects found to tear down.")
            return

        for root in targets:
            UI.warning(f"Tearing down stack: {root.name}")

            # DNS Cleanup (if requested)
            if clean_hosts:
                meta = self.manager.read_meta(root)
                host = meta.get("host_name")
                if host and host != "localhost":
                    # Collect subdomains as well (from extensions)
                    unresolved, _non_local = self.manager.validate_project_dns(root)[1:]
                    # We remove the primary host and any unresolved subdomains
                    to_clean = [host, *unresolved]
                    if is_dry_run:
                        UI.info(
                            f"  {UI.BYELLOW}- [Dry Run] Would remove hosts entries: {', '.join(to_clean)}{UI.COLOR_OFF}"
                        )
                    else:
                        self.manager._remove_hosts_entries(hostnames=to_clean)

            if is_dry_run:
                UI.info(
                    f"  {UI.BYELLOW}- [Dry Run] Would run docker compose down -v --remove-orphans in {root.name}{UI.COLOR_OFF}"
                )
            else:
                compose_base = get_compose_cmd()
                capture = not (UI.INFO_MODE or UI.VERBOSE)
                cmd = [*compose_base, "down", "-v", "--remove-orphans"]
                if (root / "docker-compose.yml").exists():
                    self.manager.run_command(cmd, capture_output=capture, cwd=str(root))
                else:
                    UI.debug(
                        f"No docker-compose.yml found in {root}. Skipping docker-compose down."
                    )

            if delete:
                meta = self.manager.read_meta(root)
                if meta:
                    ldm_version = meta.get("ldm_version")
                    if ldm_version and self.manager.parse_version(ldm_version) >= (
                        2,
                        11,
                        75,
                    ):
                        pass
                    from ldm_core.utils import resolve_infrastructure_mode

                    db_mode = resolve_infrastructure_mode(
                        "database_mode", meta, self.manager.defaults
                    )
                    db_type = meta.get("db_type", "postgresql")

                    if db_mode == "shared" and db_type != "hypersonic":
                        from ldm_core.utils import sanitize_id

                        project_name = meta.get("project_name", root.name)
                        db_name = (
                            f"lportal_{sanitize_id(project_name).replace('-', '_')}"
                        )
                        global_db_container = (
                            "liferay-db-mysql-global"
                            if db_type in ["mysql", "mariadb"]
                            else "liferay-db-global"
                        )

                        if is_dry_run:
                            UI.info(
                                f"  {UI.BYELLOW}- [Dry Run] Would drop database {db_name} from shared container {global_db_container}{UI.COLOR_OFF}"
                            )
                        else:
                            UI.info(f"Dropping shared database schema: {db_name}")
                            drop_cmd = []
                            if db_type == "postgresql":
                                drop_cmd = [
                                    "docker",
                                    "exec",
                                    global_db_container,
                                    "dropdb",
                                    "-U",
                                    "liferay",
                                    "--if-exists",
                                    db_name,
                                ]
                            elif db_type in ["mysql", "mariadb"]:
                                drop_cmd = [
                                    "docker",
                                    "exec",
                                    global_db_container,
                                    "mysql",
                                    "-u",
                                    "root",
                                    "-pliferay",
                                    "-e",
                                    f"DROP DATABASE IF EXISTS {db_name};",
                                ]

                            if drop_cmd:
                                try:
                                    subprocess.run(
                                        drop_cmd, check=False, capture_output=True
                                    )
                                except Exception as e:
                                    UI.warning(
                                        f"Failed to drop shared database {db_name} (container might be offline): {e}"
                                    )

                if is_dry_run:
                    UI.warning(
                        f"  {UI.BYELLOW}- [Dry Run] Would unregister project {root.name} and permanently delete directory {root}{UI.COLOR_OFF}"
                    )
                else:
                    UI.warning(f"Permanently deleting project directory: {root.name}")

                    # Release the lock before attempting deletion to avoid WinError 32 on Windows
                    path_key = Path(root).resolve().as_posix()
                    if (
                        hasattr(self.manager, "_active_locks")
                        and path_key in self.manager._active_locks
                    ):
                        self.manager._active_locks[path_key].release()
                        del self.manager._active_locks[path_key]

                    self.manager.unregister_project(root.name)
                    self.manager.safe_rmtree(root)

    def cmd_browser(self, project_id=None):
        """Opens the project's URL in the default browser."""
        from ldm_core.utils import open_browser

        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        meta = self.manager.read_meta(root)
        host_name = meta.get("host_name", "localhost")
        ssl_enabled = str(meta.get("ssl", "false")).lower() == "true"
        port = meta.get("port", 8080)

        protocol = "https" if ssl_enabled else "http"
        url = f"{protocol}://{host_name}"
        if not ssl_enabled and port != 80:
            url += f":{port}"

        UI.info(f"Opening browser: {UI.CYAN}{url}{UI.COLOR_OFF}")
        open_browser(url)

    def cmd_renew_ssl(self, project_id=None, all_projects=False):
        """Forces renewal of SSL certificates for projects."""
        targets = []
        if all_projects:
            targets = [
                {"path": r["path"], "meta": self.manager.read_meta(r["path"])}
                for r in self.manager.find_dxp_roots()
            ]
        else:
            root = self.manager.detect_project_path(project_id)
            if root:
                meta = self.manager.read_meta(root)
                targets.append({"path": root, "meta": meta})

        if not targets:
            UI.info("No projects found for SSL renewal.")
            return

        actual_home = get_actual_home()
        cert_dir = actual_home / "liferay-docker-certs"

        for target in targets:
            host_name = target["meta"].get("host_name")
            if host_name and host_name != "localhost":
                UI.info(f"Renewing SSL for {UI.CYAN}{host_name}{UI.COLOR_OFF}...")
                # Delete existing certs to force renewal
                for f in [f"{host_name}.pem", f"{host_name}-key.pem"]:
                    if (cert_dir / f).exists():
                        (cert_dir / f).unlink()
                self.manager.infra.setup_ssl(cert_dir, host_name)

        UI.success(
            "SSL renewal complete. Changes will be detected by Traefik automatically."
        )

    def cmd_reset(self, project_id=None, target="all"):
        """Wipes local state (data, logs, osgi/state) for a project."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return None

        is_dry_run = getattr(self.manager, "dry_run", False)
        if is_dry_run:
            UI.warning(
                f"[Dry Run] Resetting {UI.BOLD}{root.name}{UI.COLOR_OFF} ({target})..."
            )
            meta = self.manager.read_meta(root)
            c_name = meta.get("container_name") or root.name
            if target == "all":
                UI.info(
                    f"  {UI.BYELLOW}- Would stop/tear down project stack (down).{UI.COLOR_OFF}"
                )
            else:
                UI.info(
                    f"  {UI.BYELLOW}- Would stop project stack if running.{UI.COLOR_OFF}"
                )

            targets = ["data", "logs", "state"] if target == "all" else [target]
            for t in targets:
                if t in ["data", "state"]:
                    volume_name = f"{c_name}-{t}"
                    UI.info(
                        f"  {UI.BYELLOW}- Would delete Docker named volume: {volume_name}{UI.COLOR_OFF}"
                    )
                paths = self.manager.setup_paths(root)
                path = paths.get(t)
                if path and path.exists():
                    UI.info(
                        f"  {UI.BYELLOW}- Would delete host directory: {path.relative_to(root) if path.is_relative_to(root) else path}{UI.COLOR_OFF}"
                    )
            UI.success(
                f"[Dry Run] Project {root.name} reset completed (no changes made)."
            )
            return True

        UI.warning(f"Resetting {UI.BOLD}{root.name}{UI.COLOR_OFF} ({target})...")

        meta = self.manager.read_meta(root)
        c_name = meta.get("container_name") or root.name
        from ldm_core.docker_service import DockerService

        is_running = DockerService.is_running(c_name)

        # LDM-388: If target is 'all', we must 'down -v' to destroy anonymous DB volumes
        if target == "all":
            self.cmd_down(root.name, delete=False)
        elif is_running:
            self.cmd_stop(root.name)

        # 2. Wipe directories
        paths = self.manager.setup_paths(root)
        targets = []
        targets = ["data", "logs", "state"] if target == "all" else [target]

        for t in targets:
            path = paths.get(t)

            # LDM-369: Handle Named Volumes (Hybrid Mount Strategy)
            if t in ["data", "state"]:
                volume_name = f"{c_name}-{t}"
                # Check if this volume exists in Docker
                try:
                    res = self.manager.run_command(
                        ["docker", "volume", "ls", "-q", "-f", f"name=^{volume_name}$"],
                        check=False,
                    )
                    if res.strip():
                        UI.detail(
                            f"  - Removing Docker volume {UI.CYAN}{volume_name}{UI.COLOR_OFF}..."
                        )
                        self.manager.run_command(
                            ["docker", "volume", "rm", "-f", volume_name], check=False
                        )
                except Exception as e:
                    UI.detail(f"Warning removing docker volume {volume_name}: {e}")

            if path and path.exists():
                UI.detail(f"  - Cleaning {t} (host)...")
                shutil.rmtree(path)
                path.mkdir(parents=True, exist_ok=True)

        UI.success(f"Project {root.name} reset successful.")
        return None

    def cmd_gogo(self, project_id=None):
        """Connects to the OSGi Gogo shell."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        meta = self.manager.read_meta(root)
        port = meta.get("gogo_port")

        if not port or port == "None":
            UI.die(
                "Gogo shell is not exposed. Run 'ldm run --gogo-port <port>' to enable it."
            )

        UI.info(f"Connecting to Gogo shell on localhost:{port}...")
        try:
            subprocess.run(["telnet", "localhost", str(port)], check=False)
        except FileNotFoundError:
            UI.error("telnet not found. Run: telnet localhost " + str(port))
        except KeyboardInterrupt:
            pass

    def _cmd_logs_instance(  # noqa: PLR0913
        self,
        project_id=None,
        service=None,
        instance=1,
        follow=False,
        tail="100",
        timestamps=False,
        since=None,
        until=None,
        grep=None,
        grep_i=False,
        grep_v=False,
        level=None,
        export=False,
    ):
        """Stream logs from a single scaled replica via 'docker logs'.

        Container name is resolved using the pattern stored in project metadata
        (written by cmd_scale). Falls back to the Docker Compose v2 naming
        convention: {project}-{service}-{index}.
        """
        root = self.manager.detect_project_path(project_id)
        if not root:
            UI.die("Project not found.")

        meta = self.manager.read_meta(root)
        from ldm_core.utils import sanitize_id

        project_name = sanitize_id(meta.get("container_name") or root.name)

        # Default service to 'liferay' when not specified
        svc = (
            service[0]
            if isinstance(service, list) and service
            else (service or "liferay")
        )

        # Validate instance index against stored scale
        scale_key = f"scale_{svc}"
        max_instances = int(meta.get(scale_key, 1))
        if instance < 1 or instance > max_instances:
            if max_instances == 1:
                UI.error(
                    f"Service '{svc}' is not scaled (only 1 instance). "
                    f"Use 'ldm logs' without --instance to view its logs."
                )
            else:
                UI.error(
                    f"Invalid instance index {instance} for service '{svc}'. "
                    f"Valid range: 1–{max_instances} (current scale={max_instances})."
                )
            return

        # Fast path: use pattern stored in metadata by cmd_scale
        pattern_key = f"container_name_pattern_{svc}"
        pattern = meta.get(pattern_key)
        if pattern:
            container_name = pattern.replace("{index}", str(instance))
        else:
            # Fallback: Docker Compose v2 standard naming convention
            container_name = f"{project_name}-{svc}-{instance}"

        # Confirm the container exists
        check = self.manager.run_command(
            ["docker", "ps", "-a", "-q", "-f", f"name=^{container_name}$"],
            check=False,
        )
        if not check:
            UI.error(
                f"Container '{container_name}' not found. "
                f"Is '{project_name}' running with {max_instances} replica(s)?"
            )
            return

        UI.info(
            f"Streaming logs for {UI.CYAN}{container_name}{UI.COLOR_OFF} "
            f"(instance {instance} of {max_instances})..."
        )

        cmd = ["docker", "logs"]
        if follow:
            cmd.append("-f")
        if tail:
            cmd.extend(["--tail", str(tail)])
        if timestamps:
            cmd.append("-t")
        if since:
            cmd.extend(["--since", str(since)])
        if until:
            cmd.extend(["--until", str(until)])
        cmd.append(container_name)

        self._run_log_command(
            cmd,
            cwd=str(root),
            grep=grep,
            grep_i=grep_i,
            grep_v=grep_v,
            level=level,
            follow=follow,
            export=export,
            export_prefix=f"{root.name}-{container_name}",
        )

    def _run_log_command(  # noqa: C901, PLR0912, PLR0913, PLR0915
        self,
        cmd,
        env=None,
        cwd=None,
        grep=None,
        grep_i=False,
        grep_v=False,
        level=None,
        follow=False,
        export=False,
        export_prefix="logs",
    ):
        """Runs the log command, streaming, filtering, and performing troubleshooting diagnostics."""
        if not grep and not level and not follow and not export:
            self.manager.run_command(
                cmd, env=env, cwd=cwd, capture_output=False, check=False
            )
            return

        import os
        import re
        import shutil
        import subprocess
        import sys

        from ldm_core.utils import check_troubleshooting_signatures

        seen_troubleshooting = set()

        # Build regex pattern if grep is specified
        pattern = None
        if grep:
            flags = re.IGNORECASE if grep_i else 0
            try:
                pattern = re.compile(grep, flags)
            except re.error as e:
                UI.die(f"Invalid grep regular expression: {e}")

        # Severity level configuration
        SEVERITY_LEVELS = {
            "DEBUG": 10,
            "INFO": 20,
            "WARN": 30,
            "WARNING": 30,
            "ERROR": 40,
            "FATAL": 50,
        }

        target_severity = None
        if level:
            norm_level = level.upper()
            target_severity = SEVERITY_LEVELS.get(norm_level)
            if target_severity is None:
                UI.die(f"Invalid log level: {level}")

        LEVEL_PATTERNS = {
            "FATAL": re.compile(r"\bFATAL\b|\[FATAL\]"),
            "ERROR": re.compile(r"\bERROR\b|\[ERROR\]"),
            "WARN": re.compile(r"\bWARN(?:ING)?\b|\[WARN(?:ING)?\]"),
            "INFO": re.compile(r"\bINFO\b|\[INFO\]"),
            "DEBUG": re.compile(r"\bDEBUG\b|\[DEBUG\]"),
        }

        def get_line_level(line):
            for lvl in ["FATAL", "ERROR", "WARN", "INFO", "DEBUG"]:
                if LEVEL_PATTERNS[lvl].search(line):
                    return lvl
            return None

        # Resolve path to command executable (Bandit B607)
        if isinstance(cmd, list) and len(cmd) > 0:
            executable = shutil.which(cmd[0])
            if executable:
                cmd[0] = executable

        display_cmd = UI.redact(" ".join(cmd) if isinstance(cmd, list) else cmd)
        if self.manager.verbose:
            UI.debug(f"Executing log command: {display_cmd}")

        if getattr(self.manager, "dry_run", False):
            UI.info(
                f"{UI.BYELLOW}[DRY RUN] Would execute log command:{UI.COLOR_OFF} {display_cmd}"
            )
            return

        run_env = os.environ.copy() if env is None else env.copy()
        run_env["DOCKER_CLI_HINTS"] = "false"
        if "DOCKER_API_VERSION" not in run_env:
            run_env["DOCKER_API_VERSION"] = "1.44"

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                env=run_env,
                cwd=cwd,
                bufsize=1,
            )

            # Default print_subsequent state.
            # If level filtering is active, default to False to hide startup noise.
            print_subsequent = level is None
            export_file = None

            try:
                while True:
                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break
                    if line:
                        stripped_line = line.rstrip("\r\n")
                        clean_line = strip_ansi(stripped_line)

                        # 1. Level Filter evaluation
                        if target_severity is not None:
                            line_level = get_line_level(clean_line)
                            if line_level is not None:
                                level_severity = SEVERITY_LEVELS[line_level]
                                print_subsequent = level_severity >= target_severity
                                match_level = print_subsequent
                            else:
                                match_level = print_subsequent
                        else:
                            match_level = True

                        # 2. Grep Filter evaluation
                        if match_level:
                            if pattern is not None:
                                match_grep = bool(pattern.search(clean_line))
                                if grep_v:
                                    match_grep = not match_grep
                            else:
                                match_grep = True
                        else:
                            match_grep = False

                        advice = (
                            check_troubleshooting_signatures(clean_line)
                            if follow
                            else None
                        )
                        if advice and advice not in seen_troubleshooting:
                            seen_troubleshooting.add(advice)
                            print(
                                f"\n{UI.BYELLOW}⚠️  LDM TROUBLESHOOTING ADVICE:{UI.COLOR_OFF}"
                            )
                            print(f"👉 {UI.BWHITE}{advice}{UI.COLOR_OFF}\n")
                            sys.stdout.flush()

                        if match_grep:
                            if export:
                                if export_file is None:
                                    from datetime import datetime

                                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                                    export_filename = f"{export_prefix}_{ts}.log"
                                    export_file = open(  # noqa: SIM115
                                        export_filename, "w", encoding="utf-8"
                                    )
                                    UI.info(
                                        f"Exporting logs to: {UI.CYAN}{export_filename}{UI.COLOR_OFF}"
                                    )
                                export_file.write(stripped_line + "\n")
                            else:
                                print(stripped_line)
                                sys.stdout.flush()
            finally:
                if export_file is not None:
                    export_file.close()
                if process.stdout:
                    process.stdout.close()
            process.wait()
        except KeyboardInterrupt:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()

    def cmd_logs(  # noqa: C901, PLR0912, PLR0913, PLR0915
        self,
        project_id=None,
        service=None,
        all_projects=False,
        infra=False,
        follow=False,
        no_wait=False,
        tail="100",
        timestamps=False,
        since=None,
        until=None,
        instance=None,
        grep=None,
        grep_i=False,
        grep_v=False,
        level=None,
        export=False,
        include_infra=False,
    ):
        """Shows logs for a project or global infrastructure."""
        if include_infra and export and not infra:
            UI.info("Including infrastructure logs in export...")
            self.cmd_logs(
                project_id=project_id,
                service=service,
                all_projects=all_projects,
                infra=True,
                follow=follow,
                no_wait=no_wait,
                tail=tail,
                timestamps=timestamps,
                since=since,
                until=until,
                instance=instance,
                grep=grep,
                grep_i=grep_i,
                grep_v=grep_v,
                level=level,
                export=True,
                include_infra=False,
            )

        if instance is not None:
            self._cmd_logs_instance(
                project_id=project_id,
                service=service,
                instance=instance,
                follow=follow,
                tail=tail,
                timestamps=timestamps,
                since=since,
                until=until,
                grep=grep,
                grep_i=grep_i,
                grep_v=grep_v,
                level=level,
                export=export,
            )
            return

        if infra:
            UI.info("Showing infrastructure logs...")
            containers = []
            if not service or "proxy" in service:
                containers.append("liferay-proxy-global")
            if not service or "es" in service:
                containers.append("liferay-search-global")

            for container in containers:
                self.manager.run_command(
                    ["docker", "ps", "-q", "-f", f"name=^{container}$"]
                )

            infra_compose = self.manager.get_resource_path("infra-compose.yml")
            if not infra_compose:
                UI.die("Infrastructure compose file 'infra-compose.yml' not found.")

            cmd = [*get_compose_cmd(), "-f", str(infra_compose), "logs"]
            if follow:
                cmd.append("-f")

            if tail:
                cmd.extend(["--tail", str(tail)])

            if timestamps:
                cmd.append("-t")

            if since:
                cmd.extend(["--since", str(since)])

            if until:
                cmd.extend(["--until", str(until)])

            env = self.manager.infra._get_infra_env()
            self._run_log_command(
                cmd,
                env=env,
                grep=grep,
                grep_i=grep_i,
                grep_v=grep_v,
                level=level,
                follow=follow,
                export=export,
                export_prefix="infra",
            )
        else:
            targets = []
            if all_projects:
                targets = [r["path"] for r in self.manager.find_dxp_roots()]
            else:
                root = self.manager.detect_project_path(project_id)
                if root:
                    targets = [root]

            if not targets:
                UI.info("No running projects found to show logs.")
                return

            for root in targets:
                if self.manager.verbose:
                    UI.debug(f"Processing logs for project: {root.name} in {root}")

                meta = self.manager.read_meta(root)
                meta.get("container_name") or root.name
                target_service = (
                    service if service and not isinstance(service, list) else "liferay"
                )

                # LDM-381: Resolve the actual container name using labels
                actual_container = self.manager.resolve_container(
                    root.name, target_service
                )

                # Check if it exists
                check_cmd = [
                    "docker",
                    "ps",
                    "-a",
                    "-q",
                    "-f",
                    f"name=^{actual_container}$",
                ]
                if not self.manager.run_command(check_cmd, check=False):
                    if no_wait:
                        UI.error(
                            f"Service '{target_service}' in project '{root.name}' does not exist. Skipping."
                        )
                        continue

                    UI.info(
                        f"Waiting for container {UI.CYAN}{root.name}{UI.COLOR_OFF} (service: {target_service})..."
                    )
                    start_wait = time.time()
                    found = False
                    while time.time() - start_wait < 60:
                        elapsed = int(time.time() - start_wait)
                        if elapsed > 0 and elapsed % 10 == 0:
                            UI.info(
                                f"  ... still waiting for '{root.name}' ({elapsed}s)"
                            )

                        # Re-resolve in case it was created during wait
                        actual_container = self.manager.resolve_container(
                            root.name, target_service
                        )
                        if self.manager.run_command(check_cmd, check=False):
                            found = True
                            break
                        time.sleep(2)

                    if not found:
                        UI.error(f"Container '{root.name}' did not appear within 60s.")
                        continue

                if follow:
                    log_dir = root / "logs"
                    if not log_dir.exists():
                        if no_wait:
                            UI.error(
                                f"Logs directory missing in {root.name}. Skipping."
                            )
                            continue

                        UI.info(f"Waiting for logs directory in {root.name}...")
                        start_wait = time.time()
                        while not log_dir.exists() and time.time() - start_wait < 30:
                            time.sleep(1)

                cmd = [*get_compose_cmd(), "logs"]
                if follow:
                    cmd.append("-f")

                if tail:
                    cmd.extend(["--tail", str(tail)])

                if timestamps:
                    cmd.append("-t")

                if since:
                    cmd.extend(["--since", str(since)])

                if until:
                    cmd.extend(["--until", str(until)])

                if service:
                    if isinstance(service, list):
                        cmd.extend(service)
                    else:
                        cmd.append(service)
                self._run_log_command(
                    cmd,
                    cwd=str(root),
                    grep=grep,
                    grep_i=grep_i,
                    grep_v=grep_v,
                    level=level,
                    follow=follow,
                    export=export,
                    export_prefix=f"{root.name}-{actual_container}",
                )

    def cmd_deploy(self, project_id=None, targets=None, service=None):
        """Deploys a project, specific services, or individual artifacts."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        paths, meta = self.manager.setup_paths(root), self.manager.read_meta(root)

        # Normalize targets (legacy support for service parameter)
        if service and not targets:
            targets = [service]
        elif not targets:
            targets = []

        if not targets:
            # Full stack sync
            self.sync_stack(
                paths, meta, rebuild=getattr(self.manager.args, "rebuild", False)
            )
            return

        # Handle specific targets (services or files)
        from ldm_core.utils import atomic_copy

        services_to_up = set()
        for t in targets:
            t_path = Path(t)
            if t_path.exists() and t_path.is_file():
                ext = t_path.suffix.lower()
                if ext in [".jar", ".war"]:
                    dest = paths["modules"] / t_path.name
                    UI.detail(f"Syncing Module: {t_path.name}")
                    atomic_copy(t_path, dest)
                elif ext == ".zip":
                    # Potentially a CX or Fragment
                    from ldm_core.handlers.workspace import WorkspaceService

                    handler = WorkspaceService(self.manager)
                    handler._sync_cx_artifact(t_path, paths)
                else:
                    UI.warning(f"Unsupported file type for deployment: {t}")
            else:
                # Treat as service name
                services_to_up.add(t)

        if services_to_up:
            for svc in sorted(services_to_up):
                UI.info(f"Deploying service '{svc}'...")
                self.manager.run_command(
                    [*get_compose_cmd(), "up", "-d", svc],
                    capture_output=False,
                    cwd=str(root),
                )
        else:
            UI.success("Artifact deployment complete.")

    def cmd_shell(self, project_id=None, service="liferay"):
        """Enters a project container via bash."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return
        service_name = service or "liferay"

        # LDM-381: Resolve the actual container name using labels
        target_container = self.manager.resolve_container(root.name, service_name)

        UI.info(f"Entering container: {target_container}")
        try:
            subprocess.run(
                ["docker", "exec", "-it", target_container, "/bin/bash"], check=False
            )
        except KeyboardInterrupt:
            pass

    def cmd_scale(self, project_id, scale_args, no_run=False):
        """Scales project services."""
        project_path = self.manager.detect_project_path(project_id)
        if not project_path:
            UI.die("Project not found.")

        meta = self.manager.read_meta(project_path)
        from ldm_core.utils import sanitize_id

        project_name = sanitize_id(meta.get("container_name") or project_path.name)

        for arg in scale_args:
            if "=" not in arg:
                UI.error(f"Invalid scale argument: {arg}. Expected service=number")
                continue
            service, count = arg.split("=", 1)
            if not count.isdigit():
                UI.error(f"Invalid scale count for {service}: {count}")
                continue
            meta[f"scale_{service}"] = count
            # Store the standard naming pattern so future lookups avoid docker ps.
            # Docker Compose v2 convention: {compose_project}-{service}-{index}
            meta[f"container_name_pattern_{service}"] = (
                f"{project_name}-{service}-{{index}}"
            )

        self.manager.write_meta(project_path, meta)
        UI.success(f"Updated scale factors for project {project_path.name}")

        if not no_run:
            # Trigger regeneration and restart (pass is_restart=True to bypass running check)
            self.cmd_run(project_id, is_restart=True)

    def cmd_migrate_search(self, project_id=None):
        """Migrates a project from Sidecar to Global Elasticsearch."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return

        p_id = root.name
        paths = self.manager.setup_paths(p_id)

        # 1. Ensure Liferay is NOT running
        is_running = self.manager.run_command(
            ["docker", "ps", "-q", "-f", f"name=^{p_id}$"], check=False
        )
        if is_running:
            UI.die(
                f"Project '{p_id}' is currently running. Please stop it first with: ldm stop {p_id}"
            )

        UI.heading(f"Migrating '{p_id}' to Global Search")

        # 2. Check if Global Search is running
        search_running = self.manager.run_command(
            ["docker", "ps", "-q", "-f", "name=^liferay-search-global$"], check=False
        )
        if not search_running:
            if (
                UI.ask(
                    "Global Search container is not running. Start it now?", "Y"
                ).upper()
                == "Y"
            ):
                self.manager.infra.setup_global_search()
            else:
                UI.die("Migration aborted. Global Search is required.")

        # 3. Clean up internal indices
        data_dir = paths["data"]
        indices_found = False
        for es_dir in ["elasticsearch7", "elasticsearch8"]:
            target = data_dir / es_dir
            if target.exists():
                UI.detail(f"Removing internal index directory: {target}")
                shutil.rmtree(target)
                indices_found = True

        if not indices_found:
            UI.detail("No internal sidecar indices found. (Already clean?)")

        # 4. Sync configuration
        UI.detail("Applying Global Search configurations...")
        # We force use_shared_search=True in meta
        project_meta = self.manager.read_meta(root)
        project_meta["use_shared_search"] = "true"
        self.manager.write_meta(root, project_meta)

        # sync_common_assets will now find the global search running and copy the configs
        self.manager.config.sync_common_assets(paths)

        UI.success(
            f"Migration complete! Project '{p_id}' is now configured for Global Search."
        )

        if not self.manager.non_interactive:
            if UI.ask("Restart project now?", "Y").upper() == "Y":
                self.cmd_run(project_id)

    def cmd_reindex(self, project_id=None):
        """Triggers search reindexing (immediately if running, otherwise on next boot)."""
        root = self.manager.detect_project_path(project_id)
        if not root:
            return

        from ldm_core.docker_service import DockerService

        meta = self.manager.read_meta(root)
        container_name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or root.name
        )
        force_boot = getattr(self.manager.args, "force_boot", False)

        is_running = DockerService.is_running(container_name)

        if is_running and not force_boot:
            UI.info(
                f"Liferay container '{container_name}' is running. Triggering immediate runtime reindex..."
            )
            groovy_code = 'com.liferay.portal.kernel.search.IndexWriterHelperUtil.reindex(0, "reindex", [com.liferay.portal.kernel.util.PortalUtil.getDefaultCompanyId()] as long[], null)'
            command_list = [
                "sh",
                "-c",
                f"echo '{groovy_code}' | telnet localhost 11311",
            ]
            try:
                DockerService.exec(container_name, command_list, check=True)
                UI.success(
                    f"Successfully triggered immediate runtime reindex on '{container_name}'."
                )
                return
            except Exception as e:
                UI.warning(
                    f"Failed to execute immediate reindex via Gogo shell ({e}). Falling back to boot-time scheduling."
                )

        if self.flag_reindex(root):
            UI.success(
                f"Project '{root.name}' scheduled for search reindex on next boot."
            )
            if not self.manager.non_interactive:
                if UI.confirm("Do you want to restart the project now to apply?", "Y"):
                    self.cmd_run(root.name)
        else:
            UI.error(f"Failed to schedule reindex for project '{root.name}'.")
