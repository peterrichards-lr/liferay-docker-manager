"""An imported package must not override the resolved search mode (LDM-#1773).

Found while tracking down an OOM on an 8 GB machine running
`ldm quickstart aica`. The project resolved to **shared** search:

```
search_mode shared   use_shared_search true
```

and the stack it rendered ran an **embedded** Elasticsearch inside the Liferay
container as well, by two routes that both bypass the decision
`resolve_infrastructure_mode` had just made.

## Route 1 -- the captured environment

`snapshot/archive.py` captures the Liferay service's environment **from the
rendered compose file**, so these entries are not a publisher's declaration at
all: they are LDM's own output, from a different machine, travelling back in.
`composer._build_liferay_service` appends `custom_env` *after* the
shared-search block, and Compose resolves a duplicated key to the later entry:

```yaml
- LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=false   # shared mode
- LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=true    # from custom_env
- LIFERAY_ELASTICSEARCH_PERIOD_OPERATION_PERIOD_MODE=EMBEDDED
```

## Route 2 -- the restored portal-ext

A package built on a `--sidecar` machine carries
`...ElasticsearchConfiguration.operationMode=EMBEDDED` and that machine's
sidecar ports, and `module.framework.properties.*` is deliberately the
highest-precedence route into that configuration.

**The file lands in two layers of the cascade at once**, which is the part
worth a test of its own. It is layer 5 (project customisations) where it sits,
and `cmd_restore` copies it to `ldmp-portal-ext.properties` as layer 2.
Stripping only the layer-2 copy achieves nothing: the key stops matching the
baseline and is promoted to a layer-5 customisation, which outranks every other
layer. `TheStripCoversBothCascadeLayers` exists because the first draft of this
fix did exactly that.

## The contract (maintainer's call, 2026-09-17)

Strip the keys LDM owns, and **say so**. It matches what already happens to
`jdbc.default.url` and `virtual.hosts.valid.hosts`, which are regenerated on
import -- search topology is the same class of thing, a property of the
consumer's machine rather than of the package. Warning alone would leave the
consumer with a stack that OOMs. Silent stripping was rejected because a
publisher who set `operationMode` deliberately would get no signal.
"""

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.handlers.snapshot import (
    SnapshotService,
    announce_stripped_search_keys,
    strip_ldm_search_env,
    strip_ldm_search_properties,
)

ES_PROP = (
    "module.framework.properties.com.liferay.portal.search.elasticsearch7"
    ".configuration.ElasticsearchConfiguration"
)


class TheCapturedEnvironmentIsCleaned(unittest.TestCase):
    def test_ldms_own_search_keys_are_removed(self):
        kept, removed = strip_ldm_search_env(
            {
                "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED": "true",
                "LIFERAY_ELASTICSEARCH_PERIOD_OPERATION_PERIOD_MODE": "EMBEDDED",
            }
        )

        self.assertEqual(kept, {})
        self.assertEqual(len(removed), 2)

    def test_the_numbered_variants_are_removed_too(self):
        """`_inject_liferay_search_env` emits ELASTICSEARCH7/8, not just the
        unnumbered form. Matching only the unnumbered one would leave the
        higher-precedence half in place."""
        _, removed = strip_ldm_search_env(
            {
                "LIFERAY_ELASTICSEARCH7_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED": "true",
                "LIFERAY_ELASTICSEARCH8_PERIOD_NETWORK_PERIOD_HOST_PERIOD_ADDRESSES": "search:9200",
            }
        )

        self.assertEqual(len(removed), 2)

    def test_everything_else_survives(self):
        """A publisher's own variables are theirs. This removes LDM's output,
        not the package's configuration."""
        kept, removed = strip_ldm_search_env(
            {
                "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED": "true",
                "LIFERAY_AICA_PERIOD_API_PERIOD_KEY": "abc",
                "LIFERAY_JDBC_PERIOD_DEFAULT_PERIOD_URL": "jdbc:postgresql://x/y",
            }
        )

        self.assertEqual(
            kept,
            {
                "LIFERAY_AICA_PERIOD_API_PERIOD_KEY": "abc",
                "LIFERAY_JDBC_PERIOD_DEFAULT_PERIOD_URL": "jdbc:postgresql://x/y",
            },
        )
        self.assertEqual(
            list(removed), ["LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED"]
        )

    def test_nothing_to_strip_reports_nothing(self):
        """The announcement is driven by what was removed, so a clean package
        must produce an empty result rather than an empty-ish one."""
        kept, removed = strip_ldm_search_env({"LIFERAY_AICA_PERIOD_X": "1"})

        self.assertEqual(kept, {"LIFERAY_AICA_PERIOD_X": "1"})
        self.assertEqual(removed, {})

    def test_a_non_dict_is_returned_untouched(self):
        """`custom_env` is a dict in a snapshot, but meta is user-editable."""
        value, removed = strip_ldm_search_env("LIFERAY_X=1")

        self.assertEqual(value, "LIFERAY_X=1")
        self.assertEqual(removed, {})


