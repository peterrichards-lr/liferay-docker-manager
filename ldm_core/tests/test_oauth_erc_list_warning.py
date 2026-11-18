"""Warn when a config-node microservice's LCP.json lacks its ERC list (LDM-#2407).

config-node's `lxcConfig.oauthApplication(erc)` resolves only an ERC listed in
`LIFERAY_OAUTH_APPLICATION_EXTERNAL_REFERENCE_CODES`. Nobody but the extension
author writes that variable -- Liferay's PaaS docs put it in `LCP.json` `env`
-- and config-node 0.4.1 throws an unhelpful TypeError without it (LDM-#2388).

The rule is deliberately narrow, so a correctly configured project is quiet:

* only a Node service whose package.json depends on config-node is checked.
  Spring Boot reads its own application*.properties and fails clearly;
* only an ABSENT variable warns. A present list that omits a declared ERC is
  usually deliberate (AICA omits user-agent apps it never looks up), so the
  doctor mentions it at verbose level only and import not at all.

Every test writes real files and drives a real entry point: the doctor's
`_check_project_specific`, or the import's `_hydrate_from_workspace`.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ldm_core.handlers.workspace import WorkspaceService
from ldm_core.tests.test_workspace_hydration import _FakeManager
from ldm_core.ui import UI

VAR = "LIFERAY_OAUTH_APPLICATION_EXTERNAL_REFERENCE_CODES"

APPS_YAML = """\
assemble:
    -   from: build
        into: static
svc-oahs:
    .serviceAddress: localhost:3001
    name: Svc Headless Server
    scopes:
        -   Liferay.Headless.Delivery.everything
    type: oAuthApplicationHeadlessServer
svc-oaua:
    .serviceAddress: localhost:3001
    name: Svc User Agent
    scopes:
        -   Liferay.Headless.Delivery.everything
    type: oAuthApplicationUserAgent
svc-object-action:
    name: Svc Object Action
    oAuth2ApplicationExternalReferenceCode: svc-oaua
    resourcePath: /object/action
    type: objectAction
"""

NO_APPS_YAML = """\
svc-custom-element:
    name: Svc Element
    type: customElement
