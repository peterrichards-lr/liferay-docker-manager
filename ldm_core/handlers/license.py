class LicenseHandler:
    """Mixin for Liferay license detection and parsing."""

    def find_license(self, paths):
        """
        Locates Liferay license XML files in common, deploy, and osgi/modules folders.
        Returns a list of dictionaries with location and parsed info.
        """
        licenses = []

        # 1. Check Global Common
        common_dir = paths.get("common")
        if common_dir and common_dir.exists():
            for f in common_dir.glob("*.xml"):
                info = self._parse_license_xml(f)
                if info:
                    info["location"] = "Global (common/)"
                    info["path"] = f
                    licenses.append(info)

        # 2. Check Project Deploy
        deploy_dir = paths.get("deploy")
        if deploy_dir and deploy_dir.exists():
            for f in deploy_dir.glob("*.xml"):
                info = self._parse_license_xml(f)
                if info:
                    info["location"] = "Project (deploy/)"
                    info["path"] = f
                    licenses.append(info)

        # 3. Check Project OSGi Modules (where Liferay moves processed licenses)
        modules_dir = paths.get("modules")
        if modules_dir and modules_dir.exists():
            for f in modules_dir.glob("*.xml"):
                info = self._parse_license_xml(f)
                if info:
                    info["location"] = "Project (osgi/modules/)"
                    info["path"] = f
                    licenses.append(info)

        return licenses

    def _parse_license_xml(self, file_path):
        """
        Parses a Liferay XML license file and extracts key fields.
        Returns None if the file is not a valid Liferay license.
        Uses regex for safety instead of a full XML parser to avoid XXE.
        """
        try:
            content = file_path.read_text()
            if "<license" not in content.lower():
                return None

            import re

            def get_tag(tag, text):
                # Standard tags: <tag-name>Value</tag-name> or <tagname>Value</tagname>
                pattern = rf"<{tag}>(.*?)</{tag}>"
                match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                return match.group(1).strip() if match else None

            # Liferay DXP/EE license tags
            info = {
                "product": get_tag("product-name", content)
                or get_tag("product", content),
                "owner": get_tag("owner", content),
                "account": get_tag("account-name", content),
                "expiration": get_tag("expiration-date", content),
                "type": get_tag("license-type", content),
                "version": get_tag("product-version", content)
                or get_tag("version", content),
                "max_users": get_tag("max-users", content),
                "description": get_tag("description", content),
            }

            # If we couldn't find a product name, it might not be a license we recognize
            if not info["product"]:
                return None

            return info
        except Exception:
            return None

    def check_license_health(self, paths, image_tag=None):
        """
        Higher-level check for doctor and run commands.
        Returns (status_text, is_ok, details_list)
        """
        # If it's explicitly a Portal (CE) image, we don't strictly require an XML license.
        if image_tag and "portal" in image_tag.lower():
            return "Not Required (Portal CE)", True, []

        found = self.find_license(paths)
        if not found:
            return (
                "Missing",
                "warn",
                [
                    "No Liferay license (.xml) found in common/, deploy/, or osgi/modules/"
                ],
            )

        # If multiple found, summarize
        main_license = found[0]
        details = []
        for license_item in found:
            details.append(
                f"Found {license_item['product']} license in {license_item['location']} (Expires: {license_item['expiration'] or 'Never'})"
            )

        status = f"Present ({main_license['product']})"
        if main_license.get("expiration"):
            status += f" - Expires: {main_license['expiration']}"

        return status, True, details
