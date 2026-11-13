#!/usr/bin/env python3
"""Assert an imported .ldmp did not carry the publisher's rendered environment.

LDM-#2372, guarding LDM-#2370. `ldm snapshot` / `ldm package` built a package's
`custom_env` from the publisher's RENDERED `docker-compose.yml`: every
`LIFERAY_*` entry of the liferay service, including the ones LDM itself wrote
for the publisher's machine -- the default company's virtual host, the
display-node switch, `LIFERAY_LOG4J2_CONFIGURATION_FILE`, the upgrade flag. On
import that `custom_env` is replayed as if the publisher had declared it, and
AICA v3.3.59 shipped a package whose virtual host moved every consumer's
default company to the CI build machine's host name.

The E2E `.ldmp` round trip could not see this: source and copy both run on
`localhost`, so a leaked virtual host has the value the consumer would have
rendered anyway. Two things ARE visible whatever the host, and this asserts
both against the imported copy:

1. **No key appears twice in the copy's rendered liferay environment.**
   `composer._build_liferay_service` appends `custom_env` AFTER its own
   entries, so a captured key the composer also renders appears twice.
   `LIFERAY_LOG4J2_CONFIGURATION_FILE` is rendered unconditionally, which makes
   this observable on every round trip.

   It is NOT how the virtual host shows up, and that is why check 2 exists.
   Since LDM-#2279 the run pipeline's own environment -- where the virtual-host
   pair is rendered (`pipelines/run.py`) -- is merged in only for keys the
   service does not already carry. A leaked virtual host therefore REPLACES
   LDM's silently rather than duplicating it.

2. **The copy's `meta` `custom_env` carries none of the keys LDM renders.**
   That is the field the package delivered, and the only place a leaked
   virtual host is still visible on a localhost round trip.

3. **The PACKAGE's own `meta` `custom_env` carries none of them either**
   (`--package`). Checks 1 and 2 read the copy, AFTER restore. The #2370 fix
   also strips these keys on restore, for packages already published, so on
   the copy a clean package and a dirty one cleaned on import look identical
   and the capture side would have no coverage at all. `cmd_package` writes
   the snapshot directory into a `w:gz` tar with `arcname=item.name`, so
   `meta` is a top-level member; it is read by STREAMING the archive (`r|gz`),
   stopping at that member and extracting nothing to disk. The package is
   ~1 GB, and a random-access open would have to index all of it first.

Shared by `verify_e2e_refactor.sh` and `verify_e2e_refactor.ps1`, following
`check_published_dxp_domain.py`: one implementation cannot drift from itself
(LDM-#1982), and YAML is parsed by PyYAML rather than by a grep in each shell.

Usage:
    check_roundtrip_env_leak.py <project_dir>          # checks 1 and 2
    check_roundtrip_env_leak.py --package <file.ldmp>   # check 3

Exit codes:
    0  neither defect present
    1  a defect is present -- the message names the keys
    4  the artefacts could not be read, so nothing was asserted. The caller
       must treat this, and any other code, as a hard error: after a copy has
       booted its compose file and meta must exist, and a package with no
       top-level `meta` is not one `ldm import` accepts. Not 2, which `python`
       itself returns when it cannot open this script; not 3, which the sibling
       checkers use for a benign skip this check deliberately does not have.
"""

import json
import sys
import tarfile
from pathlib import Path

