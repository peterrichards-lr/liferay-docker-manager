"""A package carries what the publisher declared, not what LDM rendered (LDM-#2370).

`ldm snapshot` used to build `custom_env` from the project's RENDERED
`docker-compose.yml`, taking every `LIFERAY_*` entry of the liferay service
bar three. Everything LDM generated for the publisher's machine therefore
travelled as if the publisher had declared it. AICA v3.3.59 shipped its CI
machine's virtual host:

    LIFERAY_COMPANY_PERIOD_DEFAULT_PERIOD_VIRTUAL_PERIOD_HOST_PERIOD_NAME=aica-e2e.demo

and because `composer._build_liferay_service` puts `custom_env` ahead of the
run pipeline's `base_env` (which is merged only for keys not already set), the
consumer's default company became `aica-e2e.demo` and its microservice
followed that host to its own loopback.

Two halves, each tested where it ships:

- capture: `_generate_snapshot_metadata` against a compose rendered by the real
  composer from the real run stage's environment;
- restore: `cmd_restore`, with the package meta in the JSON-string form
  `archive.py` writes (LDM-#2315), and the result rendered by the composer.

`TheSetCoversWhatLdmRenders` keeps the shared key set honest: it renders the
real stage's output and requires every LDM-generated key to be recognised.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

import ldm_core.tests.test_liferay_env_reaches_compose as rendered
import ldm_core.tests.test_snapshot as snapshot_tests
from ldm_core.handlers.snapshot import is_ldm_rendered_env_key
from ldm_core.snapshot.archive import ArchiveSnapshotService

VHOST = rendered.VHOST
VHOST_SYNC = "LIFERAY_COMPANY_PERIOD_DEFAULT_PERIOD_VIRTUAL_PERIOD_HOST_PERIOD_SYNC_PERIOD_ON_PERIOD_STARTUP"
DISPLAY_NODE = "LIFERAY_WEB_PERIOD_SERVER_PERIOD_DISPLAY_PERIOD_NODE_PERIOD_NAME"
SEARCH = "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED"
USER_KEY = "LIFERAY_AICA_PERIOD_TENANT_PERIOD_ID"
PUBLISHER_HOST = "aica-e2e.demo"
CONSUMER_HOST = "consumer.local"


def _run_stage_env(fixture):
    """The list `ComposerStage` really hands the composer, for the publisher's
    host `aica-e2e.demo` (with --upgrade-db, so that branch contributes)."""
    return rendered.EverythingTheRunStageBuildsArrives._stage_env(fixture)


class TheSnapshotCapturesTheDeclaredEnvironment(rendered._ComposeFixture):
    def _publish(self, project_meta, render=True):
        """Writes the compose LDM renders for `project_meta`, then drives the
        real capture over the same project."""
        if render:
            env = self._rendered_env(
                _run_stage_env(self),
                {"custom_env": project_meta.get("custom_env") or "{}"},
            )
            self.rendered_env = env
            root = self.paths["root"]
            (root / "docker-compose.yml").write_text(
                yaml.safe_dump({"services": {"liferay": {"environment": self.raw_env}}})
            )
        facade = MagicMock()
        facade.manager.args = MagicMock()
        facade.manager.run_command.return_value = ""
        svc = ArchiveSnapshotService(facade)
        snap_dir = self.paths["root"] / "snap"
        svc._generate_snapshot_metadata(
            "n",
            "ts",
            project_meta,
            self.paths["root"],
            self.paths,
            snap_dir,
            None,
            None,
        )
        meta = facade.manager.write_meta.call_args.args[1]
        return json.loads(meta["custom_env"]) if meta["custom_env"] else None

    def test_only_the_users_key_is_captured(self):
        captured = self._publish({"custom_env": json.dumps({USER_KEY: "acme"})})

        # Guards the fixture: the compose really holds the keys that leaked,
        # so the assertion below cannot pass on a compose that lacks them.
        self.assertEqual(self.rendered_env.get(VHOST), PUBLISHER_HOST)
        for key in (VHOST_SYNC, DISPLAY_NODE, SEARCH, USER_KEY):
            self.assertIn(key, self.rendered_env)

        self.assertEqual(captured, {USER_KEY: "acme"})

    def test_a_polluted_meta_is_cleaned_before_it_is_published(self):
        """A project restored from a pre-fix package holds the publisher's
        host in its own meta; republishing must not carry it on."""
        captured = self._publish(
            {
                "custom_env": json.dumps(
                    {VHOST: PUBLISHER_HOST, DISPLAY_NODE: "true", USER_KEY: "acme"}
                )
            },
            render=False,
        )

        self.assertEqual(captured, {USER_KEY: "acme"})

    def test_the_document_library_store_still_travels(self):
        """It describes the packaged data, and `.ldmp` restore does not
        re-detect it; the compose-based capture carried it, so this must."""
        store = "com.liferay.portal.store.file.system.AdvancedFileSystemStore"
        captured = self._publish({"dl_store_impl": store}, render=False)

        self.assertEqual(captured, {"LIFERAY_DL_PERIOD_STORE_PERIOD_IMPL": store})

    def test_nothing_declared_captures_nothing(self):
        self.assertIsNone(self._publish({}))


class TheSetCoversWhatLdmRenders(rendered._ComposeFixture):
    """Every `LIFERAY_*` key LDM renders for the liferay service must be one
    `is_ldm_rendered_env_key` recognises, or restore would let it override
    the consumer. Driven from the real stage and composer, so a key added to
    either later fails here rather than in a consumer's portal."""

    def test_every_generated_key_is_recognised(self):
        exempt = {
            # Excluded by name at capture, as they always were.
            "LIFERAY_JVM_OPTS",
            "LIFERAY_HOME",
            "LIFERAY_HSQL_PERIOD_ENABLED",
            # Describes the packaged data; deliberately carried (see archive.py).
            "LIFERAY_DL_PERIOD_STORE_PERIOD_IMPL",
        }
        for extra in (
            {},
            {"reindex_required": "true", "dl_store_impl": "x"},
            {"search_mode": "shared"},
            {"scale_liferay": 2},
        ):
            with self.subTest(meta=extra):
                env = self._rendered_env(_run_stage_env(self), extra)
                generated = {
                    k for k in env if k.startswith("LIFERAY_") and k not in exempt
                }
                self.assertTrue(generated)
                missed = sorted(k for k in generated if not is_ldm_rendered_env_key(k))
                self.assertEqual(missed, [], env)


