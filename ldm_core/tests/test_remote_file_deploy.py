"""LDM-#1894: `ldm deploy <project> <file>` works against a remote node.

It used to refuse. The guard was right -- copying an artifact into the LOCAL
project directory would have updated something the remote container never
reads, silently -- but the capability behind it was missing, and the suggested
workaround (`ldm run` for a full resync) restarts the stack, which is no use
to a CI run that has already waited out a boot.

The command must behave the same whatever the target. The node comes from the
project's own `meta`, so a caller never names it, and the LOCAL end state must
not depend on where the project happens to run.

`osgi/modules` and `osgi/client-extensions` are bind-mounts from the project
directory, so placing a file in the node's copy is all a running container
needs: no container operation, no restart.
"""

import pathlib
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from ldm_core.runtime.orchestration import OrchestrationService


class _DeployBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.root = self.base / "proj"
        for sub in ("osgi/modules", "osgi/client-extensions", "client-extensions"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

        self.zip_art = self.base / "my-cx.zip"
        with zipfile.ZipFile(self.zip_art, "w") as z:
            z.writestr("client-extension.yaml", "x: 1\n")
        self.jar_art = self.base / "my-module.jar"
        self.jar_art.write_bytes(b"not really a jar")

        self.pushed = []
        self.chowned = []

    def _paths(self):
        r = self.root
        return {
            "root": r,
            "deploy": r / "deploy",
            "osgi": r / "osgi",
            "modules": r / "osgi" / "modules",
            "cx": r / "osgi" / "client-extensions",
            "ce_dir": r / "client-extensions",
        }

    def _deploy(self, artifact, remote, push_ok=True):
        mgr = MagicMock()
        mgr.detect_project_path.return_value = self.root
        mgr.read_meta.return_value = {
            "project_name": "proj",
            "liferay_container_name": "proj",
        }
        mgr.target = "aws-2" if remote else None
        mgr.setup_paths.return_value = self._paths()
        svc = OrchestrationService(mgr)

        tgt = MagicMock()
        tgt.name = "aws-2" if remote else "local"
        tgt.host = "16.0.0.1" if remote else "localhost"

        def fake_push(_t, _n, f, subdir):
            self.pushed.append((f.name, subdir))
            return push_ok

        with (
            patch("ldm_core.config.get_active_target", return_value=tgt),
            patch("ldm_core.config.push_artifact_to_target", side_effect=fake_push),
            patch(
                "ldm_core.config.fix_remote_artifact_ownership",
                side_effect=lambda *a, **_k: self.chowned.append(a[1]),
            ),
        ):
            try:
                svc.cmd_deploy("proj", targets=[str(artifact)])
            except SystemExit as exc:
                return exc.code
        return 0

    def _local_state(self):
        return (
            sorted(
                p.name for p in (self.root / "osgi" / "client-extensions").glob("*")
            ),
            sorted(p.name for p in (self.root / "osgi" / "modules").glob("*")),
            sorted(p.name for p in (self.root / "client-extensions").glob("*")),
        )


class TestTheCommandDoesNotCareWhereTheProjectRuns(_DeployBase):
    def test_a_remote_deploy_leaves_the_same_local_state_as_a_local_one(self):
        """The end state must not depend on the target (LDM-#1894)."""
        self._deploy(self.zip_art, remote=False)
        local_state = self._local_state()

        self.setUp()  # fresh tree
        self._deploy(self.zip_art, remote=True)
        remote_state = self._local_state()

        self.assertEqual(
            local_state,
            remote_state,
            "a remote deploy produced a different local project directory",
        )

    def test_the_expanded_client_extension_is_created_for_a_remote_target_too(self):
        """`ldm run` later rsyncs the whole project; it must not be missing this."""
        self._deploy(self.zip_art, remote=True)
        _cx, _mods, ce = self._local_state()
        self.assertIn("my-cx", ce)


class TestTheArtifactReachesTheNode(_DeployBase):
    def test_a_client_extension_is_shipped_to_the_bind_mounted_directory(self):
        self._deploy(self.zip_art, remote=True)
        self.assertIn(("my-cx.zip", "osgi/client-extensions"), self.pushed)

    def test_a_module_is_shipped_to_its_own_directory(self):
        self._deploy(self.jar_art, remote=True)
        self.assertIn(("my-module.jar", "osgi/modules"), self.pushed)

    def test_ownership_is_settled_inside_the_container(self):
        """Written over SSH it is owned by the SSH user; Liferay runs as liferay
        and ignores what it cannot read, silently."""
        self._deploy(self.zip_art, remote=True)
        self.assertEqual(["proj"], self.chowned)

    def test_nothing_is_shipped_for_a_local_target(self):
        self._deploy(self.zip_art, remote=False)
        self.assertEqual([], self.pushed)
        self.assertEqual([], self.chowned)


class TestFailureIsNotSilent(_DeployBase):
    def test_a_failed_push_fails_the_command_and_says_what_happened(self):
        """The old guard existed because a silent local-only copy is the worst
        outcome. That must remain true when the push itself fails."""
        code = self._deploy(self.zip_art, remote=True, push_ok=False)
        self.assertNotEqual(0, code, "a failed push must not report success")
