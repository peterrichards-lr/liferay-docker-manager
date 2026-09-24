import json
import os
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

if TYPE_CHECKING:
    pass
from ldm_core.constants import SCRIPT_DIR
from ldm_core.ui import UI
from ldm_core.utils import (
    is_env_var_blacklisted,
    load_env_blacklist,
    safe_copy,
)


def _parse_client_extension_yaml(self, content):
    """Parse client-extension.yaml content using PyYAML for robust structured extraction.

    Uses yaml.safe_load instead of fragile regex matching so that comments,
    nested configuration blocks, and multi-line YAML values are all handled
    correctly.  Falls back to an empty result on any parse failure.
    """
    info: dict[str, Any] = {"type": None, "oauth_erc": None}
    try:
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            # Top-level "type" field (single-block format)
            if "type" in data:
                info["type"] = str(data["type"]).strip()
            # Top-level oAuthApplicationHeadlessServer ERC (single-block format)
            if "oAuthApplicationHeadlessServer" in data:
                info["oauth_erc"] = str(data["oAuthApplicationHeadlessServer"]).strip()
            else:
                # Multi-block format: scan nested configuration dicts
                for block in data.values():
                    if not isinstance(block, dict):
                        continue
                    if info["type"] is None and "type" in block:
                        info["type"] = str(block["type"]).strip()
                    if info["oauth_erc"] is None and (
                        erc := block.get("oAuthApplicationHeadlessServer")
                    ):
                        info["oauth_erc"] = str(erc).strip()
                    if info["type"] and info["oauth_erc"]:
                        break
    except Exception:
        pass
    return info


def _get_effective_blacklist(self, paths=None):
    blacklist = load_env_blacklist(SCRIPT_DIR / "common" / "env-blacklist.txt")
    if paths and paths.get("root"):
        proj_blacklist = load_env_blacklist(paths["root"] / "env-blacklist.txt")
        blacklist.extend(proj_blacklist)
    return sorted(set(blacklist))


def _parse_client_extension_config_json(self, content):
    try:
        data = json.loads(content)
        prefix = "com.liferay.oauth2.provider.configuration.OAuth2ProviderApplicationHeadlessServerConfiguration~"
        for key, val in data.items():
            if key.startswith(prefix):
                return key[len(prefix) :]
            if val.get("type") == "oAuthApplicationHeadlessServer":
                return val.get("projectId") or val.get("projectName")
    except Exception:
        pass
    return None


def parse_cx_project_name(content):
    """The name Liferay publishes this extension's routes tree under.

    LDM-#1944. Liferay does NOT use the `LCP.json` id for that directory. The
    chain, from the portal source:

    * the CX build emits `<name>.client-extension-config.json` carrying BOTH
      `projectId` (equal to the `LCP.json` id) and `projectName` (equal to the
      extension directory)
    * `BaseConfigurationFactory` reads `ext.lxc.liferay.com.projectName`, or
      `projectName`, and publishes it as the label
      `ext.lxc.liferay.com/projectName`
    * `RoutesPortalK8sConfigMapModifier` resolves the path straight from that
      label and calls `Files.createDirectories` on it

    So the tree is `routes/default/<projectName>`, and an extension mounted at
    `routes/default/<id>` reads a real, empty, readable directory while Liferay
    fills a different one beside it.

    The two are not interchangeable: the id drops the hyphens the directory
    keeps. Across `ldm-cx-samples`, all 16 differ.

    The configuration key is also a fallback source, because it is suffixed
    with the same name -- `...HeadlessServerConfiguration~ecopulse-headless-auth`.
    """
    try:
        data = json.loads(content)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    prefix = "com.liferay.oauth2.provider.configuration.OAuth2ProviderApplicationHeadlessServerConfiguration~"
    suffix_name = None
    for key, val in data.items():
        if isinstance(val, dict):
            # `projectName` is authoritative -- it is the value Liferay itself
            # resolves the directory from. `projectId` is deliberately NOT a
            # fallback: it is the id, which is the wrong string.
            name = val.get("ext.lxc.liferay.com.projectName") or val.get("projectName")
            if name:
                return str(name).strip()
        if suffix_name is None and key.startswith(prefix):
            suffix_name = key[len(prefix) :]
    return suffix_name


