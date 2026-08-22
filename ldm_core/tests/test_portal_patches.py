"""Tests for the portal-patches overlay (LDM-#1264)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.base import BaseHandler
from ldm_core.runtime import portal_patches as pp

# The real parser, not a stand-in: the classifier's whole contract is about how
# real Liferay tags decompose, so a simplified fake would test nothing.
_PARSE = BaseHandler.__new__(BaseHandler).parse_version


class TestClassifyVersionChange(unittest.TestCase):
    def _tier(self, intro, current, max_version=None):
        return pp.classify_version_change(intro, current, _PARSE, max_version)[0]

    def test_identical_versions_are_ok(self):
        self.assertEqual(self._tier("2026.q1.12-lts", "2026.q1.12-lts"), pp.OK)

    def test_patch_bump_within_quarter_warns(self):
        self.assertEqual(self._tier("2026.q1.12-lts", "2026.q1.13-lts"), pp.WARN)

    def test_quarter_change_aborts(self):
        self.assertEqual(self._tier("2026.q1.12-lts", "2026.q2.1-lts"), pp.ABORT)

    def test_year_change_aborts(self):
        self.assertEqual(self._tier("2026.q4.1-lts", "2027.q1.1-lts"), pp.ABORT)

    def test_legacy_update_bump_warns(self):
        self.assertEqual(self._tier("7.4.13-u108", "7.4.13-u109"), pp.WARN)

    def test_legacy_minor_change_aborts(self):
        self.assertEqual(self._tier("7.4.13-u108", "7.5.13-u108"), pp.ABORT)

    def test_mixed_release_lines_abort(self):
        """Quarterly vs legacy cannot be compared positionally.

        Index 1 is the *quarter* in one scheme and the *minor* version in the
        other, so any positional rule would silently compare unrelated numbers.
        """
        self.assertEqual(self._tier("7.4.13-u108", "2026.q1.12-lts"), pp.ABORT)
        self.assertEqual(self._tier("2026.q1.12-lts", "7.4.13-u108"), pp.ABORT)

    def test_unparseable_tag_aborts_in_both_directions(self):
        """Guards the `() < (7, 4, 13)` trap.

        `parse_version("nightly")` returns an empty tuple, which compares as
        *older than everything* rather than raising -- so an unparseable tag
        would silently classify as a safe downgrade if parseability were
        checked after the comparison instead of before it.
        """
        self.assertEqual(self._tier("nightly", "2026.q1.12-lts"), pp.ABORT)
        self.assertEqual(self._tier("2026.q1.12-lts", "nightly"), pp.ABORT)
        self.assertEqual(self._tier("", "2026.q1.12-lts"), pp.ABORT)

    def test_max_version_ceiling_aborts_even_when_tier_would_pass(self):
        # Same release line and only a patch bump -- WARN on its own terms.
        self.assertEqual(self._tier("2026.q1.12-lts", "2026.q1.14-lts"), pp.WARN)
        # The declared ceiling overrides that.
        self.assertEqual(
            self._tier("2026.q1.12-lts", "2026.q1.14-lts", "2026.q1.13-lts"), pp.ABORT
        )

    def test_within_max_version_still_warns(self):
        self.assertEqual(
            self._tier("2026.q1.12-lts", "2026.q1.13-lts", "2026.q1.20-lts"), pp.WARN
        )

    def test_invalid_max_version_aborts(self):
        self.assertEqual(
            self._tier("2026.q1.12-lts", "2026.q1.13-lts", "not-a-version"), pp.ABORT
        )


class TestSidecar(unittest.TestCase):
    def test_sidecar_created_on_first_sight(self):
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "com.liferay.account.service.jar"
            jar.write_bytes(b"PK\x03\x04")
            manifest, created = pp.load_or_create_sidecar(jar, "2026.q1.12-lts")

            self.assertTrue(created)
            self.assertEqual(manifest["introduced_in"], "2026.q1.12-lts")
            self.assertTrue(pp.sidecar_path(jar).exists())

    def test_existing_sidecar_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "x.jar"
            jar.write_bytes(b"PK\x03\x04")
            pp.sidecar_path(jar).write_text(
                json.dumps({"introduced_in": "7.4.13-u108", "jira": "LPD-1"})
            )

            manifest, created = pp.load_or_create_sidecar(jar, "2026.q1.12-lts")

            self.assertFalse(created)
            self.assertEqual(manifest["introduced_in"], "7.4.13-u108")
            self.assertEqual(manifest["jira"], "LPD-1")

    def test_unreadable_sidecar_fails_closed(self):
        """A corrupt manifest must not be rewritten as "introduced now".

        Doing so would silently re-arm a stale patch as current, which is the
        exact failure this feature exists to prevent.
        """
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "x.jar"
            jar.write_bytes(b"PK\x03\x04")
            pp.sidecar_path(jar).write_text("{ this is not json")

            manifest, _ = pp.load_or_create_sidecar(jar, "2026.q1.12-lts")

            self.assertTrue(manifest["unreadable"])
            self.assertIsNone(manifest["introduced_in"])
            self.assertEqual(
                pp._tier_for(jar, manifest, "2026.q1.12-lts", _PARSE)[0], pp.ABORT
            )

    def test_orphaned_sidecar_is_never_pruned(self):
        """Removing a JAR must leave its manifest behind.

        Pulling a JAR out temporarily to check whether a bug still reproduces
        is routine; if the manifest went with it, re-adding the JAR would reset
        `introduced_in` to whatever release happened to be current and lose the
        JIRA reference.
        """
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "x.jar"
            jar.write_bytes(b"PK\x03\x04")
            pp.load_or_create_sidecar(jar, "2026.q1.12-lts")
            jar.unlink()

            self.assertEqual(pp.discover_patches(d, "2026.q1.12-lts"), [])
            self.assertTrue(pp.sidecar_path(jar).exists())


class TestDiscoverPatches(unittest.TestCase):
    def test_missing_directory_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pp.discover_patches(d, "2026.q1.12-lts"), [])

    def test_only_jars_are_discovered(self):
        with tempfile.TemporaryDirectory() as d:
            pdir = pp.patch_dir(d)
            pdir.mkdir()
            (pdir / "a.jar").write_bytes(b"PK\x03\x04")
            (pdir / "README.md").write_text("notes")

            found = pp.discover_patches(d, "2026.q1.12-lts")

            self.assertEqual([j.name for j, _ in found], ["a.jar"])


class TestCopyPatchesInto(unittest.TestCase):
    def _manager(self, probe_result=""):
        manager = MagicMock()
        # First call per JAR is the existence probe, second is the real copy.
        manager.run_command.side_effect = lambda cmd, **_kw: (
            probe_result if cmd[-1] == "-" else ""
        )
        return manager

    def test_copies_into_container_portal_dir(self):
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "a.jar"
            jar.write_bytes(b"PK\x03\x04")
            manager = self._manager()

            pp.copy_patches_into(manager, [(jar, {})], "myproject", ["docker"])

            copies = [
                c.args[0]
                for c in manager.run_command.call_args_list
                if c.args[0][-1] != "-"
            ]
            self.assertEqual(
                copies,
                [
                    [
                        "docker",
                        "cp",
                        str(jar),
                        f"myproject:{pp.CONTAINER_PORTAL_DIR}/a.jar",
                    ]
                ],
            )

    def test_probe_uses_is_none_not_truthiness(self):
        """A successful probe returns "", which is falsey but means present.

        Observed against a real created container: `docker cp c:/path -` with
        stdout discarded returns `""` when the path exists and `None` when it
        does not. A truthiness check would abort on every existing JAR.
        """
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "a.jar"
            jar.write_bytes(b"PK\x03\x04")

            copied = pp.copy_patches_into(
                self._manager(probe_result=""), [(jar, {})], "p", ["docker"]
            )

            self.assertEqual(copied, 1)

    def test_missing_upstream_jar_aborts_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "gone.jar"
            jar.write_bytes(b"PK\x03\x04")
            manager = self._manager(probe_result=None)

            with self.assertRaises(SystemExit):
                pp.copy_patches_into(manager, [(jar, {})], "p", ["docker"])

    def test_missing_upstream_jar_copied_under_force(self):
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "gone.jar"
            jar.write_bytes(b"PK\x03\x04")
            manager = self._manager(probe_result=None)

            copied = pp.copy_patches_into(
                manager, [(jar, {})], "p", ["docker"], force=True
            )

            self.assertEqual(copied, 1)


class TestPatchPermissions(unittest.TestCase):
    """Regression cover for the mode defect found by live testing.

    `docker cp` preserves the host file's mode and stamps the host UID onto the
    copy. Observed against a real liferay/dxp:2026.q1.12-lts container, a mode
    600 patch JAR landed as `-rw------- 501 root` beside its
    `-rw-r--r-- liferay liferay` neighbours, and Liferay (uid 1000) could not
    read it -- OSGi fails to resolve that one bundle while the container still
    boots and reports itself healthy.
    """

    def test_restrictive_jar_is_staged_world_readable(self):
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "a.jar"
            jar.write_bytes(b"PK\x03\x04")
            jar.chmod(0o600)

            with pp._world_readable(jar) as staged:
                self.assertNotEqual(staged, jar)
                self.assertEqual(staged.stat().st_mode & 0o777, 0o644)
                self.assertEqual(staged.read_bytes(), jar.read_bytes())
                self.assertEqual(staged.name, jar.name)

            # The developer's own file must not be modified as a side effect.
            self.assertEqual(jar.stat().st_mode & 0o777, 0o600)

    def test_already_readable_jar_is_not_copied(self):
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "a.jar"
            jar.write_bytes(b"PK\x03\x04")
            jar.chmod(0o644)

            with pp._world_readable(jar) as staged:
                self.assertEqual(staged, jar)

    def test_staging_directory_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "a.jar"
            jar.write_bytes(b"PK\x03\x04")
            jar.chmod(0o600)

            with pp._world_readable(jar) as staged:
                staged_dir = staged.parent

            self.assertFalse(staged_dir.exists())


class TestPlanPatches(unittest.TestCase):
    def _project(self, d, introduced_in):
        pdir = pp.patch_dir(d)
        pdir.mkdir()
        jar = pdir / "a.jar"
        jar.write_bytes(b"PK\x03\x04")
        pp.sidecar_path(jar).write_text(json.dumps({"introduced_in": introduced_in}))
        return jar

    def _manager(self):
        manager = MagicMock()
        manager.parse_version = _PARSE
        return manager

    def test_no_patch_dir_plans_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pp.plan_patches(self._manager(), d, "2026.q1.12-lts"), [])

    def test_matching_version_plans_the_patch(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, "2026.q1.12-lts")
            plan = pp.plan_patches(self._manager(), d, "2026.q1.12-lts")
            self.assertEqual(len(plan), 1)

    def test_release_line_change_aborts(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, "2026.q1.12-lts")
            with self.assertRaises(SystemExit):
                pp.plan_patches(self._manager(), d, "2026.q2.1-lts")

    def test_release_line_change_proceeds_under_force(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, "2026.q1.12-lts")
            plan = pp.plan_patches(self._manager(), d, "2026.q2.1-lts", force=True)
            self.assertEqual(len(plan), 1)

    @patch("ldm_core.runtime.portal_patches.UI.interruptible_pause")
    def test_patch_bump_pauses_but_proceeds(self, mock_pause):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, "2026.q1.12-lts")

            plan = pp.plan_patches(self._manager(), d, "2026.q1.13-lts")

            self.assertEqual(len(plan), 1)
            mock_pause.assert_called_once()

    @patch.dict("os.environ", {"LDM_FAIL_ON_STALE_PATCHES": "1"})
    def test_strict_env_turns_warn_into_abort(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, "2026.q1.12-lts")
            with self.assertRaises(SystemExit):
                pp.plan_patches(self._manager(), d, "2026.q1.13-lts")

    def test_per_patch_fail_on_mismatch_turns_warn_into_abort(self):
        with tempfile.TemporaryDirectory() as d:
            pdir = pp.patch_dir(d)
            pdir.mkdir()
            jar = pdir / "a.jar"
            jar.write_bytes(b"PK\x03\x04")
            pp.sidecar_path(jar).write_text(
                json.dumps(
                    {"introduced_in": "2026.q1.12-lts", "fail_on_mismatch": True}
                )
            )

            with self.assertRaises(SystemExit):
                pp.plan_patches(self._manager(), d, "2026.q1.13-lts")


class TestRecreateWithPatches(unittest.TestCase):
    """`ldm start`/`restart --force-recreate` must re-apply patches.

    These commands bypass the run pipeline and issue compose commands directly.
    Their plain forms are safe -- `docker cp` writes to the container's writable
    layer, which survives stop/start -- but `--force-recreate` replaces the
    container, so without this the patches would vanish silently.
    """

    def _manager(self):
        manager = MagicMock()
        manager.parse_version = _PARSE
        manager.args = MagicMock(force_portal_patches=False)
        # "" means the existence probe found the JAR upstream (see
        # test_probe_uses_is_none_not_truthiness).
        manager.run_command.return_value = ""
        return manager

    def _project(self, d):
        pdir = pp.patch_dir(d)
        pdir.mkdir()
        jar = pdir / "a.jar"
        jar.write_bytes(b"PK\x03\x04")
        jar.chmod(0o644)
        pp.sidecar_path(jar).write_text(json.dumps({"introduced_in": "2026.q1.12-lts"}))

    def test_no_patches_defers_to_caller(self):
        with tempfile.TemporaryDirectory() as d:
            handled = pp.recreate_with_patches(
                self._manager(),
                d,
                {"tag": "2026.q1.12-lts"},
                ["docker", "compose"],
                ["docker"],
            )
            self.assertFalse(handled)

    def test_emits_create_then_cp_then_start_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            manager = self._manager()

            handled = pp.recreate_with_patches(
                manager,
                d,
                {"tag": "2026.q1.12-lts", "container_name": "myproject"},
                ["docker", "compose"],
                ["docker"],
            )

            self.assertTrue(handled)
            verbs = [
                c.args[0][2] if c.args[0][1] == "compose" else c.args[0][1]
                for c in manager.run_command.call_args_list
            ]
            # The probe also uses `cp`; what matters is that a create precedes
            # every copy and a start follows them all.
            self.assertEqual(verbs[0], "create")
            self.assertEqual(verbs[-1], "start")
            self.assertIn("cp", verbs[1:-1])

    def test_force_recreate_is_carried_onto_create(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            manager = self._manager()

            pp.recreate_with_patches(
                manager,
                d,
                {"tag": "2026.q1.12-lts"},
                ["docker", "compose"],
                ["docker"],
            )

            create = manager.run_command.call_args_list[0].args[0]
            self.assertIn("--force-recreate", create)
            self.assertIn("--remove-orphans", create)


class TestNoBarePortalMountIsEmitted(unittest.TestCase):
    """The negative contract for LDM-#1264.

    A directory bind-mount onto `/opt/liferay/osgi/portal` masks all ~1,420
    core JARs and Liferay does not boot. This feature deliberately copies
    files in instead, so no generated compose file may ever mount that path.
    A regression here would not fail loudly -- it would produce a container
    that starts and then fails to resolve almost every bundle.
    """

    def test_compose_builder_never_mounts_osgi_portal(self):
        import inspect

        from ldm_core.handlers import composer

        source = inspect.getsource(composer)
        self.assertNotIn(
            "/opt/liferay/osgi/portal",
            source,
            "The compose builder must never mount osgi/portal -- it would mask "
            "every core JAR. Portal patches are copied in via docker cp "
            "between `compose create` and `compose start` instead.",
        )

    def test_patch_dir_is_not_a_mounted_project_path(self):
        """`portal-patches/` must not join the bind-mounted project dirs.

        The directory is a host-side source for `docker cp`; mounting it would
        reintroduce the container-UID ownership problems of LDM-#1255.
        """
        import inspect

        from ldm_core.handlers import composer

        self.assertNotIn(pp.PATCH_DIR_NAME, inspect.getsource(composer))


if __name__ == "__main__":
    unittest.main()
