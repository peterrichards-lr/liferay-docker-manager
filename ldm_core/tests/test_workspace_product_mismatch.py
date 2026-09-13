"""`ldm run` must say so when it boots a line the workspace did not ask for (LDM-#1658).

`liferay.workspace.product` was write-only. `ldm set-version` wrote it
(`ldm_core/workspace/versioning.py`) and nothing read it back: `_resolve_tag`
goes `args.tag` -> `project_meta["tag"]` -> defaults -> discovery, and the
workspace pin appears at no point in that chain.

Before LDM-#1647 the discovery answers failed closed, so a developer picked a
tag by hand and the gap was unreachable. They resolve now, which is correct --
and it means `ldm run --tag-latest` on a workspace pinned to `dxp-2026.q3.0`
boots a `2026.q3.2` container against artifacts built for q3.0 without a word.

Why it bites harder than a version mismatch usually does: bnd copies a declared
`Import-Package` range into the manifest verbatim, so a fragment/override bundle
cut for one product line does not resolve on another. The failure presents as an
unresolved bundle in the OSGi log at boot, a long way from the tag decision that
caused it.

`resolve_liferay_docker_tag` is patched throughout: it performs an HTTP GET to
releases.liferay.com and caches into the developer's real home, which the "no
real state" rule in `.agents/skills/testing-and-ci/SKILL.md` forbids.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from ldm_core.pipelines.run import ConfigResolutionStage

GRADLE_PROPERTIES = """\
liferay.workspace.product=dxp-2026.q3.0
liferay.workspace.docker.image.liferay=liferay/dxp:2026.q3.0
liferay.workspace.environment=local
"""


class _Manager:
    def __init__(self, non_interactive=True, tag=None):
        self.args = SimpleNamespace(tag=tag)
        self.non_interactive = non_interactive


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "project"
        self.root.mkdir()
        self.stage = ConfigResolutionStage()

    def _write_gradle(self, content=GRADLE_PROPERTIES, base=None, subdir=None):
        target = base or self.root
        if subdir:
            target = target / subdir
        target.mkdir(parents=True, exist_ok=True)
        path = target / "gradle.properties"
        path.write_text(content, encoding="utf-8")
        return path

    def _check(self, manager=None, meta=None, tag="2026.q3.2", resolved=None):
        manager = manager or _Manager()
        with patch(
            "ldm_core.utils.resolve_liferay_docker_tag",
            return_value=resolved if resolved is not None else ("2026.q3.0", False),
        ):
            return self.stage._warn_on_workspace_product_mismatch(
                manager, {"root": self.root}, meta or {}, tag, False
            )


class TestTheMismatchIsReported(_Base):
    @patch("ldm_core.pipelines.run.UI")
    def test_a_mismatch_warns(self, mock_ui):
        self._write_gradle()

        self._check()

        warned = " ".join(str(c) for c in mock_ui.warning.call_args_list)
        self.assertIn("dxp-2026.q3.0", warned)
        self.assertIn("2026.q3.2", warned)

    @patch("ldm_core.pipelines.run.UI")
    def test_non_interactive_warns_and_proceeds(self, mock_ui):
        """Failing would break CI that has been running this way all along."""
        self._write_gradle()

        tag, is_portal = self._check(manager=_Manager(non_interactive=True))

        self.assertTrue(mock_ui.warning.called)
        self.assertEqual(tag, "2026.q3.2")
        self.assertFalse(is_portal)
        mock_ui.confirm.assert_not_called()

    @patch("ldm_core.pipelines.run.UI")
    def test_interactive_offers_the_pinned_tag_as_the_default(self, mock_ui):
        self._write_gradle()
        mock_ui.confirm.return_value = True

        tag, _ = self._check(manager=_Manager(non_interactive=False))

        self.assertEqual(tag, "2026.q3.0")
        prompt, default = mock_ui.confirm.call_args[0]
        self.assertIn("2026.q3.0", prompt)
        self.assertEqual(default, "Y", "the pinned tag must be the default answer")

    @patch("ldm_core.pipelines.run.UI")
    def test_declining_keeps_the_resolved_tag(self, mock_ui):
        self._write_gradle()
        mock_ui.confirm.return_value = False

        tag, _ = self._check(manager=_Manager(non_interactive=False))

        self.assertEqual(tag, "2026.q3.2")

    @patch("ldm_core.pipelines.run.UI")
    def test_accepting_a_portal_pin_switches_the_repository(self, mock_ui):
        """Otherwise the tag would be looked up in liferay/dxp, not liferay/portal."""
        self._write_gradle("liferay.workspace.product=portal-7.4.3.132-ga132\n")
        mock_ui.confirm.return_value = True

        tag, is_portal = self._check(
            manager=_Manager(non_interactive=False),
            resolved=("7.4.3.132-ga132", True),
        )

        self.assertEqual(tag, "7.4.3.132-ga132")
        self.assertTrue(is_portal)


class TestTheSilences(_Base):
    """Three cases that must produce no warning at all."""

    @patch("ldm_core.pipelines.run.UI")
    def test_an_explicit_tag_is_a_decision_not_an_accident(self, mock_ui):
        self._write_gradle()

        tag, _ = self._check(manager=_Manager(tag="2026.q3.2"))

        self.assertEqual(tag, "2026.q3.2")
        mock_ui.warning.assert_not_called()

    @patch("ldm_core.pipelines.run.UI")
    def test_a_workspace_with_no_pin_has_expressed_no_opinion(self, mock_ui):
        self._write_gradle("liferay.workspace.environment=local\n")

        self._check()

        mock_ui.warning.assert_not_called()

    @patch("ldm_core.pipelines.run.UI")
    def test_a_commented_out_pin_is_not_a_pin(self, mock_ui):
        """`#liferay.workspace.product=...` is how a developer disables it."""
        self._write_gradle("#liferay.workspace.product=dxp-2026.q3.0\n")

        self._check()

        mock_ui.warning.assert_not_called()

    @patch("ldm_core.pipelines.run.UI")
    def test_no_gradle_properties_at_all_is_silent(self, mock_ui):
        self._check()

        mock_ui.warning.assert_not_called()

    @patch("ldm_core.pipelines.run.UI")
    def test_agreement_costs_no_network_call(self, mock_ui):
        """`dxp-2026.q3.0` and `2026.q3.0` are the same answer spelled twice."""
        self._write_gradle()

        with patch("ldm_core.utils.resolve_liferay_docker_tag") as resolve:
            tag, _ = self.stage._warn_on_workspace_product_mismatch(
                _Manager(), {"root": self.root}, {}, "2026.q3.0", False
            )

        self.assertEqual(tag, "2026.q3.0")
        resolve.assert_not_called()
        mock_ui.warning.assert_not_called()

    @patch("ldm_core.pipelines.run.UI")
    def test_an_lts_suffix_is_not_a_mismatch(self, mock_ui):
        """`dxp-2026.q1.7` resolves to `2026.q1.7-lts`; that is agreement."""
        self._write_gradle("liferay.workspace.product=dxp-2026.q1.7\n")

        self._check(tag="2026.q1.7-lts", resolved=("2026.q1.7-lts", False))

        mock_ui.warning.assert_not_called()


class TestWhereTheWorkspaceIsFound(_Base):
    @patch("ldm_core.pipelines.run.UI")
    def test_an_lcp_workspace_nests_the_properties_under_liferay(self, mock_ui):
        self._write_gradle(subdir="liferay")

        self._check()

        self.assertTrue(mock_ui.warning.called)

    @patch("ldm_core.pipelines.run.UI")
    def test_the_linked_workspace_path_is_preferred_over_the_project_root(
        self, mock_ui
    ):
        """LDM-#1684 restores this key; until then the project root carries it."""
        linked = Path(self._tmp.name) / "workspace"
        self._write_gradle(base=linked)
        self._write_gradle("liferay.workspace.product=dxp-2026.q3.2\n")

        self._check(meta={"workspace_path": str(linked)})

        warned = " ".join(str(c) for c in mock_ui.warning.call_args_list)
        self.assertIn(
            "dxp-2026.q3.0",
            warned,
            "the linked workspace's pin must win over the project root's",
        )

    @patch("ldm_core.pipelines.run.UI")
    def test_a_missing_linked_path_falls_back_to_the_project_root(self, mock_ui):
        self._write_gradle()

        self._check(meta={"workspace_path": str(Path(self._tmp.name) / "gone")})

        self.assertTrue(mock_ui.warning.called)