class RestoreStripsWhatAPublishedPackageCarries(rendered._ComposeFixture):
    """Packages published before the capture fix -- AICA v3.3.59 among them --
    still carry the publisher's host. Driven through `cmd_restore`."""

    def _restore(self, package_custom_env):
        manager = snapshot_tests.MockSnapshotManager()
        snap = manager.snapshot
        root = Path(self._td.name) / "consumer"
        root.mkdir()
        choice = root / "snapshots" / "20261113_000000"
        choice.mkdir(parents=True)
        (choice / "volume.tgz").write_bytes(b"x")
        paths = {"root": root, "backups": root / "snapshots", "data": root / "data"}
        package_meta = {"tag": "2026.q1.0-lts", "custom_env": package_custom_env}

        def read_meta(path):
            return dict(package_meta) if Path(path).name == "meta" else {}

        with (
            patch.object(manager, "detect_project_path", return_value=root),
            patch.object(manager, "setup_paths", return_value=paths),
            patch.object(manager, "check_uncommitted_changes"),
            patch.object(manager, "read_meta", side_effect=read_meta),
            patch.object(manager, "write_meta"),
            patch.object(snap.utils, "_resolve_snapshot_choice", return_value=choice),
            patch.object(snap.volumes, "_restore_cloud_volume"),
            patch.object(snap, "_defer_restored_site_initializers"),
            patch.object(snap.database, "_restore_database"),
            patch.object(snap.search, "_restore_search"),
            patch.object(snap.custom_containers, "_restore_custom_images"),
            patch.object(snap, "flag_reindex", return_value=True),
            patch(
                "ldm_core.docker_service.DockerService.get_docker_cmd_prefix",
                return_value=["docker"],
            ),
            patch("ldm_core.handlers.snapshot.time.sleep"),
            patch("ldm_core.ui.UI.warning"),
            patch("ldm_core.ui.UI.detail"),
        ):
            snap.cmd_restore("consumer", no_run=True)
        # What the restore hands the run pipeline, which renders it.
        return manager.runtime.cmd_run.call_args.kwargs["project_meta"]

    def package(self):
        """The string form `archive.py` writes and `read_meta` returns."""
        return json.dumps(
            {
                VHOST: PUBLISHER_HOST,
                VHOST_SYNC: "true",
                DISPLAY_NODE: "true",
                SEARCH: "true",
                USER_KEY: "acme",
            }
        )

    def test_the_fixture_is_the_string_form(self):
        self.assertIsInstance(self.package(), str)

    def test_the_publishers_machine_keys_are_removed_and_the_users_survive(self):
        project_meta = self._restore(self.package())

        self.assertIsInstance(project_meta["custom_env"], str)
        self.assertEqual(json.loads(project_meta["custom_env"]), {USER_KEY: "acme"})

    def test_the_consumers_own_host_is_rendered(self):
        """At the composer seam: the restored meta rendered with the
        consumer's own run environment ends on the consumer's host."""
        project_meta = self._restore(self.package())

        env = self._rendered_env(
            [f"{VHOST}={CONSUMER_HOST}"], {"custom_env": project_meta["custom_env"]}
        )

        self.assertEqual(env.get(VHOST), CONSUMER_HOST)
        self.assertEqual(env.get(USER_KEY), "acme")

    def test_control_an_unstripped_package_would_win(self):
        """Proves the seam above is the one that broke: rendered as packaged,
        the publisher's host beats the consumer's."""
        env = self._rendered_env(
            [f"{VHOST}={CONSUMER_HOST}"], {"custom_env": self.package()}
        )

        self.assertEqual(env.get(VHOST), PUBLISHER_HOST)


if __name__ == "__main__":
    unittest.main()