#: Keys LDM renders into the liferay service itself. A package's `custom_env`
#: carrying any of them is LDM-#2370: something describing the PUBLISHER'S
#: machine, replayed on the consumer's. Each is rendered by LDM, so none can be
#: a legitimate publisher declaration that the round trip's source makes.
LDM_RENDERED_KEYS = (
    # pipelines/run.py, LDM-#2154 -- the pair AICA v3.3.59 shipped.
    "LIFERAY_COMPANY_PERIOD_DEFAULT_PERIOD_VIRTUAL_PERIOD_HOST_PERIOD_NAME",
    "LIFERAY_COMPANY_PERIOD_DEFAULT_PERIOD_VIRTUAL_PERIOD_HOST_PERIOD_SYNC_PERIOD_ON_PERIOD_STARTUP",
    # pipelines/run.py, rendered for a non-localhost host name.
    "LIFERAY_WEB_PERIOD_SERVER_PERIOD_DISPLAY_PERIOD_NODE_PERIOD_NAME",
    "LIFERAY_REDIRECT_PERIOD_URL_PERIOD_IPS_PERIOD_ALLOWED",
    # pipelines/run.py, `--upgrade-db`.
    "LIFERAY_UPGRADE_PERIOD_DATABASE_PERIOD_AUTO_PERIOD_RUN",
    # handlers/composer.py, unconditional -- present in every rendered compose,
    # so it is the key a polluted package is certain to carry.
    "LIFERAY_LOG4J2_CONFIGURATION_FILE",
    # handlers/composer.py: reindex, clustering.
    "LIFERAY_INDEX_PERIOD_ON_PERIOD_STARTUP",
    "LIFERAY_INDEX_PERIOD_ON_PERIOD_STARTUP_PERIOD_DELAY",
    "LIFERAY_CLUSTER_PERIOD_LINK_PERIOD_ENABLED",
    "LIFERAY_LUCENE_PERIOD_REPLICATE_PERIOD_WRITE",
)

#: Prefixes LDM renders whole families under: the search block (current and
#: older forms) and the per-client-extension routes. Kept in step with
#: `LDM_RENDERED_ENV_KEYS` / `LDM_RENDERED_ENV_PREFIXES` in
#: `ldm_core/handlers/snapshot.py`; this script ships in the verification
#: bundle and runs against a binary, so it cannot import them.
LDM_RENDERED_PREFIXES = (
    "LIFERAY_ELASTICSEARCH",
    "LIFERAY_SEARCH_PERIOD_ELASTICSEARCH",
    "LIFERAY_MODULE_PERIOD_FRAMEWORK_PERIOD_PROPERTIES_PERIOD_COM_PERIOD_LIFERAY_PORTAL_PERIOD_SEARCH_PERIOD_ELASTICSEARCH",
    "LIFERAY_ROUTES_",
)

CHECK_FAILED, CANNOT_READ = 1, 4


class ArtefactError(Exception):
    """The compose file or meta could not be read into the expected shape."""


def liferay_environment(project_dir: Path) -> list[str]:
    """The liferay service's environment list from the rendered compose file."""
    import yaml  # PyYAML: installed into the suite's venv by both halves.

    compose_path = project_dir / "docker-compose.yml"
    try:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ArtefactError(f"cannot read {compose_path}: {e}") from e
    service = ((compose or {}).get("services") or {}).get("liferay")
    if not isinstance(service, dict):
        raise ArtefactError(f"{compose_path} defines no 'liferay' service")
    env = service.get("environment")
    if not isinstance(env, list):
        # A mapping cannot hold a duplicate once parsed -- the parser keeps the
        # last -- so a mapping would make check 1 vacuous rather than passing.
        raise ArtefactError(
            f"the liferay service's environment in {compose_path} is "
            f"{type(env).__name__}, not the list LDM renders; duplicates "
            "cannot be counted"
        )
    return [str(entry) for entry in env]


def duplicated_keys(env: list[str]) -> dict[str, int]:
    """Every `KEY` occurring more than once in a `KEY=value` list, with its count."""
    counts: dict[str, int] = {}
    for entry in env:
        key = entry.split("=", 1)[0].strip()
        counts[key] = counts.get(key, 0) + 1
    return {key: n for key, n in counts.items() if n > 1}


def meta_custom_env(project_dir: Path) -> dict:
    """The copy's `custom_env`, in whichever shape it was stored.

    LDM-#2315: a snapshot's `meta` holds it as a JSON STRING inside the meta
    JSON; `ldm config env` writes a dict; absent or empty means none. The
    composer also accepts a legacy `K=V,K=V` string, so this does too.
    """
    meta_path = project_dir / "meta"
    try:
        text = meta_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ArtefactError(f"cannot read {meta_path}: {e}") from e
    return custom_env_from_meta_text(text, str(meta_path))