class TestTheCheckIsActuallyWired(unittest.TestCase):
    """A helper nothing calls is the LDM-#1681 failure mode, one layer up.

    Driving the whole of `ConfigResolutionStage.execute` would need a project,
    a registry and Docker -- `test_vanilla_flag.py` says as much and isolates
    its rule instead. So this asserts the wiring structurally, over the AST of
    `execute` itself.

    Note what is and is not being claimed. The objection recorded in
    `test_fragment_override_module.py` is to comparing source *positions of two
    different functions*, which says nothing about execution order. Here both
    statements are straight-line code in one function body, where statement
    order IS execution order.
    """

    def _execute_body(self):
        import ast
        import inspect
        import textwrap

        src = textwrap.dedent(inspect.getsource(ConfigResolutionStage.execute))
        function = ast.parse(src).body[0]
        assert isinstance(function, ast.FunctionDef)
        return function.body

    def test_the_result_is_assigned_back_to_the_tag(self):
        """A warning nobody acts on would leave the accepted tag unused."""
        import ast

        for node in self._execute_body():
            if not isinstance(node, ast.Assign):
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            if (
                getattr(call.func, "attr", None)
                != "_warn_on_workspace_product_mismatch"
            ):
                continue
            targets = ast.dump(node.targets[0])
            self.assertIn("'tag'", targets)
            self.assertIn("'is_portal'", targets)
            return
        self.fail(
            "ConfigResolutionStage.execute never calls "
            "_warn_on_workspace_product_mismatch -- the check would be dead code"
        )

    def test_it_runs_after_the_tag_has_been_resolved(self):
        """Checking a tag that does not exist yet would compare against None."""
        import ast

        resolve_at = -1
        warn_at = -1
        for index, node in enumerate(self._execute_body()):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            attr = getattr(node.value.func, "attr", None)
            if attr == "_resolve_tag":
                resolve_at = index
            elif attr == "_warn_on_workspace_product_mismatch":
                warn_at = index

        self.assertNotEqual(resolve_at, -1, "_resolve_tag is no longer called here")
        self.assertNotEqual(warn_at, -1, "the mismatch check is not called here")
        self.assertLess(resolve_at, warn_at)


if __name__ == "__main__":
    unittest.main()