def _parse_lcp_json(self, content, context_name=None):
    info: dict[str, Any] = {
        "id": None,
        "kind": None,
        "deploy": True,  # Default to True if not specified
        "cpu": 1,
        "memory": 512,
        "ports": [],
        "loadBalancer": None,
        "readinessProbe": None,
        "livenessProbe": None,
        "oauth_erc": None,
        "project_name": None,  # LDM-#1944
        "env": {},
        "has_load_balancer": False,
    }
    try:
        data = json.loads(content)

        # Proactive Validation
        # Create a temporary file to use the validator (which expects a path)
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            tf.write(content)
            tf_path = Path(tf.name)

        try:
            status, ok, errors = self.manager.diagnostics.validate_lcp_json(tf_path)
            if not ok or ok == "warn":
                header = "LCP.json Issue"
                if context_name:
                    header = f"LCP.json Issue ({context_name})"
                UI.warning(f"{header}: {status}")
                if errors:
                    for err in errors:
                        UI.raw(f"  {UI.YELLOW}⚠{UI.COLOR_OFF} {err}")
        finally:
            if tf_path.exists():
                tf_path.unlink()

        info["id"] = data.get("id")
        if data.get("kind"):
            info["kind"] = data["kind"].capitalize()
        for k in [
            "cpu",
            "memory",
            "ports",
            "deploy",
            "loadBalancer",
            "readinessProbe",
            "livenessProbe",
        ]:
            if k in data:
                info[k] = data[k]
        info["has_load_balancer"] = "loadBalancer" in data or any(
            p.get("external") for p in cast(list[dict[str, Any]], info.get("ports", []))
        )
        env = data.get("env", {})
        info["env"] = env
        if "LIFERAY_BATCH_OAUTH_APP_ERC" in env:
            info["oauth_erc"] = env["LIFERAY_BATCH_OAUTH_APP_ERC"]
    except Exception:
        pass
    return info


def _merge_metadata_info(self, info, new_info):
    for k in info:
        if new_info.get(k) is not None:
            if k == "has_load_balancer":
                info[k] = info[k] or new_info[k]
            elif k == "ports" and new_info[k]:
                cast(list, info[k]).extend(new_info[k])
            elif k == "loadBalancer" and new_info[k]:
                if info[k] is None:
                    info[k] = {}
                cast(dict, info[k]).update(new_info[k])
            else:
                info[k] = new_info[k]
    if new_info.get("env"):
        cast(dict, info["env"]).update(new_info["env"])


def _scan_extension_zip_metadata(self, zip_ref, info):
    for f in zip_ref.namelist():
        name = Path(f).name
        if name in ["client-extension.yaml", "client-extension.yml"]:
            _merge_metadata_info(
                self,
                info,
                _parse_client_extension_yaml(self, zip_ref.read(f).decode("utf-8")),
            )
        elif name == "LCP.json":
            _merge_metadata_info(
                self,
                info,
                _parse_lcp_json(
                    self, zip_ref.read(f).decode("utf-8"), info.get("contextName")
                ),
            )
        elif name == "client-extension-config.json" or f.endswith(
            ".client-extension-config.json"
        ):
            content = zip_ref.read(f).decode("utf-8")
            erc = _parse_client_extension_config_json(self, content)
            if erc:
                info["oauth_erc"] = erc
            # LDM-#1944: same in the deployed-zip path as in the folder scan.
            project_name = parse_cx_project_name(content)
            if project_name:
                info["project_name"] = project_name


def _scan_extension_folder_metadata(self, folder_path, info):
    yaml_file = next(folder_path.glob("client-extension.y*ml"), None)
    if yaml_file:
        _merge_metadata_info(
            self, info, _parse_client_extension_yaml(self, yaml_file.read_text())
        )
    lcp_file = folder_path / "LCP.json"
    if lcp_file.exists():
        _merge_metadata_info(
            self,
            info,
            _parse_lcp_json(self, lcp_file.read_text(), info.get("contextName")),
        )
    cfg_file = next(folder_path.glob("*client-extension-config.json"), None)
    if cfg_file:
        content = cfg_file.read_text()
        erc = _parse_client_extension_config_json(self, content)
        if erc:
            info["oauth_erc"] = erc
        # LDM-#1944: the name Liferay publishes the routes tree under. Not the
        # LCP.json id -- see parse_cx_project_name.
        project_name = parse_cx_project_name(content)
        if project_name:
            info["project_name"] = project_name


def _scan_extension_metadata(self, folder_path=None, zip_ref=None):
    info: dict[str, Any] = {
        "id": None,
        "type": None,
        "kind": None,
        "deploy": True,
        "cpu": 1,
        "memory": 512,
        "ports": [],
        "loadBalancer": None,
        "readinessProbe": None,
        "livenessProbe": None,
        "oauth_erc": None,
        "project_name": None,  # LDM-#1944
        "env": {},
        "has_load_balancer": False,
    }

    if zip_ref:
        _scan_extension_zip_metadata(self, zip_ref, info)
    elif folder_path:
        _scan_extension_folder_metadata(self, folder_path, info)
    return info


