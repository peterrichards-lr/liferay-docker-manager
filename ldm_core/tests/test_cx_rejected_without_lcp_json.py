"""LDM-#1962: a client extension without an `LCP.json` id is not deployed.

Every real client extension carries an `LCP.json` -- it is what Liferay Cloud
deploys from, and every sample in `ldm-cx-samples` has one alongside its
`client-extension.yaml`. So this is not a shape to support; it is one to
refuse.

**LDM must not invent the missing id.** Liferay names the config tree it
publishes from the extension's `projectName`, which routinely differs from the
directory (`ecopulse-headless-auth` declares `ecopulseheadlessauth`). Guessing
from the folder binds a tree Liferay never writes to -- which is LDM-#1944,
arrived at deliberately instead of by accident.

**And refusing is not enough on its own.** `osgi/client-extensions` is a bind
mount Liferay reads from, so an artifact left there is deployed whatever LDM
thinks of it. The zip is moved out, never deleted.
"""

import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ldm_core.workspace.site_initializers import (
    cx_service_missing_lcp_id,
    rejected_dir,
)


def _zip(path, *, dockerfile=True, lcp=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("client-extension.yaml", "svc:\n    type: microservice\n")
        if dockerfile:
            z.writestr("Dockerfile", "FROM alpine\n")
        if lcp is not None:
            z.writestr("LCP.json", json.dumps(lcp))
    return path


class TestWhichArtifactsAreRefused(unittest.TestCase):
    def test_a_service_without_lcp_json_is_refused(self):
        with TemporaryDirectory() as d:
            z = _zip(Path(d) / "svc.zip")
            self.assertTrue(cx_service_missing_lcp_id(z))

    def test_a_service_whose_lcp_json_declares_no_id_is_refused(self):
        with TemporaryDirectory() as d:
            z = _zip(Path(d) / "svc.zip", lcp={"memory": 512})
            self.assertTrue(cx_service_missing_lcp_id(z))

    def test_a_service_with_an_id_is_accepted(self):
        with TemporaryDirectory() as d:
            z = _zip(Path(d) / "svc.zip", lcp={"id": "svcid"})
            self.assertFalse(cx_service_missing_lcp_id(z))

    def test_a_static_extension_is_not_a_service_and_is_accepted(self):
        """No Dockerfile means no container, so no identity is needed here.
        Refusing these would block every static extension."""
        with TemporaryDirectory() as d:
            z = _zip(Path(d) / "static.zip", dockerfile=False)
            self.assertFalse(cx_service_missing_lcp_id(z))

    def test_a_job_is_not_a_service_and_is_accepted(self):
        """`is_service` is `Dockerfile AND kind != "Job"`, and this check must
        mirror it exactly.

        A real built site initializer ships BOTH a Dockerfile and
        `"kind": "Job"` -- verified against the ecopulse artifact. Keying on
        the Dockerfile alone refuses every site initializer, which is a far
        worse regression than the bug being fixed. It was caught only because
        the deferral suite failed.
        """
        with TemporaryDirectory() as d:
            z = _zip(Path(d) / "si.zip", lcp={"kind": "Job"})
            self.assertFalse(cx_service_missing_lcp_id(z))

    def test_a_job_that_does_declare_an_id_is_also_accepted(self):
        with TemporaryDirectory() as d:
            z = _zip(Path(d) / "si.zip", lcp={"id": "siid", "kind": "Job"})
            self.assertFalse(cx_service_missing_lcp_id(z))

    def test_an_unreadable_zip_is_not_refused(self):
        """Refusing an artifact we failed to identify would block valid
        deployments. `is_site_initializer_zip` takes the same view."""
        with TemporaryDirectory() as d:
            bad = Path(d) / "broken.zip"
            bad.write_bytes(b"not a zip")
            self.assertFalse(cx_service_missing_lcp_id(bad))


class TestTheArtifactIsMovedOutOfTheWay(unittest.TestCase):
    """Refusing without moving it would be advisory only -- Liferay reads
    `osgi/client-extensions` regardless of what LDM decided."""

    def _sync(self, root, zip_path):
        from ldm_core.workspace.hydration import _sync_cx_artifact

        paths = {"root": root, "cx": root / "osgi" / "client-extensions"}
        paths["cx"].mkdir(parents=True, exist_ok=True)
        with patch("ldm_core.ui.UI.error"):
            _sync_cx_artifact(MagicMock(), zip_path, paths)
        return paths

    def test_it_never_reaches_osgi_client_extensions(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            z = _zip(root / "svc.zip")
            paths = self._sync(root, z)
            self.assertFalse(
                (paths["cx"] / "svc.zip").exists(),
                "a refused artifact was deployed anyway -- Liferay reads this "
                "directory (LDM-#1962)",
            )

    def test_a_copy_already_deployed_is_moved_out(self):
        """The case that matters on an existing project: the zip is already
        where Liferay will read it."""
        with TemporaryDirectory() as d:
            root = Path(d)
            z = _zip(root / "svc.zip")
            (root / "osgi" / "client-extensions").mkdir(parents=True)
            deployed = root / "osgi" / "client-extensions" / "svc.zip"
            _zip(deployed)
            self._sync(root, z)
            self.assertFalse(deployed.exists(), "the deployed copy was left in place")
            self.assertTrue((rejected_dir(root) / "svc.zip").exists())

    def test_it_is_moved_not_deleted(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            z = _zip(root / "svc.zip")
            self._sync(root, z)
            self.assertTrue(
                (rejected_dir(root) / "svc.zip").exists(),
                "the artifact must be recoverable, not destroyed",
            )

    def test_the_user_is_told_where_it_went(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            z = _zip(root / "svc.zip")
            paths = {"root": root, "cx": root / "osgi" / "client-extensions"}
            paths["cx"].mkdir(parents=True, exist_ok=True)
            from ldm_core.workspace.hydration import _sync_cx_artifact

            with patch("ldm_core.ui.UI.error") as err:
                _sync_cx_artifact(MagicMock(), z, paths)
            said = (
                " ".join(str(a) for a in err.call_args[0] if a)
                + " "
                + " ".join(str(v) for v in err.call_args[1].values() if v)
            )
            self.assertIn("LCP.json", said, "the missing file must be named")
            self.assertIn(
                str(rejected_dir(root)), said, "the path it went to must be named"
            )

    def test_a_valid_service_is_still_deployed(self):
        """The control. A fix that simply refused more would pass every
        assertion above and break client extensions entirely."""
        with TemporaryDirectory() as d:
            root = Path(d)
            z = _zip(root / "good.zip", lcp={"id": "goodid"})
            paths = self._sync(root, z)
            self.assertTrue((paths["cx"] / "good.zip").exists())
            self.assertFalse((rejected_dir(root) / "good.zip").exists())


if __name__ == "__main__":
    unittest.main()