def package_custom_env(package: Path) -> dict:
    """The `custom_env` of the package's own top-level `meta` member.

    Streamed (`r|gz`): members are visited in archive order and the read
    stops at `meta`, wherever it sits. Nothing is extracted to disk -- the
    member is read into memory only.
    """
    try:
        with tarfile.open(package, mode="r|gz") as tar:
            for member in tar:
                if member.name.removeprefix("./") != "meta" or not member.isfile():
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    break
                text = handle.read().decode("utf-8")
                return custom_env_from_meta_text(text, f"{package}!meta")
    except (OSError, tarfile.TarError, EOFError, UnicodeDecodeError) as e:
        raise ArtefactError(f"cannot read {package}: {e}") from e
    raise ArtefactError(
        f"{package} has no top-level 'meta' member, so it is not a package "
        "'ldm import' accepts and there is no custom_env to assert"
    )


def custom_env_from_meta_text(text: str, where: str) -> dict:
    """Parses a meta file's text and returns its `custom_env` as a dict."""
    try:
        meta = json.loads(text)
    except ValueError as e:
        raise ArtefactError(f"cannot parse {where}: {e}") from e
    if not isinstance(meta, dict):
        raise ArtefactError(f"{where} is not a JSON object")

    raw = meta.get("custom_env")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        legacy = {}
        for pair in raw.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                legacy[key.strip()] = value.strip()
        return legacy
    raise ArtefactError(
        f"custom_env in {where} is {type(raw).__name__}, not a dict or string"
    )


def judge(env: list[str], custom_env: dict) -> tuple[int, list[str]]:
    """The verdict on both checks; names every offending key, never values."""
    lines = []
    failed = False

    dupes = duplicated_keys(env)
    if dupes:
        failed = True
        lines.append(
            "ERROR: the imported copy's liferay environment declares a key more "
            "than once (LDM-#2370). The package's custom_env is appended after "
            "LDM's own entries, so the publisher's value wins:"
        )
        lines.extend(f"     {key} (x{n})" for key, n in sorted(dupes.items()))
    else:
        lines.append(
            f"ASSERTED: no key is duplicated in the copy's liferay environment "
            f"({len(env)} entries)."
        )

    leaked, leak_lines = judge_custom_env(custom_env, "the copy's")
    lines.extend(leak_lines)

    return (CHECK_FAILED if failed or leaked else 0), lines


def judge_custom_env(custom_env: dict, whose: str) -> tuple[bool, list[str]]:
    """Whether `custom_env` carries an LDM-rendered key, and the lines saying so."""
    leaked = sorted(
        key
        for key in custom_env
        if key in LDM_RENDERED_KEYS or str(key).startswith(LDM_RENDERED_PREFIXES)
    )
    if leaked:
        return True, [
            f"ERROR: {whose} custom_env carries keys LDM renders itself "
            "(LDM-#2370). They describe the publisher's machine:",
            *(f"     {key}" for key in leaked),
        ]
    return False, [
        f"ASSERTED: {whose} custom_env carries none of LDM's rendered keys "
        f"({len(custom_env)} declared)."
    ]


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--package":
        try:
            custom_env = package_custom_env(Path(argv[2]))
        except ArtefactError as e:
            print(f"ERROR: nothing was asserted -- {e}")
            return CANNOT_READ
        leaked, lines = judge_custom_env(custom_env, "the package's")
        for line in lines:
            print(line)
        return CHECK_FAILED if leaked else 0

    if len(argv) != 2:
        print(
            "usage: check_roundtrip_env_leak.py <project_dir> | --package <file.ldmp>"
        )
        return 2
    project_dir = Path(argv[1])
    try:
        env = liferay_environment(project_dir)
        custom_env = meta_custom_env(project_dir)
    except ArtefactError as e:
        print(f"ERROR: nothing was asserted -- {e}")
        return CANNOT_READ

    code, lines = judge(env, custom_env)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