def _resolve_and_persist_cx_port(self, ext_info, ext_id, meta, root_dir):
    load_balancer = ext_info.get("loadBalancer") or {}
    default_port = next(
        (p.get("port") for p in ext_info.get("ports", []) if isinstance(p, dict)),
        load_balancer.get("targetPort", 8080),
    )
    try:
        default_port = int(default_port)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        default_port = 8080

    meta_port_key = f"port_{ext_id}"
    if meta_port_key not in meta:
        resolved_port = self.manager.find_available_port("127.0.0.1", default_port)
        meta[meta_port_key] = str(resolved_port)
        self.manager.write_meta(root_dir, meta)


def _process_built_cx_zips(  # noqa: C901
    self,
    ce_build_dir,
    ce_source_truth,
    osgi_cx_dir,
    root_dir,
    host_name,
    meta,
    extensions,
):
    import os

    from ldm_core.utils import UI

    if not ce_build_dir or not ce_build_dir.exists():
        return

    for item in [i for i in ce_build_dir.iterdir() if i.suffix.lower() == ".zip"]:
        try:
            target_folder = ce_source_truth / item.stem
            root_zip_copy = ce_source_truth / item.name

            is_same = False
            try:
                if item.resolve() == root_zip_copy.resolve() or os.path.samefile(
                    item, root_zip_copy
                ):
                    is_same = True
            except Exception:
                pass

            if not is_same:
                safe_copy(item, root_zip_copy)

            with zipfile.ZipFile(root_zip_copy, "r") as zip_ref:
                ext_info = _scan_extension_metadata(self, zip_ref=zip_ref)
                namelist = zip_ref.namelist()

                if ext_info["type"] or any(
                    Path(f).name in ["client-extension.yaml", "LCP.json"]
                    for f in namelist
                ):
                    is_service = (
                        any(Path(f).name == "Dockerfile" for f in namelist)
                        and ext_info.get("kind") != "Job"
                        and ext_info.get("deploy", True) is not False
                    )

                    if is_service:
                        if target_folder.exists():
                            self.manager.safe_rmtree(target_folder)
                        target_folder.mkdir(parents=True)
                        from ldm_core.utils import safe_extract

                        safe_extract(zip_ref, target_folder)

                    dest_zip = osgi_cx_dir / item.name
                    is_same_dest = False
                    try:
                        if (
                            root_zip_copy.resolve() == dest_zip.resolve()
                            or os.path.samefile(root_zip_copy, dest_zip)
                        ):
                            is_same_dest = True
                    except Exception:
                        pass

                    if not is_same_dest:
                        if dest_zip.exists():
                            os.remove(dest_zip)
                        safe_copy(root_zip_copy, dest_zip)

                    ext_id = ext_info.get("id") or item.stem
                    if is_service:
                        _resolve_and_persist_cx_port(
                            self, ext_info, ext_id, meta, root_dir
                        )

                    if host_name:
                        self._rewrite_oauth_urls_in_zip(
                            dest_zip,
                            host_name,
                            item.stem.lower().replace("_", "-"),
                            root_dir,
                        )

                    extensions.append(
                        {
                            "name": item.stem.lower().replace("_", "-"),
                            "id": ext_info.get("id") or item.stem,
                            "path": target_folder,
                            "is_service": is_service,
                            "port": next(
                                (
                                    p.get("port")
                                    for p in ext_info.get("ports", [])
                                    if p.get("external")
                                ),
                                80,
                            ),
                            **ext_info,
                        }
                    )
        except Exception as e:
            UI.error(f"Failed to process {item.name}: {e}")


