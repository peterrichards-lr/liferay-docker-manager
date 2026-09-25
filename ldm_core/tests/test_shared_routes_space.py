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
# LDM-#1944: the single anchored mount that replaced the two leaves above.
ROUTES_MOUNT = "/etc/liferay/lxc/routes"


def _composer():
    return ComposerService(MagicMock())


def _mount_paths():
    return {"root": Path(tempfile.mkdtemp())}


def _targets(mounts):
    return {m.split(":")[1] for m in mounts}


def _source_for(mounts, target):
    return next(m.split(":")[0] for m in mounts if m.split(":")[1] == target)


class TestAClientExtensionSeesBothConfigTrees:
    """LDM-#1928 established that an extension needs both trees.

    LDM-#1944 changed HOW it gets them: one mount at the routes root, with the
    two trees addressed as subdirectories via the variables the extension
    already reads. The requirement is unchanged; the mechanism is not.
    """

    def _env(self, ext, ext_id="ms1"):
        return dict(
            e.split("=", 1) for e in _composer()._extension_routes_env(ext, ext_id)
        )

    def test_both_trees_are_reachable(self):
        """The LDM-#1928 requirement, restated against the new mechanism."""
        env = self._env({"id": "ms1"})
        assert set(env) == {"LIFERAY_ROUTES_DXP", "LIFERAY_ROUTES_CLIENT_EXTENSION"}, (
            "an extension reads both config trees; pointing it at only the "
            "DXP one leaves LIFERAY_ROUTES_CLIENT_EXTENSION at nothing "
            "(LDM-#1928)"
        )
        for value in env.values():
            assert value.startswith(ROUTES_MOUNT + "/"), (
                f"{value} is outside the single mounted tree, so nothing is "
                f"mounted at it (LDM-#1944)"
            )

    def test_the_mount_is_the_instance_tree_not_a_leaf(self):
        """The LDM-#1944 fix itself.

        A leaf bind mount resolves its source inode once, at container-create
        time, and cannot survive that directory being deleted and recreated --
        which something does between the mount being established and Liferay
        publishing. Measured: the extension held inode 2013443 while the live
        directory was 2013444.
        """
        mp = _mount_paths()
        mounts = _composer()._extension_routes_mounts({"id": "ms1"}, "ms1", mp)
        assert len(mounts) == 1, f"expected one anchored mount, got {mounts}"
        source, target = mounts[0].split(":")
        assert target == ROUTES_MOUNT
        assert source.endswith("/routes/default"), (
            f"mounted at {source}. It must be the virtual-instance directory: "
            f"a leaf mount goes stale when its directory is replaced "
            f"(LDM-#1944), and `routes` itself would expose every other "
            f"virtual instance's trees."
        )

    def test_the_extension_tree_is_its_own_subdirectory(self):
        """Per-extension, because Liferay publishes per-extension config."""
        env = self._env({"id": "ms1"})
        assert env["LIFERAY_ROUTES_CLIENT_EXTENSION"] == f"{ROUTES_MOUNT}/ms1"

    def test_the_subtree_is_named_from_the_project_name_not_the_id(self):
        """LDM-#1944's first half, which the anchoring did not replace.

        Liferay derives the directory from the `ext.lxc.liferay.com/projectName`
        ConfigMap label, not the LCP.json id. All 16 samples in
        `ldm-cx-samples` have ids that differ from their projectName.
        """
        env = self._env(
            {"id": "ecopulseheadlessauth", "project_name": "ecopulse-headless-auth"}
        )
        assert (
            env["LIFERAY_ROUTES_CLIENT_EXTENSION"]
            == f"{ROUTES_MOUNT}/ecopulse-headless-auth"
        ), env

    def test_the_id_remains_the_fallback(self):
        """An extension whose config carries no projectName is no worse off."""
        env = self._env({"id": "ms1", "project_name": None})
        assert env["LIFERAY_ROUTES_CLIENT_EXTENSION"] == f"{ROUTES_MOUNT}/ms1"

    def test_the_dxp_tree_is_shared_not_per_extension(self):
        """DXP metadata describes Liferay itself, so every extension resolves
        the same directory -- the one Liferay writes."""
        a = self._env({"id": "a"}, "a")
        b = self._env({"id": "b"}, "b")
        assert a["LIFERAY_ROUTES_DXP"] == b["LIFERAY_ROUTES_DXP"]
        assert (
            a["LIFERAY_ROUTES_CLIENT_EXTENSION"] != b["LIFERAY_ROUTES_CLIENT_EXTENSION"]
        )

    def test_neither_tree_is_mounted_over_the_application(self):
        """LDM-#1911: /opt/liferay/routes in an extension container is the
        application's own code."""
        mp = _mount_paths()
        mounts = _composer()._extension_routes_mounts({"id": "ms1"}, "ms1", mp)
        for target in _targets(mounts):
            assert not target.startswith("/opt/liferay"), (
                f"mounted at {target}, which shadows the extension's own code"
            )

    def test_ldm_overrides_the_extensions_own_declaration(self):
        """The deliberate inversion of LDM-#1923 option 4.

        Honouring the declaration was right while the mount was a leaf. Under
        the anchored mount it points the extension at a path nothing is
        mounted at, which is the failure LDM-#1944 exists to remove -- LDM owns
        the layout and the extension cannot know it.
        """
        ext = {
            "id": "ms1",
            "env": {
                "LIFERAY_ROUTES_DXP": "/custom/dxp",
                "LIFERAY_ROUTES_CLIENT_EXTENSION": "/custom/ext",
            },
        }
        env = self._env(ext)
        assert env["LIFERAY_ROUTES_DXP"] == f"{ROUTES_MOUNT}/dxp"
        assert env["LIFERAY_ROUTES_CLIENT_EXTENSION"] == f"{ROUTES_MOUNT}/ms1"


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