"""

CONFIG_NODE = {"dependencies": {"@rotty3000/config-node": "^0.4.1"}}
PLAIN_NODE = {"dependencies": {"express": "^4.19.0"}}


def write_cx(
    parent: Path,
    name: str,
    *,
    yaml_text: str | None = APPS_YAML,
    env: dict | None = None,
    lcp: bool = True,
    dockerfile: bool = True,
    kind: str = "Deployment",
    package: dict | str | None = CONFIG_NODE,
) -> Path:
    cx = parent / "client-extensions" / name
    cx.mkdir(parents=True)
    if yaml_text is not None:
        (cx / "client-extension.yaml").write_text(yaml_text)
    if lcp:
        (cx / "LCP.json").write_text(
            json.dumps({"id": name.replace("-", ""), "kind": kind, "env": env or {}})
        )
    if dockerfile:
        (cx / "Dockerfile").write_text("FROM node:22\n")
    if package is not None:
        (cx / "package.json").write_text(
            package if isinstance(package, str) else json.dumps(package)
        )
    return cx


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)


class DoctorCase(_Case):
    """Runs the real `DoctorRunner._check_project_specific` over a project."""

    def doctor(self, project: Path):
        from ldm_core.config import TargetNode
        from ldm_core.diagnostics.doctor import DoctorRunner
        from ldm_core.tests.test_diagnostics import MockDiagManager

        runner = DoctorRunner(MockDiagManager().diagnostics)
        runner.project_paths = [project]
        details: list[str] = []
        with (
            patch(
                "ldm_core.docker_service.get_active_target",
                return_value=TargetNode(name="local", host="localhost"),
            ),
            patch("ldm_core.diagnostics.doctor.run_command", return_value=""),
            patch.object(UI, "detail", side_effect=details.append),
        ):
            runner._check_project_specific()
        rows = [r for r in runner.results if "OAuth ERC List" in r[0]]
        hints = "\n".join(h["text"] for h in runner.hints)
        return rows, hints, "\n".join(str(d) for d in details)

    def assert_quiet(self, project: Path):
        rows, hints, _ = self.doctor(project)
        self.assertEqual(rows, [])
        self.assertNotIn(VAR, hints)


class TheDoctorWarns(DoctorCase):
    def test_a_config_node_project_missing_the_variable_is_warned(self):
        project = self.base / "proj"
        write_cx(project, "svc")

        rows, hints, _ = self.doctor(project)

        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][2], "warn", "this must never be a failure")
        self.assertIn("client-extensions/svc", rows[0][1])
        self.assertIn(f"its LCP.json env has no {VAR}", hints)
        self.assertIn(f'"{VAR}": "svc-oahs,svc-oaua"', hints)

    def test_the_liferay_client_extension_package_counts_as_config_node(self):
        project = self.base / "proj"
        write_cx(
            project,
            "svc",
            package={"devDependencies": {"@liferay/client-extension": "^1.0.0"}},
        )

        rows, hints, _ = self.doctor(project)

        self.assertEqual(len(rows), 1, rows)
        self.assertIn(f'"{VAR}": "svc-oahs,svc-oaua"', hints)

    def test_a_built_extension_without_yaml_is_read_from_its_config_json(self):
        """An LDM project holds extracted build zips, which carry no YAML.

        The ERCs are then the `~<erc>` suffixes in the built
        `*.client-extension-config.json`, as shipped by `gradlew build`.
        """
        project = self.base / "proj"
        cx = write_cx(project, "svc", yaml_text=None)
        prefix = "com.liferay.oauth2.provider.configuration."
        (cx / "svc.client-extension-config.json").write_text(
            json.dumps(
                {
                    f"{prefix}OAuth2ProviderApplicationHeadlessServerConfiguration~svc-oahs": {
                        "type": "oAuthApplicationHeadlessServer"
                    },
                    f"{prefix}OAuth2ProviderApplicationUserAgentConfiguration~svc-oaua": {
                        "type": "oAuthApplicationUserAgent"
                    },
                }
            )
        )

        rows, hints, _ = self.doctor(project)

        self.assertEqual(len(rows), 1, rows)
        self.assertIn(f'"{VAR}": "svc-oahs,svc-oaua"', hints)


class TheDoctorStaysQuiet(DoctorCase):
    def test_an_omitted_erc_with_the_variable_present_is_not_warned(self):
        """AICA's working config: user-agent apps it never looks up are left out.

        It is still mentioned, at verbose level only.
        """
        project = self.base / "proj"
        write_cx(project, "svc", env={VAR: "svc-oahs"})

        rows, hints, details = self.doctor(project)

        self.assertEqual(rows, [])
        self.assertNotIn(VAR, hints)
        self.assertIn(f"svc-oaua that its LCP.json {VAR} does not list", details)

    def test_a_complete_list_is_not_flagged_even_at_verbose(self):
        project = self.base / "proj"
        # Quoted, spaced and carrying an extra ERC, as config-node accepts.
        write_cx(project, "svc", env={VAR: '"svc-oaua, svc-oahs,other-oahs"'})

        rows, hints, details = self.doctor(project)

        self.assertEqual(rows, [])
        self.assertNotIn(VAR, hints)
        self.assertNotIn(VAR, details)

    def test_a_node_project_without_config_node_is_not_warned(self):
        project = self.base / "proj"
        write_cx(project, "svc", package=PLAIN_NODE)
        self.assert_quiet(project)

    def test_a_spring_project_with_no_package_json_is_not_warned(self):
        project = self.base / "proj"
        write_cx(project, "svc", package=None)
        self.assert_quiet(project)

    def test_an_invalid_package_json_is_not_warned(self):
        project = self.base / "proj"
        write_cx(project, "svc", package="{not json")
        self.assert_quiet(project)

    def test_an_extension_without_an_oauth_application_is_not_flagged(self):
        project = self.base / "proj"
        write_cx(project, "svc", yaml_text=NO_APPS_YAML)
        self.assert_quiet(project)

    def test_an_extension_with_no_lcp_json_is_not_flagged(self):
        project = self.base / "proj"
        write_cx(project, "svc", lcp=False)
        self.assert_quiet(project)

    def test_a_job_is_not_flagged_even_with_a_dockerfile(self):
        """Batch and site initializers are Jobs using LIFERAY_BATCH_OAUTH_APP_ERC."""
        project = self.base / "proj"
        write_cx(project, "svc", kind="Job")
        self.assert_quiet(project)

    def test_an_lcp_json_without_a_dockerfile_is_not_flagged(self):
        project = self.base / "proj"
        write_cx(project, "svc", dockerfile=False)
        self.assert_quiet(project)


class _ImportCase(_Case):
    """Runs the real `_hydrate_from_workspace` that `ldm import` calls."""

    def hydrate(self, workspace: Path):
        project = self.base / "project"
        paths = {
            "root": project,
            "cx": project / "osgi" / "client-extensions",
            "modules": project / "osgi" / "modules",
            "deploy": project / "deploy",
        }
        for directory in paths.values():
            directory.mkdir(parents=True, exist_ok=True)
        service = WorkspaceService(_FakeManager(project))

        warnings: list[str] = []
        raw: list[str] = []
        with (
            patch.object(
                UI, "warning", side_effect=lambda m, *_a, **_k: warnings.append(m)
            ),
            patch.object(UI, "raw", side_effect=raw.append),
        ):
            result = service._hydrate_from_workspace(workspace, paths)
        return result, "\n".join(warnings), "\n".join(raw)


class TheImportWarns(_ImportCase):
    def test_a_config_node_project_missing_the_variable_is_warned_at_import(self):
        workspace = self.base / "ws"
        write_cx(workspace, "svc")

        result, warnings, raw = self.hydrate(workspace)

        self.assertTrue(result, "a warning must never fail the import")
        self.assertIn(
            "'svc' uses config-node and declares OAuth application(s) "
            "svc-oahs, svc-oaua",
            warnings,
        )
        self.assertIn(f"has no {VAR}", warnings)
        self.assertIn(f'"{VAR}": "svc-oahs,svc-oaua"', raw)

    def test_correct_omitted_and_unrelated_extensions_are_quiet_at_import(self):
        workspace = self.base / "ws"
        write_cx(workspace, "ok", env={VAR: "svc-oahs,svc-oaua"})
        write_cx(workspace, "omits", env={VAR: "svc-oaua"})
        write_cx(workspace, "plain", yaml_text=NO_APPS_YAML)
        write_cx(workspace, "spring", package=None)
        write_cx(workspace, "express", package=PLAIN_NODE)
        write_cx(workspace, "frontend", lcp=False, dockerfile=False)

        _, warnings, raw = self.hydrate(workspace)

        self.assertNotIn(VAR, warnings)
        self.assertNotIn(VAR, raw)
        self.assertNotIn("svc-oahs", warnings)


if __name__ == "__main__":
    unittest.main()