def _process_existing_cx_folders(
    self, ce_source_truth, osgi_cx_dir, root_dir, host_name, meta, extensions, found_ids
):
    for item in [
        i
        for i in ce_source_truth.iterdir()
        if i.is_dir() and not i.name.startswith(".")
    ]:
        ext_info = _scan_extension_metadata(self, folder_path=item)
        if ext_info["type"] or (item / "LCP.json").exists():
            found_ids.add(item.name)
            is_service = (item / "Dockerfile").exists() and ext_info.get(
                "kind"
            ) != "Job"
            port = next(
                (p.get("port") for p in ext_info.get("ports", []) if p.get("external")),
                80,
            )
            entry = {
                "id": ext_info.get("id") or item.name,
                "name": item.name.lower().replace("_", "-"),
                "port": port,
                "path": item,
                "is_service": is_service,
                **ext_info,
            }
            ext_id = ext_info.get("id") or item.name
            if is_service:
                _resolve_and_persist_cx_port(self, ext_info, ext_id, meta, root_dir)

            if host_name:
                dest_zip = osgi_cx_dir / f"{item.name}.zip"
                if not dest_zip.exists():
                    alt_name = item.name.replace("-", "_")
                    if (osgi_cx_dir / f"{alt_name}.zip").exists():
                        dest_zip = osgi_cx_dir / f"{alt_name}.zip"
                    elif (osgi_cx_dir / f"{item.name.replace('_', '-')}.zip").exists():
                        dest_zip = osgi_cx_dir / f"{item.name.replace('_', '-')}.zip"

                if dest_zip.exists():
                    self._rewrite_oauth_urls_in_zip(
                        dest_zip,
                        host_name,
                        item.name.lower().replace("_", "-"),
                        root_dir,
                    )

            existing = next((e for e in extensions if e["id"] == entry["id"]), None)
            if existing:
                existing.update(entry)
            else:
                extensions.append(entry)


def scan_client_extensions(self, root_dir, osgi_cx_dir, ce_build_dir, host_name=None):
    extensions: list[dict[str, Any]] = []
    if not root_dir.exists():
        return extensions
    meta = self.manager.read_meta(root_dir) or {}

    ce_source_truth = root_dir / "client-extensions"
    ce_source_truth.mkdir(parents=True, exist_ok=True)
    osgi_cx_dir.mkdir(parents=True, exist_ok=True)

    found_ids: set[str] = set()

    _process_built_cx_zips(
        self,
        ce_build_dir,
        ce_source_truth,
        osgi_cx_dir,
        root_dir,
        host_name,
        meta,
        extensions,
    )
    _process_existing_cx_folders(
        self,
        ce_source_truth,
        osgi_cx_dir,
        root_dir,
        host_name,
        meta,
        extensions,
        found_ids,
    )

    return extensions


def scan_standalone_services(self, root_path):
    services: list[dict[str, Any]] = []
    services_dir = root_path / "services"
    if not services_dir.exists():
        return services
    for item in [
        i for i in services_dir.iterdir() if i.is_dir() and not i.name.startswith(".")
    ]:
        if (item / "LCP.json").exists() and (item / "Dockerfile").exists():
            ext_info = _scan_extension_metadata(self, folder_path=item)
            port = next(
                (p.get("port") for p in ext_info.get("ports", []) if p.get("external")),
                80,
            )
            services.append(
                {
                    "id": ext_info.get("id") or item.name,
                    "name": item.name.lower().replace("_", "-"),
                    "path": item,
                    "port": port,
                    "is_standalone": True,
                    **ext_info,
                }
            )
    return services


def _announce_withheld(withheld):
    """Names variables that matched a forwarding rule but were withheld.

    A dropped variable used to be indistinguishable from one that was never
    set, which is how LDM-#1903 cost an external team days: they exported
    `COM_LIFERAY_LXC_DXP_*` because the documentation listed the prefix as
    automatic passthrough, and nothing said the `_DXP_` subset was excluded.

    Names only, never values -- the point of withholding a credential is not
    served by printing it.
    """
    if not withheld:
        return
    from ldm_core.ui import UI

    names = sorted(withheld)
    UI.detail(f"Withheld from container environment (blacklisted): {', '.join(names)}")
    UI.detail(
        "  If one of these is needed, add its negation to the project's "
        "env-blacklist.txt:"
    )
    # The exact line, not a placeholder. LDM-#1910 blocks credential shapes on
    # every route, so the users most likely to see this are the ones whose
    # working setup just stopped -- notably anyone supplying an OAuth2 client
    # extension's secret by environment because LDM never populates the routes
    # that should carry it (LDM-#1911). Making them derive the syntax from an
    # example is friction at exactly the wrong moment.
    for name in names:
        UI.detail(f"    !{name}")