class TestTheRoutesTreeIsScaffoldedOnTheHost:
    """LDM-#1955: `_scaffold_routes_tree` had no pytest coverage at all.

    It was never referenced anywhere in `ldm_core/tests/` -- only by
    `scripts/verify_e2e_refactor.{sh,ps1}`, which do not run on every PR.

    What it prevents: Docker creates a missing bind-mount source itself, as an
    empty ROOT-OWNED directory. A subtree LDM never scaffolds is one nothing
    can write to -- and on a tree whose permissions are already the subject of
    LDM-#1944, a root-owned directory is the last thing wanted.

    The directories are derived from the compose that was just built, so a
    mount and its host directory cannot drift apart.
    """

    def _svc(self, root, *targets):
        routes = ComposerService.routes_root({"root": root}).as_posix()
        return {
            "ext": {
                "volumes": [
                    f"{routes}/default/{t}:/etc/liferay/lxc/{t}" for t in targets
                ]
            }
        }

    def test_it_creates_every_subtree_the_compose_mounts(self):
        root = Path(tempfile.mkdtemp())
        mp = {"root": root}
        _composer()._scaffold_routes_tree(self._svc(root, "dxp", "my-ext"), mp, mp)
        for sub in ("dxp", "my-ext"):
            assert (root / "routes" / "default" / sub).is_dir(), (
                f"routes/default/{sub} was not created, so Docker will "
                f"auto-create it as an empty root-owned directory (LDM-#1928)"
            )

    def test_a_remote_target_is_skipped_deliberately(self):
        """Creating a remote node's directories on this machine would be worse
        than not creating them, so the two paths differing means skip."""
        root = Path(tempfile.mkdtemp())
        _composer()._scaffold_routes_tree(
            self._svc(root, "dxp"), {"root": root}, {"root": Path("/remote/elsewhere")}
        )
        assert not (root / "routes").exists()

    def test_it_creates_the_subtrees_the_anchored_mount_hides(self):
        """LDM-#1944: the anchored mount's only source is `routes/default`.

        The volume-derived loop therefore never reaches the per-extension
        subtrees, and they still have to exist -- an extension starting before
        Liferay has published reads the tree immediately, and `config.node`
        treats a MISSING tree as a configuration error rather than an empty
        one. Before anchoring they existed because they WERE the mount sources.
        """
        root = Path(tempfile.mkdtemp())
        mp = {"root": root}
        routes = ComposerService.routes_root(mp).as_posix()
        services = {
            "ext": {
                "volumes": [f"{routes}/default:{ROUTES_MOUNT}"],
                "environment": [
                    f"LIFERAY_ROUTES_DXP={ROUTES_MOUNT}/dxp",
                    f"LIFERAY_ROUTES_CLIENT_EXTENSION={ROUTES_MOUNT}/my-ext",
                ],
            }
        }
        _composer()._scaffold_routes_tree(services, mp, mp)
        for sub in ("dxp", "my-ext"):
            assert (root / "routes" / "default" / sub).is_dir(), (
                f"routes/default/{sub} was not created. The anchored mount "
                f"hides it from the volume-derived loop, so it must be "
                f"derived from the environment instead (LDM-#1944)"
            )

    def test_it_ignores_a_routes_variable_pointing_outside_the_mount(self):
        """Not a general mkdir: only paths inside the anchor are ours."""
        root = Path(tempfile.mkdtemp())
        mp = {"root": root}
        routes = ComposerService.routes_root(mp).as_posix()
        _composer()._scaffold_routes_tree(
            {
                "ext": {
                    "volumes": [f"{routes}/default:{ROUTES_MOUNT}"],
                    "environment": ["LIFERAY_ROUTES_DXP=/somewhere/else"],
                }
            },
            mp,
            mp,
        )
        assert not (root / "routes" / "somewhere").exists()
        assert not Path("/somewhere/else").exists()

    def test_it_does_not_create_directories_for_unrelated_mounts(self):
        """Only sources under the routes root -- it is not a general mkdir."""
        root = Path(tempfile.mkdtemp())
        mp = {"root": root}
        unrelated = (root / "somewhere-else").as_posix()
        _composer()._scaffold_routes_tree(
            {"ext": {"volumes": [f"{unrelated}:/opt/x"]}}, mp, mp
        )
        assert not (root / "somewhere-else").exists()


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
