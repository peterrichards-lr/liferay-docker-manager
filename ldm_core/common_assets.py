"""What the common/ sync may do to a project's copy of a shared `.config` (LDM-#2424).

A `.config` or `.cfg` copied from a `common/` folder used to be overwritten on
every sync once its name was in the project's `.liferay-docker.deployed`, so an
edit made in the project was lost silently. This module holds the pieces the
sync in `ldm_core/handlers/config.py` needs to tell an edit apart from a file
LDM wrote, and to decide what to do about it:

- the `common_config_policy` setting and its values
- the per-project checksum record, `.liferay-docker/common_assets.json`, kept
  separate from `.liferay-docker.deployed` so an older LDM still reads that
  list (names only) after a downgrade
- the keys LDM itself needs in each `.config` it manages, checked when the
  policy keeps a user's edited copy
"""

import contextlib
import hashlib
import json
from pathlib import Path

POLICY_KEY = "common_config_policy"

PROTECT = "protect"
WARN = "warn"
OVERWRITE = "overwrite"
POLICIES = (PROTECT, WARN, OVERWRITE)
DEFAULT_POLICY = PROTECT

# Destination name -> sha256 of the content LDM last wrote there.
CHECKSUMS_RELPATH = Path(".liferay-docker") / "common_assets.json"

# Keys LDM relies on in each `.config` it manages, with the value it needs.
# Deliberately not the whole file: anything else in it is the user's to change.
#
# The Elasticsearch connection config. The values are what the shipped baseline
# writes (`ldm_core/resources/common_baseline/
# com.liferay.portal.search.elasticsearch{7,8}.configuration.ElasticsearchConnectionConfiguration-REMOTE.config`)
# for shared search, minus `authenticationEnabled`: the shared container runs
# with `xpack.security.enabled=false` (`ldm_core/handlers/infra.py`), so turning
# authentication on does not break the connection.
#
# - `active`, `connectionId`: the connection `ElasticsearchConfiguration`'s
#   `remoteClusterConnectionId="REMOTE"` names, switched on
# - `networkHostAddresses`: the shared container, `liferay-search-global`
# - `httpSSLEnabled`: that container serves plain HTTP
#
# `ElasticsearchConfiguration.config` is not here: the composer rewrites it after
# the sync (`ComposerService._write_shared_search_config`), so a policy never
# decides its content.
_ES_CONNECTION_REQUIRED = {
    "active": 'B"true"',
    "connectionId": '"REMOTE"',
    "httpSSLEnabled": 'B"false"',
    "networkHostAddresses": '["http://liferay-search-global:9200"]',
}

REQUIRED_CONFIG_KEYS: dict[str, dict[str, str]] = {
    f"com.liferay.portal.search.elasticsearch{n}.configuration."
    "ElasticsearchConnectionConfiguration.config": _ES_CONNECTION_REQUIRED
    for n in (7, 8)
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_checksums(root: Path) -> dict[str, str]:
    """The recorded checksums, or `{}` when absent or unreadable.

    An unreadable record degrades to "no checksum", which the sync treats as
    an old-format entry: overwritten with a warning, never silently protected.
    """
    path = Path(root) / CHECKSUMS_RELPATH
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    return {}


def save_checksums(root: Path, checksums: dict[str, str]) -> None:
    from ldm_core.utils import safe_write_text

    path = Path(root) / CHECKSUMS_RELPATH
    with contextlib.suppress(PermissionError, OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_text(path, json.dumps(dict(sorted(checksums.items())), indent=2))


def resolve_policy(project_meta, defaults) -> tuple[str, str | None]:
    """The policy for one project: its own `meta` value, else the defaults cascade.

    Returns `(policy, rejected)`. `rejected` is an unrecognised configured value,
    which falls back to the default rather than guessing what was meant.
    """
    raw = None
    if isinstance(project_meta, dict):
        raw = project_meta.get(POLICY_KEY)
    if raw in (None, "") and defaults is not None:
        raw = defaults.get(POLICY_KEY, DEFAULT_POLICY)
    value = str(raw or DEFAULT_POLICY).strip().lower()
    if value in POLICIES:
        return value, None
    return DEFAULT_POLICY, str(raw)


def _normalise_value(value: str) -> str:
    """Drops whitespace outside double quotes, so `[ "a" ]` equals `["a"]`."""
    out = []
    in_quotes = False
    escaped = False
    for ch in value:
        if in_quotes:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quotes = False
        elif ch == '"':
            in_quotes = True
            out.append(ch)
        elif not ch.isspace():
            out.append(ch)
    return "".join(out)


def parse_config(text: str) -> dict[str, str]:
    """Key/value pairs of an OSGi `.config` file, values normalised.

    Handles comments, blank lines and `\\` line continuations, which is what
    multi-line arrays use. Not a full typed parser: values are compared as text.
    """
    result: dict[str, str] = {}
    pending = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1]
            continue
        entry = pending + stripped
        pending = ""
        key, sep, value = entry.partition("=")
        if sep:
            result[key.strip()] = _normalise_value(value)
    if pending:
        key, sep, value = pending.partition("=")
        if sep:
            result[key.strip()] = _normalise_value(value)
    return result


def required_key_problems(
    dest_name: str, text: str
) -> list[tuple[str, str, str | None]]:
    """`(key, expected, actual)` for each required key missing or different.

    `actual` is None when the key is absent. Empty for a file with no table entry.
    """
    required = REQUIRED_CONFIG_KEYS.get(dest_name)
    if not required:
        return []
    present = parse_config(text)
    problems = []
    for key, expected in required.items():
        actual = present.get(key)
        if actual is None or actual != _normalise_value(expected):
            problems.append((key, expected, actual))
    return problems
