import unittest
from pathlib import Path
from ldm_core.handlers.license import LicenseHandler


class MockManager(LicenseHandler):
    def __init__(self):
        self.verbose = False


class TestLicenseHandler(unittest.TestCase):
    def setUp(self):
        self.manager = MockManager()
        self.test_dir = Path("/tmp/ldm_license_test")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.common_dir = self.test_dir / "common"
        self.deploy_dir = self.test_dir / "deploy"
        self.modules_dir = self.test_dir / "osgi" / "modules"

        for d in [self.common_dir, self.deploy_dir, self.modules_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self.paths = {
            "common": self.common_dir,
            "deploy": self.deploy_dir,
            "modules": self.modules_dir,
        }

    def tearDown(self):
        import shutil

        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_parse_valid_license(self):
        license_xml = """<?xml version="1.0"?>
<license>
    <product-name>Liferay DXP</product-name>
    <owner>Test User</owner>
    <expiration-date>2027-01-01</expiration-date>
    <license-type>Production</license-type>
    <version>7.4</version>
    <max-users>100</max-users>
</license>
"""
        license_file = self.common_dir / "license.xml"
        license_file.write_text(license_xml)

        info = self.manager._parse_license_xml(license_file)
        self.assertIsNotNone(info)
        self.assertEqual(info["product"], "Liferay DXP")
        self.assertEqual(info["owner"], "Test User")
        self.assertEqual(info["expiration"], "2027-01-01")

    def test_parse_invalid_xml(self):
        not_license_xml = """<?xml version="1.0"?>
<note>
    <to>Tove</to>
    <from>Jani</from>
</note>
"""
        license_file = self.common_dir / "note.xml"
        license_file.write_text(not_license_xml)

        info = self.manager._parse_license_xml(license_file)
        self.assertIsNone(info)

    def test_find_licenses(self):
        # Create one in common
        (self.common_dir / "global.xml").write_text(
            "<license><product-name>Global DXP</product-name></license>"
        )
        # Create one in deploy
        (self.deploy_dir / "project.xml").write_text(
            "<license><product-name>Project DXP</product-name></license>"
        )

        found = self.manager.find_license(self.paths)
        self.assertEqual(len(found), 2)

        locations = [lic["location"] for lic in found]
        self.assertIn("Global (common/)", locations)
        self.assertIn("Project (deploy/)", locations)

    def test_check_license_health_missing(self):
        status, ok, details = self.manager.check_license_health(
            self.paths, image_tag="liferay/dxp:latest"
        )
        self.assertEqual(status, "Missing")
        self.assertEqual(ok, "warn")
        self.assertTrue(any("No Liferay license" in d for d in details))

    def test_check_license_health_portal_ce(self):
        # Portal CE should be OK even if missing
        status, ok, details = self.manager.check_license_health(
            self.paths, image_tag="liferay/portal:7.4.3.112"
        )
        self.assertEqual(status, "Not Required (Portal CE)")
        self.assertTrue(ok)

    def test_check_license_health_present(self):
        (self.common_dir / "license.xml").write_text(
            "<license><product-name>Liferay DXP</product-name><expiration-date>2027-01-01</expiration-date></license>"
        )

        status, ok, details = self.manager.check_license_health(
            self.paths, image_tag="liferay/dxp:latest"
        )
        self.assertTrue(ok)
        self.assertIn("Present (Liferay DXP)", status)
        self.assertIn("Expires: 2027-01-01", status)


if __name__ == "__main__":
    unittest.main()
