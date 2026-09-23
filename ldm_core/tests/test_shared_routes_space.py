"""LDM-#1928: the routes tree is a shared space, not a Liferay-only one.

Liferay publishes its config trees into `<project>/routes`, and everything
that needs them reads a subtree of it. Two things were wrong:

* A client extension was given only `default/dxp`. Real extensions declare
  TWO trees in their LCP.json and read both --

      "LIFERAY_ROUTES_CLIENT_EXTENSION": "/etc/liferay/lxc/ext-init-metadata",
      "LIFERAY_ROUTES_DXP":              "/etc/liferay/lxc/dxp-metadata"
      "config.node.config.trees": ["${LIFERAY_ROUTES_CLIENT_EXTENSION}",
                                   "${LIFERAY_ROUTES_DXP}"]

  LDM forwarded the first variable straight out of LCP.json and mounted
  nothing at it, so every extension was pointed at a path containing nothing.
  That tree is where Liferay publishes per-extension config, including the
  OAuth2 credentials it generates.

* Custom services got none of it -- no routes, no LXC variables, no route back
  to the host, and no wait for Liferay.

Client extensions are a headline feature: LDM handles their domains, routing
and ports automatically. That only works if the config-tree half works too.
"""

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from ldm_core.handlers.composer import ComposerService

DXP = "/etc/liferay/lxc/dxp-metadata"
EXT_INIT = "/etc/liferay/lxc/ext-init-metadata"


def _composer():
    return ComposerService(MagicMock())


def _mount_paths():
    return {"root": Path(tempfile.mkdtemp())}


def _targets(mounts):
    return {m.split(":")[1] for m in mounts}


def _source_for(mounts, target):
    return next(m.split(":")[0] for m in mounts if m.split(":")[1] == target)


class TestAClientExtensionSeesBothConfigTrees:
    def test_both_trees_are_mounted(self):
        mp = _mount_paths()
        mounts = _composer()._extension_routes_mounts({"id": "ms1"}, "ms1", mp)
        assert _targets(mounts) == {DXP, EXT_INIT}, (
            "an extension reads both config trees; mounting only the DXP one "
            "points LIFERAY_ROUTES_CLIENT_EXTENSION at nothing (LDM-#1928)"
        )

    def test_the_extension_tree_is_its_own_subdirectory(self):
        """Per-extension, because Liferay publishes per-extension config."""
        mp = _mount_paths()
        mounts = _composer()._extension_routes_mounts({"id": "ms1"}, "ms1", mp)
        source = _source_for(mounts, EXT_INIT)
        assert source.endswith("/routes/default/ms1"), source

    def test_the_dxp_tree_is_shared_not_per_extension(self):
        """DXP metadata describes Liferay itself, so every extension sees the
        same directory -- the one Liferay writes."""
        mp = _mount_paths()
        a = _composer()._extension_routes_mounts({"id": "a"}, "a", mp)
        b = _composer()._extension_routes_mounts({"id": "b"}, "b", mp)
        assert _source_for(a, DXP) == _source_for(b, DXP)
        assert _source_for(a, EXT_INIT) != _source_for(b, EXT_INIT)

    def test_neither_tree_is_mounted_over_the_application(self):
        """LDM-#1911: /opt/liferay/routes in an extension container is the
        application's own code."""
        mp = _mount_paths()
        mounts = _composer()._extension_routes_mounts({"id": "ms1"}, "ms1", mp)
        for target in _targets(mounts):
            assert not target.startswith("/opt/liferay"), (
                f"mounted at {target}, which shadows the extension's own code"
            )

    def test_the_extensions_own_declaration_wins(self):
        """Derived from LCP.json, which LDM already parses into ext['env'] --
        so a non-standard path is honoured rather than overridden. This is what
        LDM-#1923 wanted; it is infeasible from the built image, not from here.
        """
        mp = _mount_paths()
        ext = {
            "id": "ms1",
            "env": {
                "LIFERAY_ROUTES_DXP": "/custom/dxp",
                "LIFERAY_ROUTES_CLIENT_EXTENSION": "/custom/ext",
            },
        }
        mounts = _composer()._extension_routes_mounts(ext, "ms1", mp)
        assert _targets(mounts) == {"/custom/dxp", "/custom/ext"}, mounts

    def test_the_constants_are_only_a_fallback(self):
        mp = _mount_paths()
        mounts = _composer()._extension_routes_mounts({"id": "ms1"}, "ms1", mp)
        assert _targets(mounts) == {DXP, EXT_INIT}


