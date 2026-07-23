import json
import os
import ssl
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

        UI.detail("Executing dynamic Headless API fragment configuration patches...")

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

        # Candidate API base targets (base_url, extra_headers) tried in order
        candidate_api_targets: list[tuple[str, dict[str, str]]] = []

        # 1. Mapped direct port or default localhost (direct access)
        candidate_api_targets.append((f"http://127.0.0.1:{lfr_port}", {}))

        # 2. External base URL (e.g. http://aica.local, https://aica.local, or http://localhost:8080)
        if ext_base_url and ext_base_url != f"http://127.0.0.1:{lfr_port}":
            candidate_api_targets.append((ext_base_url, {}))

        # 3. If host_name is custom (e.g. aica.local), try loopback proxy with Host header
        if host_name != "localhost":
            try:
                proxy_ports = self.manager.infra.get_proxy_ports()
                http_p = proxy_ports.get("http", 80)
                https_p = proxy_ports.get("https", 443)
                p_suffix = f":{http_p}" if http_p != 80 else ""
                candidate_api_targets.append(
                    (f"http://127.0.0.1{p_suffix}", {"Host": host_name})
                )
                p_ssl_suffix = f":{https_p}" if https_p != 443 else ""
                candidate_api_targets.append(
                    (f"https://127.0.0.1{p_ssl_suffix}", {"Host": host_name})
                )
            except Exception:
                pass

        working_target: tuple[str, dict[str, str]] | None = None

        def api_request(method, path, payload=None):
            nonlocal working_target

            targets_to_try = (
                [working_target] if working_target else candidate_api_targets
            )

            for base_url, extra_headers in targets_to_try:
                url = f"{base_url}{path}"
                req_headers = headers.copy()
                req_headers.update(extra_headers)

                req = urllib.request.Request(url, headers=req_headers, method=method)
                if payload:
                    req.data = json.dumps(payload).encode("utf-8")

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                try:
                    with urllib.request.urlopen(req, context=ctx) as response:  # nosec B310
                        res = json.loads(response.read().decode())
                        working_target = (base_url, extra_headers)
                        return res
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        working_target = (base_url, extra_headers)
                        return None
                    UI.warning(
                        f"Headless API {method} {path} failed: {e.code} {e.reason}"
                    )
                    working_target = (base_url, extra_headers)
                    return None
                except Exception as e:
                    UI.debug(f"Headless API candidate {base_url} failed: {e}")

            return None

        # 3. Fetch Sites and Patch (with retry to wait for OSGi JAX-RS and Site Initializer)

        max_retries = 60
        patched_count = 0
        all_discovered_keys: set = set()
        debug_page_tree: list = []

        def extract_candidates(element):
            """Collect all candidate key identifiers from a page element.

            Probes the element itself, definition, fragmentConfig, fragmentEntryLink,
            and sub-objects used by the Headless Delivery API (2025.Q1+).
            """
            candidates = []

            def_obj = (
                element.get("definition")
                if isinstance(element.get("definition"), dict)
                else {}
            )
            frag_config = (
                def_obj.get("fragmentConfig")
                if isinstance(def_obj.get("fragmentConfig"), dict)
                else {}
            )
            fel = (
                element.get("fragmentEntryLink")
                if isinstance(element.get("fragmentEntryLink"), dict)
                else {}
            )
            fel_entry = (
                fel.get("fragmentEntry")
                if isinstance(fel.get("fragmentEntry"), dict)
                else {}
            )
            fel_frag = (
                fel.get("fragment") if isinstance(fel.get("fragment"), dict) else {}
            )

            for obj in (element, def_obj, frag_config, fel, fel_entry, fel_frag):
                if isinstance(obj, dict):
                    for field in (
                        "externalReferenceCode",
                        "fragmentKey",
                        "fragmentEntryKey",
                        "key",
                        "id",
                        "name",
                    ):
                        val = obj.get(field)
                        if val and isinstance(val, str):
                            candidates.append(val)

            return candidates

        def patch_fragments(element, page_name):
            nonlocal patched_count
            elem_type = str(element.get("type", "")).lower()
            candidates = extract_candidates(element)
            all_discovered_keys.update(candidates)

            if candidates:
                UI.debug(
                    f"  [fragment-scan] page={page_name!r} type={elem_type!r} candidates={candidates}"
                )

            matched_key = None
            for c in candidates:
                if c in overrides:
                    matched_key = c
                    break
                if c.lower() in overrides:
                    matched_key = c.lower()
                    break

                # Support collection-namespaced or prefixed fragment keys (e.g. "collection-key/fragment-key" -> "fragment-key")
                c_tail = c.split("/")[-1].split(":")[-1]
                if c_tail in overrides:
                    matched_key = c_tail
                    break
                if c_tail.lower() in overrides:
                    matched_key = c_tail.lower()
                    break

            if matched_key:
                element_id = element.get("id")
                if element_id:
                    patch_payload = {
                        "definition": {
                            "config": overrides[matched_key],
                            "fragmentConfig": overrides[matched_key],
                        }
                    }
                    res = api_request(
                        "PATCH",
                        f"/o/headless-delivery/v1.0/page-elements/{element_id}",
                        payload=patch_payload,
                    )
                    if res:
                        UI.success(
                            f"  -> Patched configuration for fragment '{matched_key}' on page '{page_name}'"
                        )
                        patched_count += 1

            # Traverse all child keys used by different layout element types
            for child_key in (
                "pageElement",
                "pageElements",
                "columns",
                "rows",
                "elements",
                "children",
                "components",
            ):
                children = element.get(child_key)
                if isinstance(children, list):
                    for child in children:
                        if isinstance(child, dict):
                            patch_fragments(child, page_name)
                elif isinstance(children, dict):
                    patch_fragments(children, page_name)

        for attempt in range(max_retries):
            sites_data = api_request("GET", "/o/headless-delivery/v1.0/sites")
            if not sites_data or "items" not in sites_data:
                UI.detail(
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
                        # Collection endpoints omit heavy pageDefinition objects; fetch individual page details
                        page_id = page.get("id")
                        if page_id:
                            page_details = api_request(
                                "GET", f"/o/headless-delivery/v1.0/site-pages/{page_id}"
                            )
                            if page_details:
                                page_def = (
                                    page_details.get("pageDefinition") or page_details
                                )
                    if not page_def:
                        continue

                    debug_page_tree.append(page_def)
                    patch_fragments(page_def, page.get("name"))

            if patched_count > 0:
                break

            UI.detail(
                f"Waiting for Site Initializer to populate fragment pages (attempt {attempt + 1}/{max_retries})..."
            )
            time.sleep(5)

        if patched_count > 0:
            UI.success(
                f"Successfully applied {patched_count} fragment configuration overrides."
            )
        else:
            configured_keys = sorted(overrides.keys())
            discovered_keys = sorted(all_discovered_keys)
            unmatched = sorted(set(configured_keys) - set(discovered_keys))
            UI.warning("No matching fragments found on any site pages after waiting.")
            UI.detail(
                f"  Keys configured in fragment-overrides.json : {configured_keys}"
            )
            UI.detail(
                f"  Keys discovered across all page elements   : "
                f"{discovered_keys if discovered_keys else '(none)'}"
            )
            if unmatched:
                UI.detail(f"  Unmatched keys (in config but not found)   : {unmatched}")
            elif not discovered_keys:
                UI.detail(
                    "  No fragment elements found at all — Site Initializer may not have "
                    "run yet, or the API page element structure is unexpected."
                )
            debug_path = paths["root"] / ".ldm" / "fragment-override-debug.json"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(json.dumps(debug_page_tree, indent=2))
            UI.detail(f"  Raw page tree written to: {debug_path}")

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
