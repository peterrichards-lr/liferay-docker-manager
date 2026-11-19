"""common_config_policy: what the common/ sync does to an edited shared .config (LDM-#2424).

Every test drives the real `ConfigService._sync_common_assets_files` against a
temp project and temp common/ folders, and the real `DefaultsManager` against
a temp `LDM_HOME`, so the cascade a project inherits is the one LDM resolves.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ldm_core.config import TargetNode
from ldm_core.handlers.config import ConfigService
from ldm_core.ui import UI

NAME = "com.example.Shared.config"
CONN_SRC = (
    "com.liferay.portal.search.elasticsearch8.configuration."
    "ElasticsearchConnectionConfiguration-REMOTE.config"
)
CONN = CONN_SRC.replace("-REMOTE", "")
CONN_BASELINE = (
    Path(__file__).resolve().parents[1] / "resources" / "common_baseline" / CONN_SRC
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LDM_HOME", str(home))
    monkeypatch.delenv("LDM_COMMON_DIR", raising=False)
    monkeypatch.setattr("sys.argv", ["ldm"])
    monkeypatch.chdir(tmp_path)

    # The shared-search probe (`docker inspect liferay-search-global`) goes
    # through the module-scope run_command; see test_config.py's setUp.
    run_cmd = MagicMock(return_value=None)
    monkeypatch.setattr("ldm_core.handlers.config.run_command", run_cmd)
    monkeypatch.setattr(
        "ldm_core.docker_service.get_active_target",
        MagicMock(
            return_value=TargetNode(name="local", host="localhost", is_default=True)
        ),
    )

    from ldm_core.defaults import DefaultsManager

    manager = SimpleNamespace(
        verbose=False,
        target=None,
        defaults=DefaultsManager(),
        license=MagicMock(),
        read_meta=MagicMock(return_value=None),
    )
    service = ConfigService(manager)

    def make_project(name="proj"):
        root = tmp_path / name
        (root / "osgi" / "configs").mkdir(parents=True)
        (root / "deploy").mkdir()
        return root

    def make_common(name="common"):
        d = tmp_path / name
        d.mkdir()
        return d

    def sync(root, common_dirs, meta=None):
        paths = {
            "root": root,
            "common_dirs": list(common_dirs),
            "deploy": root / "deploy",
            "configs": root / "osgi" / "configs",
        }
        with patch.object(UI, "warning") as warn:
            service._sync_common_assets_files(paths, meta or {})
        return [str(c.args[0]) for c in warn.call_args_list]

    return SimpleNamespace(
        tmp=tmp_path,
        manager=manager,
        run_cmd=run_cmd,
        make_project=make_project,
        make_common=make_common,
        sync=sync,
    )


def _checksums(root):
    path = root / ".liferay-docker" / "common_assets.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _edited_project(env, policy_meta=None):
    """A project whose copy of NAME was edited, and common/ has since moved on."""
    root = env.make_project()
    common = env.make_common()
    (common / NAME).write_text('a="1"\n')
    assert env.sync(root, [common], policy_meta) == []
    (root / "osgi" / "configs" / NAME).write_text('a="mine"\n')
    (common / NAME).write_text('a="2"\n')
    return root, common


def test_first_sync_copies_and_records_checksum(env):
    root = env.make_project()
    common = env.make_common()
    (common / NAME).write_text('a="1"\n')

    assert env.sync(root, [common]) == []

    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="1"\n'
    assert NAME in (root / ".liferay-docker.deployed").read_text().splitlines()
    assert NAME in _checksums(root)


def test_unedited_file_is_updated(env):
    root = env.make_project()
    common = env.make_common()
    (common / NAME).write_text('a="1"\n')
    env.sync(root, [common])

    (common / NAME).write_text('a="2"\n')
    assert env.sync(root, [common]) == []

    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="2"\n'


def test_protect_keeps_edit_and_warns_with_both_remedies(env):
    root, common = _edited_project(env)

    warnings = env.sync(root, [common])

    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="mine"\n'
    assert len(warnings) == 1
    assert NAME in warnings[0]
    assert "NOT applied" in warnings[0]
    assert "ldm defaults common_config_policy overwrite" in warnings[0]
    assert "ldm config common-config-policy overwrite proj" in warnings[0]
    assert "delete" in warnings[0]


def test_protect_is_silent_when_common_has_not_changed(env):
    root = env.make_project()
    common = env.make_common()
    (common / NAME).write_text('a="1"\n')
    env.sync(root, [common])
    (root / "osgi" / "configs" / NAME).write_text('a="mine"\n')

    assert env.sync(root, [common]) == []
    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="mine"\n'


def test_warn_overwrites_and_names_the_file(env):
    root, common = _edited_project(env, {"common_config_policy": "warn"})

    warnings = env.sync(root, [common], {"common_config_policy": "warn"})

    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="2"\n'
    assert len(warnings) == 1
    assert NAME in warnings[0]
    assert "Replaced your edited" in warnings[0]


def test_overwrite_is_silent(env):
    root, common = _edited_project(env)

    warnings = env.sync(root, [common], {"common_config_policy": "overwrite"})

    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="2"\n'
    assert warnings == []


def test_project_meta_overrides_default_and_others_inherit_it(env):
    env.manager.defaults.set_user_default("common_config_policy", "overwrite")
    from ldm_core.defaults import DefaultsManager

    env.manager.defaults = DefaultsManager()  # re-read ~/.ldmrc from LDM_HOME

    common = env.make_common()
    (common / NAME).write_text('a="1"\n')
    inherits = env.make_project("inherits")
    pinned = env.make_project("pinned")
    for root in (inherits, pinned):
        env.sync(root, [common])
        (root / "osgi" / "configs" / NAME).write_text('a="mine"\n')
    (common / NAME).write_text('a="2"\n')

    assert env.sync(inherits, [common], {}) == []
    pinned_warnings = env.sync(pinned, [common], {"common_config_policy": "protect"})

    assert (inherits / "osgi" / "configs" / NAME).read_text() == 'a="2"\n'
    assert (pinned / "osgi" / "configs" / NAME).read_text() == 'a="mine"\n'
    assert len(pinned_warnings) == 1


def test_old_name_only_list_is_overwritten_with_a_warning(env):
    root = env.make_project()
    common = env.make_common()
    (common / NAME).write_text('a="2"\n')
    # An LDM before LDM-#2424: name recorded, no checksum file.
    (root / ".liferay-docker.deployed").write_text(NAME)
    (root / "osgi" / "configs" / NAME).write_text('a="1"\n')

    warnings = env.sync(root, [common])

    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="2"\n'
    assert len(warnings) == 1
    assert NAME in warnings[0]
    assert NAME in _checksums(root)


def test_old_name_only_list_same_content_is_silent(env):
    root = env.make_project()
    common = env.make_common()
    (common / NAME).write_text('a="2"\n')
    (root / ".liferay-docker.deployed").write_text(NAME)
    (root / "osgi" / "configs" / NAME).write_text('a="2"\n')

    assert env.sync(root, [common]) == []
    assert NAME in _checksums(root)


def test_unlisted_project_file_is_left_alone(env):
    root = env.make_project()
    common = env.make_common()
    (common / NAME).write_text('a="2"\n')
    (root / "osgi" / "configs" / NAME).write_text('a="own"\n')

    assert env.sync(root, [common]) == []
    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="own"\n'


def test_name_in_two_common_dirs_is_not_mistaken_for_an_edit(env):
    root = env.make_project()
    first = env.make_common("common-a")
    second = env.make_common("common-b")
    (first / NAME).write_text('a="from-a"\n')
    (second / NAME).write_text('a="from-b-1"\n')
    env.sync(root, [first, second])
    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="from-b-1"\n'

    (second / NAME).write_text('a="from-b-2"\n')
    warnings = env.sync(root, [first, second])

    assert warnings == []
    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="from-b-2"\n'


# --- Elasticsearch connection config: required keys -----------------------


def _connection_project(env, edited_text):
    env.run_cmd.return_value = "elasticsearch:8.11.0"
    root = env.make_project()
    common = env.make_common()
    (common / CONN_SRC).write_text(CONN_BASELINE.read_text())
    assert env.sync(root, [common], {"use_shared_search": "true"}) == []
    dest = root / "osgi" / "configs" / CONN
    assert dest.read_text() == CONN_BASELINE.read_text()
    dest.write_text(edited_text)
    return root, common, dest


def test_protected_connection_config_missing_required_key_warns(env):
    edited = CONN_BASELINE.read_text().replace(
        'networkHostAddresses=["http://liferay-search-global:9200"]\n', ""
    )
    root, common, dest = _connection_project(env, edited)

    warnings = env.sync(root, [common], {"use_shared_search": "true"})

    assert dest.read_text() == edited
    assert len(warnings) == 1
    assert CONN in warnings[0]
    assert "networkHostAddresses" in warnings[0]
    assert "http://liferay-search-global:9200" in warnings[0]


def test_protected_connection_config_with_required_keys_is_kept_quietly(env):
    edited = CONN_BASELINE.read_text() + 'logExceptionsOnly=B"false"\n'
    root, common, dest = _connection_project(env, edited)

    warnings = env.sync(root, [common], {"use_shared_search": "true"})

    assert dest.read_text() == edited
    assert warnings == []


def test_sidecar_project_is_not_under_the_policy(env):
    """Sidecar search keeps its unconditional removal; the policy never runs."""
    es_cfg = (
        "com.liferay.portal.search.elasticsearch8.configuration."
        "ElasticsearchConfiguration.config"
    )
    edited = CONN_BASELINE.read_text().replace('active=B"true"\n', "")
    root, common, _dest = _connection_project(env, edited)
    (common / es_cfg).write_text('operationMode="REMOTE"\n')
    (root / "osgi" / "configs" / es_cfg).write_text('operationMode="REMOTE"\n')
    before = _checksums(root)

    warnings = env.sync(root, [common], {"use_shared_search": "false"})

    assert not (root / "osgi" / "configs" / es_cfg).exists()
    assert warnings == []
    assert _checksums(root) == before


def test_connection_config_from_before_checksums_is_rewritten(env):
    """LDM never recorded this file in .liferay-docker.deployed, so an
    upgraded project has it unlisted: it is still LDM's, not the user's."""
    env.run_cmd.return_value = "elasticsearch:8.11.0"
    root = env.make_project()
    common = env.make_common()
    (common / CONN_SRC).write_text(CONN_BASELINE.read_text())
    dest = root / "osgi" / "configs" / CONN
    dest.write_text("stale=true\n")

    warnings = env.sync(root, [common], {"use_shared_search": "true"})

    assert dest.read_text() == CONN_BASELINE.read_text()
    assert len(warnings) == 1


def test_parse_config_normalises_whitespace_and_continuations():
    from ldm_core.common_assets import parse_config

    parsed = parse_config(
        '# comment\nnetworkHostAddresses=[ \\\n  "http://h:9200" \\\n]\nx = B"true"\n'
    )
    assert parsed["networkHostAddresses"] == '["http://h:9200"]'
    assert parsed["x"] == 'B"true"'


# --- ldm config common-config-policy ---------------------------------------


def _parse(argv):
    from ldm_core.cli import get_parser, preprocess_args

    return get_parser()[0].parse_args(preprocess_args(argv))


def test_real_parser_routes_to_the_subcommand_not_key_value():
    from ldm_core.cli import _build_command_map

    args = _parse(["config", "common-config-policy", "warn", "myproj"])
    assert (args.command, args.subcommand) == ("config", "common-config-policy")
    assert args.policy == "warn"
    assert args.project == "myproj"
    assert getattr(args, "key", None) is None

    manager = MagicMock()
    _build_command_map(args, manager)[("config", "common-config-policy")]()
    manager.config.cmd_common_config_policy.assert_called_once_with("warn", "myproj")


def _policy_cli(env, root, unset=False):
    """A ConfigService whose manager reads and writes `root`'s real meta file."""
    from ldm_core.utils import read_meta, resolve_meta_file_path, write_meta

    meta_file = resolve_meta_file_path(root)
    env.manager.args = SimpleNamespace(unset=unset)
    env.manager.detect_project_path = MagicMock(return_value=root)
    env.manager.read_meta = lambda _root: read_meta(meta_file)
    env.manager.write_meta = lambda _root, meta: write_meta(meta_file, meta)
    return ConfigService(env.manager), meta_file


