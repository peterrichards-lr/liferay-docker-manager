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
        "env": {},
        "has_load_balancer": False,
    }

    def merge_info(new_info):
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

    if zip_ref:
        for f in zip_ref.namelist():
            name = Path(f).name
            if name in ["client-extension.yaml", "client-extension.yml"]:
                merge_info(
                    self._parse_client_extension_yaml(zip_ref.read(f).decode("utf-8"))
                )
            elif name == "LCP.json":
                ctx = Path(zip_ref.filename).name
                merge_info(
                    self._parse_lcp_json(
                        zip_ref.read(f).decode("utf-8"), context_name=ctx
                    )
                )
            elif name == "client-extension-config.json" or f.endswith(
                ".client-extension-config.json"
            ):
                erc = self._parse_client_extension_config_json(
                    zip_ref.read(f).decode("utf-8")
                )
                if erc:
                    info["oauth_erc"] = erc
    elif folder_path:
        yaml_file = next(folder_path.glob("client-extension.y*ml"), None)
        if yaml_file:
            merge_info(self._parse_client_extension_yaml(yaml_file.read_text()))
        lcp_file = folder_path / "LCP.json"
        if lcp_file.exists():
            merge_info(
                self._parse_lcp_json(
                    lcp_file.read_text(), context_name=folder_path.name
                )
            )
        cfg_file = next(folder_path.glob("*client-extension-config.json"), None)
        if cfg_file:
            erc = self._parse_client_extension_config_json(cfg_file.read_text())
            if erc:
                info["oauth_erc"] = erc
    return info


def scan_client_extensions(self, root_dir, osgi_cx_dir, ce_build_dir, host_name=None):
    extensions: list[dict[str, Any]] = []
    if not root_dir.exists():
        return extensions
    meta = self.manager.read_meta(root_dir) or {}

    # Paths based on Core Mandates:
    # root_dir / client-extensions -> Build Source of Truth
    # root_dir / osgi / client-extensions -> Liferay Auto-deploy (ZIPs)
    ce_source_truth = root_dir / "client-extensions"
    ce_source_truth.mkdir(parents=True, exist_ok=True)
    osgi_cx_dir.mkdir(parents=True, exist_ok=True)

    found_ids = set()

    # 1. Process ZIPs from the workspace build directory (ldm-cx-samples/client-extensions/*/dist/*.zip)
    for item in [i for i in ce_build_dir.iterdir() if i.suffix.lower() == ".zip"]:
        try:
            # Root project folder is the build context (Source of Truth)
            target_folder = ce_source_truth / item.stem
            root_zip_copy = ce_source_truth / item.name

            # Copy to root first to expand (if not already there)
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
                ext_info = self._scan_extension_metadata(zip_ref=zip_ref)
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

                    # Move the original ZIP from root to OSGI for Liferay's scanner
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

                    # Resolve and persist port first
                    ext_id = ext_info.get("id") or item.stem
                    if is_service:
                        load_balancer = ext_info.get("loadBalancer") or {}
                        default_port = next(
                            (
                                p.get("port")
                                for p in ext_info.get("ports", [])
                                if isinstance(p, dict)
                            ),
                            load_balancer.get("targetPort", 8080),
                        )
                        try:
                            default_port = int(default_port)
                        except (ValueError, TypeError):
                            default_port = 8080

                        meta_port_key = f"port_{ext_id}"
                        if meta_port_key not in meta:
                            resolved_port = self.manager.find_available_port(
                                "127.0.0.1", default_port
                            )
                            meta[meta_port_key] = str(resolved_port)
                            self.manager.write_meta(root_dir, meta)

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
                            "path": target_folder,  # Build from root/client-extensions/
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

    # 2. Process existing folders in the Source of Truth
    for item in [
        i
        for i in ce_source_truth.iterdir()
        if i.is_dir() and not i.name.startswith(".")
    ]:
        ext_info = self._scan_extension_metadata(folder_path=item)
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
                "path": item,  # Already in source of truth
                "is_service": is_service,
                **ext_info,
            }
            # Resolve and persist port first
            ext_id = ext_info.get("id") or item.name
            if is_service:
                load_balancer = ext_info.get("loadBalancer") or {}
                default_port = next(
                    (
                        p.get("port")
                        for p in ext_info.get("ports", [])
                        if isinstance(p, dict)
                    ),
                    load_balancer.get("targetPort", 8080),
                )
                try:
                    default_port = int(default_port)
                except (ValueError, TypeError):
                    default_port = 8080

                meta_port_key = f"port_{ext_id}"
                if meta_port_key not in meta:
                    resolved_port = self.manager.find_available_port(
                        "127.0.0.1", default_port
                    )
                    meta[meta_port_key] = str(resolved_port)
                    self.manager.write_meta(root_dir, meta)

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
            ext_info = self._scan_extension_metadata(folder_path=item)
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


def get_host_passthrough_env(self, paths=None, target_id=None):
    blacklist = self._get_effective_blacklist(paths)

    # 1. Global Strip-Forwarding (LDM_VAR=xxx -> VAR=xxx in ALL containers)
    global_pool = {
        k[4:]: v
        for k, v in os.environ.items()
        if k.upper().startswith("LDM_") and not is_env_var_blacklisted(k, blacklist)
    }

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

    global_pool.update(
        {
            k: v
            for k, v in os.environ.items()
            if any(k.upper().startswith(p.upper()) for p in passthrough_prefixes)
            and not is_env_var_blacklisted(k, blacklist)
        }
    )

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
    prefix = target_id.upper().replace("-", "_") + "_"
    targeted = {
        k[len(prefix) :] if target_id.lower() != "liferay" else k: v
        for k, v in os.environ.items()
        if k.upper().startswith(prefix) and not is_env_var_blacklisted(k, blacklist)
    }
    return [f"{k}={v}" for k, v in {**global_pool, **targeted}.items()]