class TheRestoredPropertiesAreCleaned(unittest.TestCase):
    def test_the_elasticsearch_configuration_is_removed(self):
        text, removed = strip_ldm_search_properties(
            f"{ES_PROP}.operationMode=EMBEDDED\n{ES_PROP}.sidecarHttpPort=9201\n"
        )

        self.assertEqual(text, "")
        self.assertEqual(len(removed), 2)
        self.assertEqual(removed[f"{ES_PROP}.operationMode"], "EMBEDDED")

    def test_the_publishers_own_properties_survive(self):
        text, removed = strip_ldm_search_properties(
            "# AICA settings\n"
            "aica.feature.enabled=true\n"
            f"{ES_PROP}.operationMode=EMBEDDED\n"
            "virtual.hosts.valid.hosts=aica.demo\n"
        )

        self.assertIn("aica.feature.enabled=true", text)
        self.assertIn("virtual.hosts.valid.hosts=aica.demo", text)
        self.assertNotIn("operationMode", text)

    def test_comments_and_layout_survive(self):
        """The file is a publisher's document. Round-tripping it through a
        property parser would silently reformat everything they wrote."""
        original = "# header\n\n# why this exists\naica.x=1\n\n"
        text, removed = strip_ldm_search_properties(original)

        self.assertEqual(text, original)
        self.assertEqual(removed, {})

    def test_a_commented_out_setting_is_left_alone(self):
        """A commented line is inert, and deleting it would destroy a note the
        publisher deliberately left behind."""
        original = f"#{ES_PROP}.operationMode=EMBEDDED\n"
        text, removed = strip_ldm_search_properties(original)

        self.assertEqual(text, original)
        self.assertEqual(removed, {})


class TheOperatorIsTold(unittest.TestCase):
    """Silent stripping was rejected: a publisher who set `operationMode` on
    purpose must get a signal."""

    def announce(self, removed):
        said = []

        def record(message, *_args, **_kwargs):
            said.append(str(message))

        with (
            patch("ldm_core.ui.UI.warning", side_effect=record),
            patch("ldm_core.ui.UI.detail", side_effect=record),
        ):
            announce_stripped_search_keys(removed, "portal-ext.properties", "shared")
        return "\n".join(said)

    def test_it_names_the_key_the_value_and_the_source(self):
        out = self.announce({f"{ES_PROP}.operationMode": "EMBEDDED"})

        self.assertIn("operationMode", out)
        self.assertIn("EMBEDDED", out)
        self.assertIn("portal-ext.properties", out)

    def test_it_names_the_mode_the_machine_resolved(self):
        """ "Discarded a setting" is not actionable without what it was
        measured against."""
        out = self.announce({f"{ES_PROP}.operationMode": "EMBEDDED"})

        self.assertIn("shared", out)

    def test_nothing_removed_says_nothing(self):
        """A message on every import would train the operator to skip it."""
        self.assertEqual(self.announce({}), "")


