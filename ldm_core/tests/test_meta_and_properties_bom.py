"""A project meta or portal-ext.properties with a UTF-8 BOM is read (LDM-#2287).

The follow-on to LDM-#2281 for the two other files a person (or the PowerShell
suite) writes by hand. Found on Windows PowerShell 5.1 during v2.26.7-pre.4
verification: the suite wrote the smoke-test project's meta with
`Out-File -Encoding utf8`, LDM read it as no metadata at all, and the project
ran nameless -- every volume came out without its ownership labels.
"""

import json
import tempfile
import unittest
from pathlib import Path

BOM = b"\xef\xbb\xbf"


class AMetaWithABomIsRead(unittest.TestCase):
    def test_json_meta(self):
        from ldm_core.utils import read_meta

        with tempfile.TemporaryDirectory() as td:
            meta = Path(td) / "meta"
            meta.write_bytes(
                BOM + json.dumps({"container_name": "bom-project"}).encode()
            )
            self.assertEqual(read_meta(meta).get("container_name"), "bom-project")

    def test_flat_meta(self):
        from ldm_core.utils import read_meta

        with tempfile.TemporaryDirectory() as td:
            meta = Path(td) / "meta"
            meta.write_bytes(BOM + b"container_name=bom-project\nport=9123\n")
            self.assertEqual(read_meta(meta).get("container_name"), "bom-project")


class APortalExtWithABomKeepsItsFirstProperty(unittest.TestCase):
    def test_update_keeps_the_first_key(self):
        """The BOM fused onto the first key's name, so updating any other key
        rewrote the file with that property under a corrupted name."""
        from ldm_core.handlers.config import ConfigService
        from ldm_core.tests.test_workspace import MockWorkspaceManager

        with tempfile.TemporaryDirectory() as td:
            pe = Path(td) / "portal-ext.properties"
            pe.write_bytes(BOM + b"test.override.prop=456\nother.prop=1\n")
            ConfigService(MockWorkspaceManager()).update_portal_ext(
                pe, {"other.prop": "2"}
            )
            props = dict(
                line.split("=", 1)
                for line in pe.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            self.assertEqual(props.get("test.override.prop"), "456")
            self.assertNotIn("﻿test.override.prop", props)


if __name__ == "__main__":
    unittest.main()