def test_setting_writes_project_meta_not_ldmrc(env):
    root = env.make_project()
    (root / "meta").write_text(json.dumps({"project_name": "proj"}))
    service, meta_file = _policy_cli(env, root)

    service.cmd_common_config_policy("warn", "proj")

    meta = json.loads(meta_file.read_text())
    assert meta["common_config_policy"] == "warn"
    assert meta["project_name"] == "proj"
    ldmrc = env.tmp / "home" / ".ldmrc"
    assert not ldmrc.exists() or "common_config_policy" not in ldmrc.read_text()


def test_unset_removes_it_and_the_project_inherits_ldm_defaults(env):
    env.manager.defaults.set_user_default("common_config_policy", "overwrite")
    from ldm_core.defaults import DefaultsManager

    env.manager.defaults = DefaultsManager()
    root = env.make_project()
    (root / "meta").write_text(json.dumps({"common_config_policy": "protect"}))
    common = env.make_common()
    (common / NAME).write_text('a="1"\n')
    env.sync(root, [common])
    (root / "osgi" / "configs" / NAME).write_text('a="mine"\n')
    (common / NAME).write_text('a="2"\n')

    service, meta_file = _policy_cli(env, root, unset=True)
    service.cmd_common_config_policy(None, "proj")

    meta = json.loads(meta_file.read_text())
    assert "common_config_policy" not in meta
    env.sync(root, [common], meta)
    assert (root / "osgi" / "configs" / NAME).read_text() == 'a="2"\n'


def test_unset_accepts_the_project_as_the_only_positional(env):
    root = env.make_project()
    (root / "meta").write_text(json.dumps({"common_config_policy": "warn"}))
    service, meta_file = _policy_cli(env, root, unset=True)

    service.cmd_common_config_policy("proj", None)

    assert "common_config_policy" not in json.loads(meta_file.read_text())
    env.manager.detect_project_path.assert_called_once_with("proj", fatal=False)


def test_invalid_value_is_refused_with_exit_1(env):
    root = env.make_project()
    (root / "meta").write_text("{}")
    service, meta_file = _policy_cli(env, root)

    with pytest.raises(SystemExit) as exc:
        service.cmd_common_config_policy("sometimes", "proj")

    assert exc.value.code == 1
    assert json.loads(meta_file.read_text()) == {}
