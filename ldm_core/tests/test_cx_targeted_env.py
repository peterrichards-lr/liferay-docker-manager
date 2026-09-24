"""Service-targeted host variables must reach a client-extension container.

LDM-#1903: `docs/reference/configuration.md` section 4 documents targeting a
specific service "including Client Extensions" -- `SERVICEID_FOO=bar` on the
host arriving as `FOO=bar` in that service's container. The implementation
worked; nothing ever called it with a `target_id`, so the branch was
unreachable and a client-extension container received only what its own
LCP.json declared.

The global passthrough pool is deliberately NOT injected. That distinction is
the substance of the fix, so it is asserted in both directions.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ldm_core.config import TargetNode
from ldm_core.handlers.composer import ComposerService
from ldm_core.handlers.workspace import WorkspaceService

EXT_ID = "ai-commerce-accelerator-microservice"
PREFIX = "AI_COMMERCE_ACCELERATOR_MICROSERVICE_"


def _cx_env(env, target=("local", "localhost")):
    """The `environment:` list the composer builds for the extension service."""
    with patch.dict(os.environ, env, clear=True):
        mgr = MagicMock()
        mgr.workspace = WorkspaceService(mgr)
        mgr.workspace.scan_client_extensions = MagicMock(
            return_value=[
                {
                    "id": EXT_ID,
                    "deploy": True,
                    "is_service": True,
                    "path": "/tmp/ms",
                    "env": {"LIFERAY_NODE_RUNNER_START": "npm start"},
                }
            ]
        )
        paths = {
            "root": Path("/tmp/p"),
            "cx": Path("/tmp/p/cx"),
            "ce_dir": Path("/tmp/p/ce"),
        }
        node = TargetNode(name=target[0], host=target[1], is_default=True)
        with patch("ldm_core.config.get_active_target", return_value=node):
            services = ComposerService(mgr)._build_extensions_services(
                paths, {}, "example.test", "proj", False
            )
        return services[f"proj-{EXT_ID}"]["environment"]


class TestTargetedVariablesArrive(unittest.TestCase):
    def test_a_targeted_variable_arrives_with_the_prefix_stripped(self):
        got = _cx_env({f"{PREFIX}LIFERAY_API_URL": "https://example.test"})
        self.assertIn("LIFERAY_API_URL=https://example.test", got)

    def test_the_prefixed_form_does_not_also_arrive(self):
        """`SERVICEID_FOO` becomes `FOO`; shipping both would be noise."""
        got = _cx_env({f"{PREFIX}DEBUG": "true"})
        self.assertIn("DEBUG=true", got)
        self.assertFalse([e for e in got if e.startswith(PREFIX)])

    def test_the_extensions_own_declared_env_survives(self):
        got = _cx_env({f"{PREFIX}DEBUG": "true"})
        self.assertIn("LIFERAY_NODE_RUNNER_START=npm start", got)

    def test_it_works_identically_against_a_remote_node(self):
        """LDM-#1894's lesson: behaviour must not vary with the target."""
        env = {f"{PREFIX}LIFERAY_API_URL": "https://example.test"}
        self.assertEqual(
            sorted(_cx_env(env)),
            sorted(_cx_env(env, target=("ec2", "10.0.0.9"))),
        )


class TestTheGlobalPoolIsNotInjected(unittest.TestCase):
    """The narrow scope is the point, not an omission.

    A targeted variable names one service explicitly, so delivering it
    surprises nobody. The global pool is implicit and carries whatever the
    developer happens to have exported -- see LDM-#1910, where a GitHub PAT was
    found being forwarded as `BOT_PAT`.
    """

    def test_an_ldm_stripped_variable_does_not_reach_the_container(self):
        got = _cx_env({"LDM_COMPANY_ID": "123"})
        self.assertNotIn("COMPANY_ID=123", got)

    def test_a_provider_passthrough_variable_does_not_reach_the_container(self):
        got = _cx_env({"OPENAI_API_KEY": "sk-x"})
        self.assertFalse([e for e in got if e.startswith("OPENAI_API_KEY")])

    def test_a_forward_prefix_variable_does_not_reach_the_container(self):
        """Even a prefix the user configured stays global -- it is not
        addressed to this service."""
        got = _cx_env(
            {"LDM_FORWARD_PREFIXES": "LIFERAY_", "LIFERAY_API_URL": "https://x"}
        )
        self.assertFalse([e for e in got if e.startswith("LIFERAY_API_URL")])


class TestAnotherServicesVariablesAreNotDelivered(unittest.TestCase):
    def test_a_variable_targeted_at_a_different_service_is_ignored(self):
        got = _cx_env({"SOME_OTHER_SERVICE_DEBUG": "true"})
        self.assertNotIn("DEBUG=true", got)


class TestTheBlacklistIsTestedAgainstTheDeliveredName(unittest.TestCase):
    """LDM-#1954: it was tested against the HOST spelling instead.

    `get_service_targeted_env` strips the service prefix before handing the
    variable to the container, but filtered on the un-stripped name. The two
    differ by that prefix for every target but Liferay, so any pattern
    anchored at the START of the name never matched:
    `SERVICEID_LIFERAY_ROUTES_CLIENT_EXTENSION` sailed past `LIFERAY_ROUTES_*`
    and arrived as `LIFERAY_ROUTES_CLIENT_EXTENSION`.

    Suffix patterns were never affected -- a prefix does not disturb a suffix
    match -- which is why the credential half of LDM-#1910 held and this went
    unnoticed. The negative cases in `TestTheGlobalPoolIsNotInjected` above all
    happen to use suffix-matched or `LDM_`-stripped names, so none of them
    could see it. That is the gap this class closes.

    The branch was unreachable until LDM-#1903 made it callable, so the bypass
    became reachable in v2.26.0.
    """

    def _delivered(self, name):
        return [e for e in _cx_env({PREFIX + name: "x"}) if e.startswith(name + "=")]

    def test_a_targeted_routes_variable_never_reaches_the_container(self):
        """The one with teeth: it repoints a tree LDM itself mounts, which
        reproduces exactly the symptom LDM-#1928 fixed -- a container pointed
        at a path nothing is mounted at."""
        for name in (
            "LIFERAY_ROUTES_CLIENT_EXTENSION",
            "LIFERAY_ROUTES_DXP",
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    self._delivered(name),
                    f"{name} is LDM-managed and must not be forwarded by any "
                    f"route, targeted or not (LDM-#1954)",
                )

    def test_a_targeted_lxc_variable_never_reaches_the_container(self):
        self.assertFalse(self._delivered("COM_LIFERAY_LXC_DXP_MAIN_DOMAIN"))

    def test_an_exact_match_blacklist_entry_is_honoured_when_targeted(self):
        """`LIFERAY_JVM_OPTS` is an exact entry, not a pattern -- it was
        defeated the same way, since the host name carries the prefix."""
        self.assertFalse(self._delivered("LIFERAY_JVM_OPTS"))

    def test_an_ordinary_targeted_variable_still_arrives(self):
        """The control. A fix that simply blocked more would pass every
        assertion above and break the feature LDM-#1903 delivered."""
        self.assertTrue(self._delivered("NODE_ENV"))
        self.assertTrue(self._delivered("HARMLESS_FLAG"))


if __name__ == "__main__":
    unittest.main()