class TheStripCoversBothCascadeLayers(unittest.TestCase):
    """The restored `portal-ext.properties` is layer 5 AND the source of the
    layer-2 copy. The first draft of this fix stripped only the copy, which
    achieves nothing: the key stops matching the baseline and is promoted to a
    layer-5 customisation, outranking every layer below it.

    Drives the real `_install_restored_portal_ext` -- the method `cmd_restore`
    calls -- rather than a copy of its body. A test that re-implements the code
    it is testing passes against the bug (LDM-#1759).
    """

    def install(self, contents):
        """Runs the real method over a temp project and returns (layer5, layer2)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = root / "files"
            files.mkdir()
            target_pe = files / "portal-ext.properties"
            target_pe.write_text(contents, encoding="utf-8")

            service = SnapshotService.__new__(SnapshotService)
            service.manager = MagicMock()

            with patch("ldm_core.ui.UI.warning"), patch("ldm_core.ui.UI.detail"):
                service._install_restored_portal_ext({"root": root, "files": files}, {})

            return (
                target_pe.read_text(encoding="utf-8"),
                (root / ".liferay-docker" / "ldmp-portal-ext.properties").read_text(
                    encoding="utf-8"
                ),
            )

    def test_both_files_come_out_clean(self):
        layer5, layer2 = self.install(f"aica.x=1\n{ES_PROP}.operationMode=EMBEDDED\n")

        self.assertNotIn("operationMode", layer5, "layer 5 still carries it")
        self.assertNotIn("operationMode", layer2, "layer 2 still carries it")

    def test_the_rest_of_the_file_reaches_both_layers(self):
        layer5, layer2 = self.install(f"aica.x=1\n{ES_PROP}.operationMode=EMBEDDED\n")

        self.assertIn("aica.x=1", layer5)
        self.assertIn("aica.x=1", layer2)

    def test_a_clean_package_is_copied_unchanged(self):
        original = "# publisher notes\naica.x=1\n"
        layer5, layer2 = self.install(original)

        self.assertEqual(layer5, original)
        self.assertEqual(layer2, original)

    def test_a_missing_file_is_not_an_error(self):
        """`files/` exists without a portal-ext on a package that ships none."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "files").mkdir()
            service = SnapshotService.__new__(SnapshotService)
            service.manager = MagicMock()

            service._install_restored_portal_ext(
                {"root": root, "files": root / "files"}, {}
            )

        self.assertFalse((root / ".liferay-docker").exists())


class TheCustomEnvCleanerIsDriven(unittest.TestCase):
    """Drives `_clean_restored_custom_env`, the method `cmd_restore` calls."""

    def clean(self, custom_env):
        service = SnapshotService.__new__(SnapshotService)
        service.manager = MagicMock()
        with patch("ldm_core.ui.UI.warning"), patch("ldm_core.ui.UI.detail"):
            return service._clean_restored_custom_env(custom_env, {})

    def test_it_returns_the_package_env_without_ldms_search_keys(self):
        kept = self.clean(
            {
                "LIFERAY_ELASTICSEARCH_PERIOD_OPERATION_PERIOD_MODE": "EMBEDDED",
                "LIFERAY_AICA_PERIOD_X": "1",
            }
        )

        self.assertEqual(kept, {"LIFERAY_AICA_PERIOD_X": "1"})


class ItRunsDuringRestore(unittest.TestCase):
    """A strip nobody calls is the LDM-#1774 shape.

    Asserted against the source of `cmd_restore`, because driving the whole
    command needs a real snapshot archive. This is deliberately the weaker half
    of the pair: it checks the two calls are present, and the classes above
    check what they do when they run.
    """

    def test_cmd_restore_calls_both_cleaners(self):
        source = inspect.getsource(SnapshotService.cmd_restore)

        self.assertIn("_install_restored_portal_ext(", source)
        self.assertIn("_clean_restored_custom_env(", source)


if __name__ == "__main__":
    unittest.main()