class TestACustomServiceGetsTheSameSharedSpace:
    def test_a_plain_custom_service_receives_all_four(self):
        svc: dict[str, Any] = {"image": "nginx"}
        _composer()._apply_shared_project_context(svc, [], "localhost", _mount_paths())
        assert _targets(svc["volumes"]) == {DXP}
        assert "LIFERAY_LXC_DXP_MAIN_DOMAIN=localhost" in svc["environment"]
        assert svc["extra_hosts"] == ["localhost:host-gateway"]
        assert svc["depends_on"] == {"liferay": {"condition": "service_healthy"}}

    def test_a_declared_volume_is_never_displaced(self):
        """A custom container is an arbitrary third-party image. Mounting over
        a path its owner configured is LDM-#1911 done to an image LDM knows
        nothing about.

        The `:z` matters: Linux appends the SELinux label, so the target is the
        SECOND field, not whatever follows the last colon.
        """
        svc: dict[str, Any] = {"image": "wordpress"}
        mine = f"/somewhere/mine:{DXP}:z"
        _composer()._apply_shared_project_context(
            svc, [mine], "localhost", _mount_paths()
        )
        assert svc["volumes"] == [mine], (
            f"LDM added a second mount over the user's own: {svc['volumes']}"
        )

    def test_a_declared_variable_is_never_overwritten(self):
        svc: dict[str, Any] = {
            "image": "nginx",
            "environment": ["LIFERAY_LXC_DXP_DOMAINS=mine"],
        }
        _composer()._apply_shared_project_context(svc, [], "localhost", _mount_paths())
        assert "LIFERAY_LXC_DXP_DOMAINS=mine" in svc["environment"]
        assert "LIFERAY_LXC_DXP_DOMAINS=localhost" not in svc["environment"]

    def test_declared_ordering_is_never_overwritten(self):
        """A custom service may deliberately not depend on Liferay."""
        own = {"db": {"condition": "service_started"}}
        svc: dict[str, Any] = {"image": "nginx", "depends_on": dict(own)}
        _composer()._apply_shared_project_context(svc, [], "localhost", _mount_paths())
        assert svc["depends_on"] == own


class TestNothingStartsBeforeLiferayIsServing:
    """The tree is written by Liferay at boot, so a container that starts
    first reads an empty directory. `service_healthy` resolves against the
    liferay/dxp image's own HEALTHCHECK, which curls /c/portal/layout -- so it
    means "serving pages", not merely "process started". Measured: compose
    honours an image-level healthcheck for this condition.
    """

    def test_a_custom_service_waits_for_liferay(self):
        svc: dict[str, Any] = {"image": "nginx"}
        _composer()._apply_shared_project_context(svc, [], "localhost", _mount_paths())
        assert svc["depends_on"]["liferay"]["condition"] == "service_healthy", (
            "started concurrently with a boot that takes minutes, so the "
            "config tree it mounts is empty when it reads it (LDM-#1928)"
        )


class TestTheCodeMatchesTheDocumentedContract:
    """The guard that outlives this conversation.

    Every regression in this area followed the same path: someone changed a
    mount, updated the test to match the new code, and shipped. The test could
    not object because it asserted the implementation. `test_composer.py` says
    so in its own comment -- that assertion "has now been wrong three times".

    So this one does not assert a value. It reads the contract table out of
    `.agents/skills/ldm-architecture/SKILL.md` and requires the composer to
    agree with it. Changing a mount now means changing the architecture
    document in the same commit, which is the step that was missing every
    time: the mechanism was documented nowhere, so a refactor had nothing to
    preserve.
    """

    SKILL = (
        Path(__file__).resolve().parent.parent.parent
        / ".agents"
        / "skills"
        / "ldm-architecture"
        / "SKILL.md"
    )

    def _documented_targets(self):
        text = self.SKILL.read_text(encoding="utf-8")
        assert "## The Routes Tree (Shared Config Space)" in text, (
            "The routes tree section has been removed from the architecture "
            "skill. It exists because this mechanism was undocumented and "
            "therefore silently dropped three times -- restore it rather than "
            "deleting this guard."
        )
        return text

    def test_the_documented_defaults_are_the_constants_the_code_uses(self):
        text = self._documented_targets()
        for constant in (DXP, EXT_INIT):
            assert constant in text, (
                f"{constant} is mounted by the composer but is not in the "
                "architecture skill's routes table. Document it there, or the "
                "next refactor has nothing telling it this mount matters."
            )

    def test_the_application_path_is_documented_as_forbidden(self):
        text = self._documented_targets()
        assert "/opt/liferay/routes" in text and "shadow" in text.lower(), (
            "The architecture skill no longer records WHY an extension must "
            "not be mounted at /opt/liferay/routes. That reason is the whole "
            "of LDM-#1911, and losing it is how the mount was 'corrected' back "
            "to the broken value once already."
        )

    def test_every_mount_the_composer_emits_is_a_documented_one(self):
        """The direction that actually catches a new regression: code first."""
        text = self._documented_targets()
        mounts = _composer()._extension_routes_mounts(
            {"id": "ms1"}, "ms1", _mount_paths()
        )
        for target in _targets(mounts):
            assert target in text, (
                f"The composer mounts a client extension at {target}, which "
                "appears nowhere in the architecture skill's routes table. "
                "Add it there in this same change -- an undocumented mount is "
                "one a future refactor will drop without noticing (LDM-#1928)."
            )
