import json
import os
import time

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI


class FragmentsService(BaseHandler):
    """Fragments service for runtime operations."""

    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager

    def _patch_fragment_overrides(self, project_meta, paths):  # noqa: C901, PLR0912, PLR0915
        """Execute headless API requests to dynamically patch fragment configurations."""
        import base64
        import string
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
