import json
import zipfile
from pathlib import Path

import yaml

from ldm_core.utils import UI


class ClientExtensionAnalyzer:
    """Pre-Flight analyzer for validating the integrity of Client Extension deployment artifacts."""

    @staticmethod
    def analyze_workspace(workspace_root: Path) -> bool:
        """
        Scans all .zip files in the deploy/ or client-extensions/ directories and validates them.
        Returns True if all checks pass, False if there are critical errors.
        """
        success = True

        # Check standard deployment folders
        deploy_dir = workspace_root / "deploy"
        cx_dir = workspace_root / "client-extensions"

        zip_files: list[Path] = []
        if deploy_dir.exists():
            zip_files.extend(deploy_dir.glob("*.zip"))
        if cx_dir.exists():
            zip_files.extend(cx_dir.glob("*.zip"))

        if not zip_files:
            return True

        for zf_path in set(zip_files):
            try:
                with zipfile.ZipFile(zf_path, "r") as zf:
                    if not ClientExtensionAnalyzer._analyze_zip(zf, zf_path.name):
                        success = False
            except zipfile.BadZipFile:
                UI.warning(
                    f"Validation Analyzer: '{zf_path.name}' is not a valid zip archive."
                )

        return success

    @staticmethod
    def validate_lcp_json_structure(data: dict) -> list[str]:
        """Validates the structure and content of an LCP.json file. Returns a list of errors."""
        errors = []

        # 1. Mandatory ID
        if not data.get("id"):
            errors.append("Missing mandatory 'id' field.")

        # 2. Port Validation
        ports = data.get("ports", [])
        if not isinstance(ports, list):
            errors.append("'ports' must be an array.")
        else:
            for i, p in enumerate(ports):
                if not isinstance(p, dict):
                    errors.append(f"Port at index {i} must be an object.")
                    continue
                if not p.get("port") and not p.get("targetPort"):
                    errors.append(f"Port at index {i} missing 'port' or 'targetPort'.")

        # 3. Load Balancer / External Port Consistency
        has_lb = "loadBalancer" in data
        has_external_port = any(p.get("external") for p in ports if isinstance(p, dict))

        if has_lb and not has_external_port:
            errors.append(
                "loadBalancer defined but no ports are marked as 'external: true'."
            )

        # 4. Resource Limits
        for res in ["cpu", "memory"]:
            val = data.get(res)
            if val is not None and not isinstance(val, (int, float)):
                errors.append(f"'{res}' must be a numeric value.")

        return errors

    @staticmethod
    def _analyze_zip(zf: zipfile.ZipFile, filename: str) -> bool:
        """Analyzes a single zip file for LCP.json and Dockerfile integrity."""
        success = True
        namelist = zf.namelist()

        # 1. Parse LCP.json if it exists
        lcp_kind = None
        lcp_data = None
        if "LCP.json" in namelist:
            try:
                lcp_content = zf.read("LCP.json").decode("utf-8")
                lcp_data = json.loads(lcp_content)
                lcp_kind = lcp_data.get("kind")
            except Exception as e:
                UI.error(
                    f"Validation Analyzer: Failed to parse LCP.json in '{filename}': {e}"
                )
                success = False

        # 2. Check for missing Dockerfile when kind is Deployment
        if lcp_kind == "Deployment":
            if "Dockerfile" not in namelist:
                UI.error(
                    f"Validation Analyzer: CRITICAL gap in '{filename}'. 'LCP.json' declares kind='Deployment', but no 'Dockerfile' was found in the archive root. It will fail to containerize."
                )
                success = False

        # 2.5 LCP.json Structural Integrity Check
        if lcp_data:
            lcp_errors = ClientExtensionAnalyzer.validate_lcp_json_structure(lcp_data)
            for err in lcp_errors:
                UI.error(f"Validation Analyzer: LCP.json Issue in '{filename}': {err}")
                success = False

        # 3. Parse client-extension.yaml if it exists
        if "client-extension.yaml" in namelist:
            try:
                cx_content = zf.read("client-extension.yaml").decode("utf-8")
                cx_data = yaml.safe_load(cx_content) or {}

                # Check for hacks or unsupported configurations
                for ext_id, ext_config in cx_data.items():
                    if isinstance(ext_config, dict):
                        cx_type = ext_config.get("type", "")
                        if cx_type == "microservice":
                            UI.error(
                                f"Validation Analyzer: UNSUPPORTED CONFIG in '{filename}'. '{ext_id}' uses 'type: microservice' which is not officially supported by Liferay. Use a standard type (e.g. 'customElement') and define a proper 'LCP.json'."
                            )
                            success = False

                # Check if it needs an LCP.json (heuristic: if it's a type that requires external hosting but has no LCP.json)
                if not lcp_kind:
                    # In a real environment, any extension that requires routing needs an LCP.json, but for now we only warn if it explicitly looks like it's missing one.
                    pass

            except Exception as e:
                UI.error(
                    f"Validation Analyzer: Failed to parse client-extension.yaml in '{filename}': {e}"
                )
                success = False

        return success