def get_host_passthrough_env(self, paths=None, target_id=None):
    blacklist = _get_effective_blacklist(self, paths)
    withheld: set[str] = set()

    # 1. Global Strip-Forwarding (LDM_VAR=xxx -> VAR=xxx in ALL containers)
    global_pool = {}
    for k, v in os.environ.items():
        if not k.upper().startswith("LDM_"):
            continue
        if is_env_var_blacklisted(k, blacklist):
            withheld.add(k)
            continue
        global_pool[k[4:]] = v

    # 2. Passthrough Prefixes (Preserve prefix, forward to ALL containers)
    # Default set covers Liferay Cloud and common AI providers
    passthrough_prefixes = [
        "LXC_",
        "COM_LIFERAY_LXC_",
        "OPENAI_",
        "GEMINI_",
        "ANTHROPIC_",
        "MISTRAL_",
    ]
    # Allow user to extend this list via host environment
    extra_prefixes = os.environ.get("LDM_FORWARD_PREFIXES")
    if extra_prefixes:
        passthrough_prefixes.extend(
            [p.strip() for p in extra_prefixes.split(",") if p.strip()]
        )

    for k, v in os.environ.items():
        if not any(k.upper().startswith(p.upper()) for p in passthrough_prefixes):
            continue
        if is_env_var_blacklisted(k, blacklist):
            withheld.add(k)
            continue
        global_pool[k] = v

    _announce_withheld(withheld)

    if not target_id:
        # Multi-service request (used for initialcompose generation)
        res = [f"{k}={v}" for k, v in global_pool.items()]
        exts = self.scan_client_extensions(paths["root"], paths["cx"], paths["ce_dir"])
        services = self.scan_standalone_services(paths["root"])

        # Add un-stripped service/liferay vars to the comprehensive list
        for k, v in os.environ.items():
            if is_env_var_blacklisted(k, blacklist):
                continue
            if k.upper().startswith("LIFERAY_") or any(
                k.upper().startswith(e["id"].upper().replace("-", "_") + "_")
                for e in exts + services
            ):
                res.append(f"{k}={v}")
        return sorted(set(res))

    # Targeted service request: [SERVICE_ID]_VAR=xxx -> VAR=xxx
    targeted = get_service_targeted_env(self, target_id, blacklist=blacklist)
    return [f"{k}={v}" for k, v in {**global_pool, **targeted}.items()]


def get_service_targeted_env(self, target_id, paths=None, blacklist=None):
    """Host variables explicitly addressed to one service (LDM-#1903).

    ``SERVICEID_FOO=bar`` in the host environment becomes ``FOO=bar`` inside
    that service's container -- section 4 of ``docs/reference/configuration.md``.
    The Liferay container is the exception and keeps the full name, because its
    own settings are already ``LIFERAY_``-prefixed.

    Split out of ``get_host_passthrough_env`` so the composer can inject
    **only** these, without the global passthrough pool that function also
    returns. That distinction is the whole point: a targeted variable is an
    explicit act naming one service, so delivering it surprises nobody. The
    global pool is implicit, and quietly starting to inject it into every
    client-extension container of every existing project would be a behaviour
    change nobody asked for -- see the PR for why that half was left alone.

    The blacklist still applies. Note it excludes ``COM_LIFERAY_LXC_DXP_*``
    and ``LIFERAY_ROUTES_*`` as LDM-managed, so those cannot be forwarded by
    any route, targeted or not.

    LDM-#1954: that last sentence was false for four releases. The blacklist
    was tested against the host spelling while the container received the
    *stripped* name, and the two differ by the service prefix for every target
    but Liferay -- so a pattern anchored at the START of the name never
    matched. ``SERVICEID_LIFERAY_ROUTES_CLIENT_EXTENSION`` sailed past
    ``LIFERAY_ROUTES_*`` and arrived as ``LIFERAY_ROUTES_CLIENT_EXTENSION``,
    repointing a tree LDM manages. Suffix patterns (``*_API_KEY``,
    ``*_OAUTH2_*_CLIENT_SECRET``) were never affected, because a prefix does
    not disturb a suffix match -- which is why the credential half of
    LDM-#1910 held and this went unnoticed. The branch itself was unreachable
    until LDM-#1903 made it callable.
    """
    if blacklist is None:
        blacklist = _get_effective_blacklist(self, paths)
    prefix = target_id.upper().replace("-", "_") + "_"
    keeps_full_name = target_id.lower() == "liferay"

    delivered = {}
    for key, value in os.environ.items():
        if not key.upper().startswith(prefix):
            continue
        # Test the name the container will actually RECEIVE. The host spelling
        # is tested too, so a blacklist entry written against either form
        # still bites; checking only one is exactly how LDM-#1954 happened.
        name = key if keeps_full_name else key[len(prefix) :]
        if is_env_var_blacklisted(key, blacklist) or is_env_var_blacklisted(
            name, blacklist
        ):
            continue
        delivered[name] = value
    return delivered
