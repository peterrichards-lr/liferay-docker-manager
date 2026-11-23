"""The `--samples` pack: its manifest, its cache and its checksum.

LDM-#2492: `get_samples_tag` / `get_samples_db_type` read `tag=` lines from a
`meta` the extracted samples never contain -- `download_samples` extracts only
the inner files.tar.gz, and a package manifest is JSON anyway -- so both always
returned None, and the None db_type overrode the user's `~/.ldmrc` default.

LDM-#2491: `~/.ldm/references/samples/samples_latest.ldmp` was reused forever,
so a new samples release never reached anyone who had run `--samples` once,
and the published `.sha256` was never checked.

LDM-#2493: a pack that declares itself stateless has no snapshot to restore.

The pack here is a real `.ldmp` built the way ldm-cx-samples builds its
release (`tar -czf ... -C staging .`, so members are `./meta` and
`./files.tar.gz`), and it goes through the real download and extraction. Only
HTTP is replaced -- no test reaches the network.
"""

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from ldm_core.handlers.assets import AssetService, read_samples_package
from ldm_core.handlers.config import ConfigService

REPO = "https://github.com/peterrichards-lr/ldm-cx-samples"


def _tar_bytes(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def make_pack(manifest, files):
    """An .ldmp as ldm-cx-samples' package-ldmp.sh lays it out."""
    return _tar_bytes(
        {
            "./meta": json.dumps(manifest, indent=4).encode(),
            "./files.tar.gz": _tar_bytes(files),
            "./database.sql": b"",
        }
    )


V1 = make_pack(
    {"tag": "2026.q1.4-lts", "db_type": "mysql", "includes_database": False},
    {"client-extensions/old-cx.zip": b"old"},
)
V2 = make_pack(
    {"tag": "2026.q2.0-lts", "db_type": "postgresql", "package_kind": "stateless"},
    {"client-extensions/new-cx.zip": b"new"},
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class FakeGitHub:
    """`requests.head` / `requests.get` for the samples repo's releases."""

    def __init__(self):
        self.latest: str | None = None  # None: offline
        self.packs: dict[str, bytes] = {}
        self.sha_text: dict[str, str] = {}
        self.gets: list[str] = []
        self.heads = 0

    def head(self, url, **_kw):
        self.heads += 1
        if self.latest is None:
            raise requests.ConnectionError("offline")
        assert url == f"{REPO}/releases/latest", url
        res = MagicMock(status_code=302)
        res.headers = {"Location": f"{REPO}/releases/tag/{self.latest}"}
        return res

    def get(self, url, **_kw):
        self.gets.append(url)
        if self.latest is None:
            raise requests.ConnectionError("offline")
        tag = url.split("/releases/download/")[-1].split("/")[0]
        res = MagicMock(status_code=200)
        if url.endswith(".sha256"):
            if tag not in self.sha_text:
                res.status_code = 404
            res.text = self.sha_text.get(tag, "")
            return res
        data = self.packs[tag]
        res.iter_content = lambda **_kw: [data]
        return res

    def publish(self, tag, data, sha_text=None):
        self.packs[tag] = data
        # Bare digest, as the ldm-cx-samples release publishes it.
        self.sha_text[tag] = sha_text if sha_text is not None else _sha(data) + "\n"
        self.latest = tag


class _SamplesHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = patch.dict(os.environ, {"LDM_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.github = FakeGitHub()
        for target in (
            patch("ldm_core.handlers.assets.get_actual_home", return_value=self.home),
            patch("ldm_core.handlers.config.get_actual_home", return_value=self.home),
            patch("requests.head", side_effect=self.github.head),
            patch("requests.get", side_effect=self.github.get),
        ):
            target.start()
            self.addCleanup(target.stop)
        self.cache = self.home / ".ldm" / "references" / "samples"
        self.dest = self.cache / "v9.9.9"
        self.assets = AssetService(MagicMock())

    def download(self):
        return self.assets.download_samples("9.9.9", self.dest)


class TheManifestIsReadAsJson(_SamplesHome):
    def config_for(self, root):
        manager = MagicMock()
        manager.assets = self.assets
        config = ConfigService(manager)
        return config, patch.object(
            ConfigService, "get_samples_root", return_value=root
        )

    def test_tag_and_db_type_come_from_the_package_manifest(self):
        self.github.publish("v1.3.3", V1)
        self.assertTrue(self.download())
        config, root = self.config_for(self.dest)
        with root:
            self.assertEqual(config.get_samples_tag(), "2026.q1.4-lts")
            self.assertEqual(config.get_samples_db_type(), "mysql")

    def test_the_manifest_is_kept_beside_the_samples_not_in_them(self):
        """sync_samples copies the whole samples root into the project."""
        self.github.publish("v1.3.3", V1)
        self.download()
        self.assertEqual(
            sorted(p.name for p in self.dest.iterdir()), ["client-extensions"]
        )

    def test_a_pack_that_declares_nothing_yields_none(self):
        self.github.publish("v1", make_pack({}, {"deploy/x.zip": b"x"}))
        self.download()
        config, root = self.config_for(self.dest)
        with root:
            self.assertIsNone(config.get_samples_tag())
            self.assertIsNone(config.get_samples_db_type())
            self.assertFalse(config.samples_are_stateless())

    def test_a_stateless_pack_is_recognised(self):
        self.github.publish("v2", V2)
        self.download()
        config, root = self.config_for(self.dest)
        with root:
            self.assertTrue(config.samples_are_stateless())


class AMissingSamplesDbTypeKeepsTheDefault(unittest.TestCase):
    """LDM-#2492: None from the pack must not erase the resolved default."""

    def test_the_users_default_survives(self):
        from argparse import Namespace

        from ldm_core.pipelines.run import ConfigResolutionStage

        manager = MagicMock()
        manager.args = Namespace(db=None, database_mode=None)
        manager.defaults = {"db_type": "mysql"}
        manager.config.get_samples_db_type.return_value = None
        db_type, _mode = ConfigResolutionStage()._resolve_database(
            manager, {}, is_samples=True
        )
        self.assertEqual(db_type, "mysql")

    def test_a_declared_db_type_still_wins(self):
        from argparse import Namespace

        from ldm_core.pipelines.run import ConfigResolutionStage

        manager = MagicMock()
        manager.args = Namespace(db=None, database_mode=None)
        manager.defaults = {"db_type": "mysql"}
        manager.config.get_samples_db_type.return_value = "postgresql"
        db_type, _mode = ConfigResolutionStage()._resolve_database(
            manager, {}, is_samples=True
        )
        self.assertEqual(db_type, "postgresql")


class TheCacheFollowsTheLatestRelease(_SamplesHome):
    def test_a_moved_release_is_downloaded_and_replaces_the_old_one(self):
        self.github.publish("v1.3.3", V1)
        self.download()
        self.github.publish("v1.3.4", V2)
        self.assertTrue(self.download())

        self.assertIn(
            f"{REPO}/releases/download/v1.3.4/ldm-cx-samples.ldmp", self.github.gets
        )
        self.assertTrue((self.dest / "client-extensions" / "new-cx.zip").is_file())
        self.assertFalse(
            (self.dest / "client-extensions" / "old-cx.zip").exists(),
            "the previous release's files were left behind",
        )
        self.assertEqual(read_samples_package(self.dest)["release_tag"], "v1.3.4")

    def test_an_unmoved_release_is_not_downloaded_again(self):
        self.github.publish("v1.3.3", V1)
        self.download()
        before = list(self.github.gets)
        self.assertTrue(self.download())
        self.assertEqual(self.github.gets, before)

    def test_offline_the_cache_is_used_as_it_is(self):
        self.github.publish("v1.3.3", V1)
        self.download()
        self.github.latest = None
        before = list(self.github.gets)
        self.assertTrue(self.download())
        self.assertEqual(self.github.gets, before)
        self.assertTrue((self.dest / "client-extensions" / "old-cx.zip").is_file())

    def test_offline_with_no_cache_fails(self):
        self.assertFalse(self.download())


class GetSamplesRootRefreshes(_SamplesHome):
    """`get_samples_root` returned an extracted pack forever (LDM-#2491)."""

    def setUp(self):
        super().setUp()
        self.github.publish("v1.3.3", V1)
        self.download()
        self.manager = MagicMock()
        self.manager.assets = self.assets
        self.config = ConfigService(self.manager)
        version = patch("ldm_core.constants.VERSION", "9.9.9")
        version.start()
        self.addCleanup(version.stop)
        env = patch.dict(os.environ, {"LDM_SAMPLES_PATH": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_a_new_release_reaches_an_existing_cache(self):
        self.github.publish("v1.3.4", V2)
        self.assertEqual(self.config.get_samples_root(), self.dest)
        self.assertEqual(self.config.get_samples_tag(), "2026.q2.0-lts")
        self.assertTrue((self.dest / "client-extensions" / "new-cx.zip").is_file())

    def test_one_lookup_per_command(self):
        """The tag, the database and the sync each ask for the root."""
        self.github.heads = 0
        self.config.get_samples_tag()
        self.config.get_samples_db_type()
        self.config.get_samples_root()
        self.assertEqual(self.github.heads, 1)

    def test_offline_the_extracted_pack_is_kept(self):
        self.github.latest = None
        self.assertEqual(self.config.get_samples_root(), self.dest)
        self.assertEqual(self.config.get_samples_tag(), "2026.q1.4-lts")

    def test_a_pack_extracted_before_the_fix_is_refreshed(self):
        """No recorded release: an older LDM extracted it. It is re-extracted
        so its manifest is recorded -- from the cached pack, which is already
        the latest release, so nothing is downloaded again."""
        (self.dest.with_name(f"{self.dest.name}.package.json")).unlink()
        before = list(self.github.gets)
        self.config.get_samples_root()
        self.assertEqual(self.github.gets, before)
        self.assertEqual(read_samples_package(self.dest).get("release_tag"), "v1.3.3")
        self.assertEqual(self.config.get_samples_tag(), "2026.q1.4-lts")


class TheDownloadIsVerified(_SamplesHome):
    def assert_verified(self):
        """Accepted AND checked: an unreadable checksum is also accepted, with
        a warning, so success alone does not show the digest was compared."""
        with patch("ldm_core.ui.UI.warning") as warning:
            self.assertTrue(self.download())
        unverified = [c for c in warning.call_args_list if "not verified" in str(c)]
        self.assertEqual(unverified, [], "the published checksum was not read")

    def test_a_bare_digest_verifies(self):
        self.github.publish("v1", V1, sha_text=_sha(V1))
        self.assert_verified()

    def test_sha256sum_output_verifies(self):
        self.github.publish("v1", V1, sha_text=f"{_sha(V1)}  ldm-cx-samples.ldmp\n")
        self.assert_verified()

    def test_an_uppercase_digest_verifies(self):
        self.github.publish("v1", V1, sha_text=_sha(V1).upper())
        self.assert_verified()

    def test_no_published_checksum_is_said(self):
        self.github.publish("v1", V1)
        del self.github.sha_text["v1"]
        with patch("ldm_core.ui.UI.warning") as warning:
            self.assertTrue(self.download())
        self.assertIn("not verified", str(warning.call_args_list))

    def test_a_mismatch_is_refused(self):
        self.github.publish("v1", V1, sha_text="0" * 64)
        with patch("ldm_core.ui.UI.error") as error:
            self.assertFalse(self.download())
        self.assertIn("Integrity check failed", str(error.call_args_list[0]))
        self.assertFalse((self.cache / "samples_latest.ldmp").exists())
        self.assertFalse(self.dest.exists())

    def test_a_mismatch_keeps_the_previous_cache(self):
        self.github.publish("v1", V1)
        self.download()
        self.github.publish("v2", V2, sha_text="0" * 64)
        self.assertTrue(self.download())
        self.assertTrue((self.dest / "client-extensions" / "old-cx.zip").is_file())
        self.assertEqual(read_samples_package(self.dest)["release_tag"], "v1")


class AStatelessSamplesPackIsNotRestored(unittest.TestCase):
    """The real `ExecutionStage`: `--samples` restored `snapshots/` index 1."""

    def execute(self, stateless):
        from ldm_core.pipelines.run import ExecutionStage, RunPipelineContext

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            root.mkdir()
            manager = MagicMock()
            manager.non_interactive = True
            manager.args.no_up = True
            manager.args.no_wait = True
            manager.config.samples_are_stateless.return_value = stateless
            context = RunPipelineContext(manager)
            context.set("project_id", "proj")
            context.set("project_meta", {})
            context.set("paths", {"root": root, "deploy": root / "deploy"})
            context.set("no_up", True)
            context.set("no_wait", True)
            context.set("is_samples", True)
            with patch("ldm_core.pipelines.run.time.sleep"):
                ExecutionStage().execute(context)
        return manager.snapshot.cmd_restore

    def test_a_stateless_pack_restores_nothing(self):
        self.execute(stateless=True).assert_not_called()

    def test_a_pack_that_is_not_stateless_restores_as_before(self):
        restore = self.execute(stateless=False)
        restore.assert_called_once()
        self.assertEqual(restore.call_args.kwargs.get("auto_index"), 1)


if __name__ == "__main__":
    unittest.main()
