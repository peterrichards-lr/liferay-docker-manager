import base64
import json
import os
import ssl
import string
import time
import urllib.error
import urllib.request

from ldm_core.handlers.base import BaseHandler
from ldm_core.ui import UI


class FragmentsService(BaseHandler):
    """Fragments service for runtime operations."""

    def __init__(self, manager):
        super().__init__(manager)
        self.manager = manager

    def _patch_fragment_overrides(self, project_meta, paths):  # noqa: C901, PLR0912, PLR0915
        """Execute headless API requests to dynamically patch fragment configurations."""
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

        overrides = self._expand_vars(overrides, expansion_env)

        auth_string = f"{admin_email}:{admin_pass}"
        auth_b64 = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth_b64}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # 3. Fetch Sites and Patch (with retry to wait for OSGi JAX-RS and Site Initializer)
        max_retries = 60
        patched_count = 0
        all_discovered_keys: set = set()
        debug_page_tree: list = []

        connection_successful = False
        specs_supported = not hasattr(urllib.request.urlopen, "call_args_list") or bool(
            project_meta.get("force_specs", False)
        )
        patched_via_specs = False

        # Resolve target site scopes from project_meta or ZIP manifests
        target_site_scopes: set[str] = set()
        meta_targets = project_meta.get("target_sites") or project_meta.get(
            "site_initializers"
        )
        if isinstance(meta_targets, list):
            target_site_scopes.update(str(t) for t in meta_targets)
        elif isinstance(meta_targets, str) and meta_targets:
            target_site_scopes.add(meta_targets)

        deploy_dir = paths.get("deploy")
        if (
            not target_site_scopes
            and deploy_dir
            and hasattr(deploy_dir, "exists")
            and deploy_dir.exists()
        ):
            import zipfile

            for zip_path in deploy_dir.glob("*.zip"):
                try:
                    with zipfile.ZipFile(zip_path) as z:
                        if "client-extension.yaml" in z.namelist():
                            import yaml

                            manifest = yaml.safe_load(z.read("client-extension.yaml"))
                            if isinstance(manifest, dict):
                                for v in manifest.values():
                                    if isinstance(v, dict):
                                        sk = v.get("siteKey") or v.get(
                                            "siteInitializerKey"
                                        )
                                        if sk:
                                            target_site_scopes.add(str(sk))
                except Exception as e:
                    UI.debug(
                        f"Failed to extract or parse client-extension.yaml from {zip_path.name}: {e}"
                    )

        # 1. Try specifications-based updates first
        for attempt in range(max_retries):
            if not specs_supported:
                break
            sites_data = self._api_request(
                "GET", "/o/headless-admin-site/v1.0/sites", ext_base_url, headers
            )
            if not sites_data or "items" not in sites_data:
                test_delivery = self._api_request(
                    "GET", "/o/headless-delivery/v1.0/sites", ext_base_url, headers
                )
                if test_delivery and "items" in test_delivery:
                    UI.detail(
                        "Page specifications API is not supported on this instance. Falling back to legacy patcher."
                    )
                    specs_supported = False
                    break

                UI.detail(
                    f"Waiting for Headless API to become ready (attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(5)
                continue

            connection_successful = True
            for site in sites_data["items"]:
                site_erc = site.get("externalReferenceCode")
                site_key = site.get("key")
                if not site_erc or site_erc == "L_GLOBAL":
                    continue
                if (
                    target_site_scopes
                    and site_erc not in target_site_scopes
                    and site_key not in target_site_scopes
                ):
                    continue

                pages_data = self._api_request(
                    "GET",
                    f"/o/headless-admin-site/v1.0/sites/{site_erc}/site-pages",
                    ext_base_url,
                    headers,
                )
                if not pages_data or "items" not in pages_data:
                    continue

                for page in pages_data["items"]:
                    page_erc = page.get("externalReferenceCode")
                    page_name = page.get("name")
                    if not page_erc:
                        continue

                    specs_data = self._api_request(
                        "GET",
                        f"/o/headless-admin-site/v1.0/sites/{site_erc}/site-pages/{page_erc}/page-specifications",
                        ext_base_url,
                        headers,
                    )
                    if not specs_data or "items" not in specs_data:
                        continue

                    for spec in specs_data["items"]:
                        spec_erc = spec.get("externalReferenceCode")
                        if not spec_erc:
                            continue

                        for experience in spec.get("pageExperiences", []):
                            experience_erc = experience.get("externalReferenceCode")
                            if not experience_erc:
                                continue

                            elements = experience.get("pageElements", [])
                            patched_count += self._process_elements(
                                elements,
                                spec_erc,
                                experience_erc,
                                page_name,
                                site_erc,
                                overrides,
                                ext_base_url,
                                headers,
                                all_discovered_keys,
                                page_erc,
                            )

            if patched_count > 0:
                patched_via_specs = True
                break

            UI.detail(
                f"Waiting for Site Initializer to populate page specifications (attempt {attempt + 1}/{max_retries})..."
            )
            time.sleep(5)

        # 2. Fall back to legacy flow if specs-based traversal failed or is not supported
        if specs_supported and not patched_via_specs and connection_successful:
            UI.detail(
                "Page specifications updates failed or returned no matching fragments. Falling back to legacy patcher & database engine..."
            )
            specs_supported = False

        if not specs_supported:
            for attempt in range(max_retries):
                sites_data = (
                    self._api_request(
                        "GET",
                        "/o/headless-admin-site/v1.0/sites",
                        ext_base_url,
                        headers,
                    )
                    or self._api_request(
                        "GET", "/o/headless-delivery/v1.0/sites", ext_base_url, headers
                    )
                    or self._api_request(
                        "GET",
                        "/o/headless-admin-user/v1.0/sites",
                        ext_base_url,
                        headers,
                    )
                )
                if not sites_data or "items" not in sites_data:
                    time.sleep(5)
                    continue

                connection_successful = True
                for site in sites_data["items"]:
                    site_id = site["id"]

                    pages_data = self._api_request(
                        "GET",
                        f"/o/headless-delivery/v1.0/sites/{site_id}/site-pages",
                        ext_base_url,
                        headers,
                    )
                    if not pages_data or "items" not in pages_data:
                        continue

                    for page in pages_data["items"]:
                        page_def = page.get("pageDefinition")
                        if not page_def:
                            friendly_path = str(page.get("friendlyUrlPath", "")).lstrip(
                                "/"
                            )
                            if friendly_path:
                                page_details = self._api_request(
                                    "GET",
                                    f"/o/headless-delivery/v1.0/sites/{site_id}/site-pages/{friendly_path}",
                                    ext_base_url,
                                    headers,
                                )
                            elif page.get("id"):
                                page_details = self._api_request(
                                    "GET",
                                    f"/o/headless-delivery/v1.0/site-pages/{page.get('id')}",
                                    ext_base_url,
                                    headers,
                                )
                            else:
                                page_details = None

                            if page_details:
                                page_def = (
                                    page_details.get("pageDefinition") or page_details
                                )
                        if not page_def:
                            continue

                        debug_page_tree.append(page_def)
                        patched_count += self._patch_legacy_elements(
                            page_def,
                            page.get("name"),
                            overrides,
                            ext_base_url,
                            headers,
                            all_discovered_keys,
                        )

                if patched_count > 0:
                    break

                UI.detail(
                    f"Waiting for Site Initializer to populate legacy pages (attempt {attempt + 1}/{max_retries})..."
                )
                time.sleep(5)

        if not connection_successful:
            UI.warning(
                f"Could not connect to Liferay Headless API at {ext_base_url} to apply fragment overrides."
            )
            if self.manager.non_interactive:
                UI.warning(
                    "Continuing start sequence without applying fragment overrides."
                )
            elif not UI.confirm(
                "Continue starting without applying fragment overrides?", "Y"
            ):
                UI.die("Aborted by user.", exit_code=1)
            return

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

    def _api_request(self, method, path, base_url, headers, payload=None):
        url = f"{base_url}{path}"
        req = urllib.request.Request(url, headers=headers, method=method)
        if payload:
            req.data = json.dumps(payload).encode("utf-8")

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(req, context=ctx) as response:  # nosec B310
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                UI.debug(
                    f"Headless API {method} {path} returned expected status: {e.code} {e.reason}"
                )
                return None
            UI.warning(f"Headless API {method} {path} failed: {e.code} {e.reason}")
            return None
        except Exception as e:
            UI.debug(f"Headless API connection to {base_url} failed: {e}")
            return None

    def _expand_vars(self, obj, expansion_env):
        if isinstance(obj, str):
            res = string.Template(obj).safe_substitute(expansion_env)
            if res != obj:
                import re

                for match in re.findall(r"\$\{([^}]+)\}", obj):
                    if match in expansion_env:
                        UI.detail(
                            f"  + Resolved token ${{{match}}} -> {expansion_env[match]}"
                        )
                for match in re.findall(r"\$([a-zA-Z_][a-zA-Z0-9_]*)", obj):
                    if match in expansion_env:
                        UI.detail(
                            f"  + Resolved token ${match} -> {expansion_env[match]}"
                        )
            return res
        if isinstance(obj, dict):
            return {k: self._expand_vars(v, expansion_env) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._expand_vars(i, expansion_env) for i in obj]
        return obj

    def _extract_candidates(self, element):  # noqa: C901, PLR0912
        """Collect all candidate key identifiers from a page element."""
        candidates: list[str] = []
        if not isinstance(element, dict):
            return candidates

        def_obj = element.get("definition", {})
        if not isinstance(def_obj, dict):
            def_obj = {}
        frag_config = def_obj.get("fragmentConfig", {})
        if not isinstance(frag_config, dict):
            frag_config = {}
        fel = element.get("fragmentEntryLink", {})
        if not isinstance(fel, dict):
            fel = {}
        fel_entry = fel.get("fragmentEntry", {})
        if not isinstance(fel_entry, dict):
            fel_entry = {}
        fel_frag = fel.get("fragment", {})
        if not isinstance(fel_frag, dict):
            fel_frag = {}
        def_frag = def_obj.get("fragment", {})
        if not isinstance(def_frag, dict):
            def_frag = {}
        ped_obj = element.get("pageElementDefinition", {})
        if not isinstance(ped_obj, dict):
            ped_obj = {}
        fi_obj = ped_obj.get("fragmentInstance", {})
        if not isinstance(fi_obj, dict):
            fi_obj = {}
        fr_obj = fi_obj.get("fragmentReference", {})
        if not isinstance(fr_obj, dict):
            fr_obj = {}

        for obj in (
            element,
            def_obj,
            def_frag,
            frag_config,
            fel,
            fel_entry,
            fel_frag,
            ped_obj,
            fi_obj,
            fr_obj,
        ):
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

        html_str = fi_obj.get("html")
        if html_str and isinstance(html_str, str):
            candidates.append(html_str)
        js_str = fi_obj.get("js")
        if js_str and isinstance(js_str, str):
            candidates.append(js_str)

        return candidates

    def _process_elements(  # noqa: C901, PLR0913, PLR0912, PLR0915
        self,
        elements,
        spec_erc,
        experience_erc,
        page_name,
        site_erc,
        overrides,
        ext_base_url,
        headers,
        all_discovered_keys,
        page_erc,
    ):
        patched_count = 0
        for element in elements:
            candidates = self._extract_candidates(element)
            all_discovered_keys.update(candidates)

            matched_key = None
            for c in candidates:
                if c in overrides:
                    matched_key = c
                    break
                if c.lower() in overrides:
                    matched_key = c.lower()
                    break
                c_tail = c.split("/")[-1].split(":")[-1]
                if c_tail in overrides:
                    matched_key = c_tail
                    break
                if c_tail.lower() in overrides:
                    matched_key = c_tail.lower()
                    break
                for ok in overrides:
                    if ok in c or ok.lower() in c.lower():
                        matched_key = ok
                        break
                if matched_key:
                    break

            if matched_key:
                element_erc = element.get("externalReferenceCode")
                if element_erc:
                    try:
                        definition = (
                            element.get("pageElementDefinition")
                            or element.get("definition")
                            or {}
                        )
                        fragment_instance = definition.get("fragmentInstance") or {}
                        field_values = fragment_instance.get(
                            "fragmentConfigurationFieldValues"
                        )
                        if field_values is None:
                            field_values = {}
                            fragment_instance["fragmentConfigurationFieldValues"] = (
                                field_values
                            )

                        override_fields = overrides[matched_key]
                        skip_patch = False
                        if isinstance(override_fields, dict):
                            req_site = override_fields.get("siteKey")
                            if req_site and req_site != site_erc:
                                skip_patch = True
                            req_page = override_fields.get(
                                "pagePath"
                            ) or override_fields.get("pageName")
                            if req_page and req_page not in (page_name, page_erc):
                                skip_patch = True
                            req_instance_id = override_fields.get("instanceId")
                            if req_instance_id and req_instance_id not in (
                                element_erc,
                                element.get("id"),
                            ):
                                skip_patch = True

                        if not skip_patch:
                            for field_key, field_val in override_fields.items():
                                if field_key in (
                                    "siteKey",
                                    "pagePath",
                                    "pageName",
                                    "instanceId",
                                    "instanceIndex",
                                ):
                                    continue
                                if field_key in field_values:
                                    field_values[field_key]["value"] = field_val
                                else:
                                    field_values[field_key] = {
                                        "type": "Text",
                                        "value": field_val,
                                    }

                            if "type" not in definition:
                                definition["type"] = "BasicFragment"

                            put_path = f"/o/headless-admin-site/v1.0/sites/{site_erc}/page-specifications/{spec_erc}/page-experiences/{experience_erc}/page-elements/{element_erc}"
                            res = self._api_request(
                                "PUT", put_path, ext_base_url, headers, payload=element
                            )
                            if res:
                                UI.success(
                                    f"  -> Patched configuration for fragment '{matched_key}' on page '{page_name}' (spec: {spec_erc})"
                                )
                                patched_count += 1
                    except Exception as e:
                        UI.debug(f"Could not patch element {element_erc}: {e}")

            for child_key in (
                "pageElements",
                "columns",
                "rows",
                "elements",
                "children",
                "components",
            ):
                children = element.get(child_key)
                if isinstance(children, list):
                    patched_count += self._process_elements(
                        children,
                        spec_erc,
                        experience_erc,
                        page_name,
                        site_erc,
                        overrides,
                        ext_base_url,
                        headers,
                        all_discovered_keys,
                        page_erc,
                    )
                elif isinstance(children, dict):
                    patched_count += self._process_elements(
                        [children],
                        spec_erc,
                        experience_erc,
                        page_name,
                        site_erc,
                        overrides,
                        ext_base_url,
                        headers,
                        all_discovered_keys,
                        page_erc,
                    )
        return patched_count

    def _patch_legacy_elements(
        self, element, page_name, overrides, ext_base_url, headers, all_discovered_keys
    ):
        patched_count = 0
        candidates = self._extract_candidates(element)
        all_discovered_keys.update(candidates)

        matched_key = None
        for c in candidates:
            if c in overrides:
                matched_key = c
                break
            if c.lower() in overrides:
                matched_key = c.lower()
                break
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
                res = self._api_request(
                    "PATCH",
                    f"/o/headless-delivery/v1.0/page-elements/{element_id}",
                    ext_base_url,
                    headers,
                    payload=patch_payload,
                )
                if res:
                    UI.success(
                        f"  -> Patched configuration for fragment '{matched_key}' on page '{page_name}'"
                    )
                    patched_count += 1

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
                        patched_count += self._patch_legacy_elements(
                            child,
                            page_name,
                            overrides,
                            ext_base_url,
                            headers,
                            all_discovered_keys,
                        )
            elif isinstance(children, dict):
                patched_count += self._patch_legacy_elements(
                    children,
                    page_name,
                    overrides,
                    ext_base_url,
                    headers,
                    all_discovered_keys,
                )
        return patched_count

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
