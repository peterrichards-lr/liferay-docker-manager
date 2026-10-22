"""NodeService runs the node-power utility, in-process (LDM-#2291).

These used to mock `subprocess.run` and assert the argv handed to it. That
passed while `ldm node power` was broken in every packaged build -- the script
it launched was never shipped -- and while `sync-dns` was missing from the
script altogether: the mock supplied the only thing either test needed. They
now go through the real parser and dispatch in `ldm_core.node_power`, patching
only the functions that would reach AWS.
"""

from unittest.mock import MagicMock, patch

import pytest

from ldm_core import node_power
from ldm_core.handlers.node import NodeService


@pytest.fixture
def service() -> NodeService:
    return NodeService(MagicMock())


@pytest.mark.parametrize(
    ("call", "handler", "expected"),
    [
        (lambda s: s.cmd_node_power_status(), "cmd_status", {}),
        (
            lambda s: s.cmd_node_power_wake("aws-1", ttl="4h"),
            "cmd_wake",
            {"node": "aws-1", "ttl": "4h"},
        ),
        (lambda s: s.cmd_node_power_sleep("aws-1"), "cmd_sleep", {"node": "aws-1"}),
        (lambda s: s.cmd_node_power_enforce(), "cmd_enforce", {}),
        (lambda s: s.cmd_node_power_sync_dns(), "cmd_sync_dns", {}),
    ],
)
def test_each_subcommand_reaches_its_handler(service, call, handler, expected):
    with patch.object(node_power, handler) as target:
        assert call(service) == 0
    target.assert_called_once()
    parsed = target.call_args.args[0]
    for key, value in expected.items():
        assert getattr(parsed, key) == value


def test_a_failing_handler_returns_its_exit_code(service):
    """The utility fails through sys.exit; ldm must return that, not raise."""
    with patch.object(node_power, "cmd_sleep", side_effect=SystemExit(1)):
        assert service.cmd_node_power_sleep("aws-1") == 1


def test_nothing_is_launched_as_a_subprocess(service):
    """The packaged-build failure: a separate process running a file no build
    ships, via a sys.executable that is ldm itself under PyInstaller."""
    with (
        patch.object(node_power, "cmd_status"),
        patch("subprocess.run") as run,
        patch("subprocess.Popen") as popen,
    ):
        service.cmd_node_power_status()
    run.assert_not_called()
    popen.assert_not_called()


class TestSyncDns:
    """`sync-dns` was offered by the CLI and absent from the utility."""

    def _run(self, nodes, ip):
        with (
            patch.object(node_power, "load_target_nodes", return_value=nodes),
            patch.object(node_power, "get_ec2_public_ip", return_value=ip) as query,
            patch.object(node_power, "update_node_host_ip") as update,
        ):
            node_power.main(["sync-dns"])
        return query, update

    def test_a_changed_ip_is_written_everywhere(self):
        _, update = self._run(
            {
                "aws-2": {
                    "ec2_instance_id": "i-1",
                    "region": "eu-west-1",
                    "host": "1.1.1.1",
                }
            },
            "2.2.2.2",
        )
        update.assert_called_once_with("aws-2", "2.2.2.2")

    def test_an_unchanged_ip_writes_nothing(self):
        _, update = self._run(
            {"aws-2": {"ec2_instance_id": "i-1", "host": "2.2.2.2"}}, "2.2.2.2"
        )
        update.assert_not_called()

    def test_a_node_without_an_instance_is_skipped(self):
        query, update = self._run({"lab": {"host": "10.0.0.5"}}, "9.9.9.9")
        query.assert_not_called()
        update.assert_not_called()


def test_an_installed_ldm_knows_no_nodes_it_was_not_given(tmp_path):
    """LDM-#2291: no built-in nodes and no download of anyone's config. The
    maintainer's servers belong to this repository's CI, not to every user."""
    with (
        patch.object(node_power, "CONFIG_FILE", tmp_path / "node-power-config.json"),
        patch.object(node_power, "LDMRC_FILE", tmp_path / ".ldmrc"),
        patch("urllib.request.urlopen", side_effect=AssertionError("network")),
        patch("urllib.request.urlretrieve", side_effect=AssertionError("network")),
    ):
        assert node_power.load_target_nodes() == {}
    assert not (tmp_path / "node-power-config.json").exists()


def test_a_configured_node_gets_every_field(tmp_path):
    ldmrc = tmp_path / ".ldmrc"
    ldmrc.write_text('{"targets": {"my-node": {"host": "10.0.0.9"}}}')
    with (
        patch.object(node_power, "CONFIG_FILE", tmp_path / "missing.json"),
        patch.object(node_power, "LDMRC_FILE", ldmrc),
    ):
        node = node_power.load_target_nodes()["my-node"]
    assert node["host"] == "10.0.0.9"
    assert node["region"] == ""
    assert node["user"] == "ubuntu"
