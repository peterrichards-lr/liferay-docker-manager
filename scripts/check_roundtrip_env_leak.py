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

4. **The PACKAGE carries none of LDM's own portal-workaround bundles**
   (`--package-bundles`, LDM-#2359). `ldm run` places
   `com.liferay.custom.dxp.routes.domain.jar` in the project's `osgi/modules`,
   and a snapshot archived the whole `osgi` tree, so v2.26.9's `ldm package`
   shipped it inside AICA's `.ldmp` and AICA's release gate refused it as
   "shipped but not built". Asserted on both places a package records it:
   the `osgi/modules` members of the nested `files.tar.gz` payload, and the
   `osgi_modules` list in the top-level `meta`. Both are read in ONE streamed
   pass that stops as soon as both are found: the payload is only read up to
   the end of its `osgi` block, which `tar.add` writes contiguously.

   The assertion is only meaningful when the SOURCE project held the bundle
   at packaging time -- `sync_workarounds` installs it only for tags inside
   the workaround's window. When it did not, a clean package proves nothing,
   and this reports SKIPPED (exit 3) rather than a pass. A bundle found in the
   package is a failure either way.

5. **A stateless package is stateless, and a stateful one is not**
   (`--stateless-package`, `--stateful-manifest`, LDM-#2493). `ldm package
   --stateless` declares `package_kind: stateless` in `meta` and carries the
   project's assets only. Asserted on the stateless `.ldmp`, in one streamed
   pass that extracts nothing to disk:

   * `meta` declares `package_kind` exactly `stateless`, and does not claim a
     database, volume assets or a search snapshot;
   * the top level holds only `meta`, `files.tar.gz` and its `.sha256` -- no
     `database.sql`, which is what a stateful package's database dump is;
   * every member of the nested `files.tar.gz` lies inside `STATELESS_TREES`
     or is one of the `STATELESS_GLOBS` files (`ldm_core/snapshot/archive.py`),
     so no `data/`, `osgi/state`, `routes/`, `logs/`, `search_backup` or
     generated `files/` -- the trees a snapshot's payload adds.

   The stateful package from the same run is read too, so a regression that
   makes EVERY package stateless fails rather than passing check 5 vacuously:
   its `package_kind` must not be `stateless`. That mode also asserts the
   manifest carries the `container_hostname` KEY (LDM-#2445). Presence only:
   the value is null for a project with none configured, and applying it on
   import is unit-tested.

Usage:
    check_roundtrip_env_leak.py <project_dir>          # checks 1 and 2
    check_roundtrip_env_leak.py --package <file.ldmp>   # check 3
    check_roundtrip_env_leak.py --package-bundles <file.ldmp> <source_project_dir>
                                                        # check 4
    check_roundtrip_env_leak.py --stateless-package <stateless.ldmp>
    check_roundtrip_env_leak.py --stateful-manifest <stateful.ldmp>
                                                        # check 5

Exit codes:
    0  neither defect present
    1  a defect is present -- the message names the keys
    3  check 4 only: the source project never held an LDM bundle, so a clean
       package proves nothing. The CALLER must announce this as SKIPPED,
       never as a pass. Checks 1-3 have no skip and never return it.
    4  the artefacts could not be read, so nothing was asserted. The caller
       must treat this, and any other code, as a hard error: after a copy has
       booted its compose file and meta must exist, and a package with no
       top-level `meta` is not one `ldm import` accepts. Not 2, which `python`
       itself returns when it cannot open this script; not 3, which the sibling
       checkers -- and check 4 -- use for a benign skip.
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

#: LDM's own portal-workaround bundles (LDM-#2359): every `Workaround.prefix`
#: and `RETIRED_PREFIXES` entry in `ldm_core/runtime/portal_workarounds.py`.
#: Kept in step by hand for the same reason as the lists above -- this script
#: ships in the verification bundle and runs against a binary.
LDM_BUNDLE_PREFIXES = ("com.liferay.custom.dxp.routes.domain",)

#: LDM-#2493: `STATELESS_TREES` and `STATELESS_GLOBS` in
#: `ldm_core/snapshot/archive.py` -- everything `write_stateless_payload` may
#: put in a stateless package's `files.tar.gz`. Kept in step by hand (this
#: script ships in the verification bundle and cannot import ldm_core); a unit
#: test compares them.
STATELESS_PAYLOAD_TREES = (
    "deploy",
    "configs",
    "osgi/configs",
    "osgi/client-extensions",
    "osgi/modules",
    "scripts",
    "portal-patches",
    "client-extensions",
)
STATELESS_PAYLOAD_FILES = (
    "files/portal-ext.properties",
    ".ldm/fragment-overrides.json",
)

#: The top-level members a stateless `.ldmp` holds: the staging directory's
#: `meta` and `files.tar.gz`, plus the checksum `cmd_package` adds. A stateful
#: package also carries `database.sql`, the database dump.
STATELESS_TOP_LEVEL = ("meta", "files.tar.gz", "files.tar.gz.sha256")

CHECK_FAILED, SKIPPED, CANNOT_READ = 1, 3, 4


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


def is_ldm_bundle(name: str) -> bool:
    """Whether a bare file name is one of LDM's workaround jars (LDM-#2359)."""
    return name.endswith(".jar") and name.startswith(LDM_BUNDLE_PREFIXES)


def source_bundles(project_dir: Path) -> list[str]:
    """LDM bundles in the source project's `osgi/modules`, by bare name."""
    if not project_dir.is_dir():
        raise ArtefactError(f"the source project {project_dir} does not exist")
    modules = project_dir / "osgi" / "modules"
    try:
        names = [p.name for p in modules.iterdir()] if modules.is_dir() else []
    except OSError as e:
        raise ArtefactError(f"cannot list {modules}: {e}") from e
    return sorted(n for n in names if is_ldm_bundle(n))


def _payload_bundles(handle) -> list[str]:
    """LDM bundles among the `osgi/modules` members of a streamed payload.

    Stops at the first member after the `osgi` block: `tar.add` recurses a
    directory contiguously, so nothing under `osgi/modules` follows it.
    """
    found = []
    in_osgi = False
    with tarfile.open(fileobj=handle, mode="r|gz") as payload:
        for member in payload:
            name = member.name.removeprefix("./")
            if name == "osgi" or name.startswith("osgi/"):
                in_osgi = True
                parent, _, base = name.rpartition("/")
                if parent == "osgi/modules" and is_ldm_bundle(base):
                    found.append(base)
            elif in_osgi:
                break
    return sorted(found)


def package_bundles(package: Path) -> tuple[list[str], list[str]]:
    """LDM bundles in the package's payload, and in its manifest's list.

    One streamed pass over the `.ldmp`, ending as soon as both the top-level
    `meta` and `files.tar.gz` have been read; nothing is extracted to disk.
    """
    payload: list[str] | None = None
    manifest: list[str] | None = None
    try:
        with tarfile.open(package, mode="r|gz") as tar:
            for member in tar:
                name = member.name.removeprefix("./")
                if not member.isfile() or name not in ("meta", "files.tar.gz"):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                if name == "files.tar.gz":
                    payload = _payload_bundles(handle)
                else:
                    meta = json.loads(handle.read().decode("utf-8"))
                    listed = (
                        meta.get("osgi_modules") if isinstance(meta, dict) else None
                    )
                    manifest = sorted(
                        n.strip()
                        for n in str(listed or "").split(",")
                        if is_ldm_bundle(n.strip())
                    )
                if payload is not None and manifest is not None:
                    break
    except (OSError, tarfile.TarError, EOFError, UnicodeDecodeError, ValueError) as e:
        raise ArtefactError(f"cannot read {package}: {e}") from e
    if manifest is None:
        raise ArtefactError(f"{package} has no top-level 'meta' member")
    if payload is None:
        raise ArtefactError(
            f"{package} has no top-level 'files.tar.gz' payload, so there is no "
            "osgi tree to assert"
        )
    return payload, manifest


def judge_bundles(
    payload: list[str], manifest: list[str], in_source: list[str]
) -> tuple[int, list[str]]:
    """The check 4 verdict: 0 asserted, 1 shipped, 3 nothing to assert."""
    if payload or manifest:
        lines = [
            "ERROR: the package carries LDM's own portal-workaround bundle "
            "(LDM-#2359). It is internal to LDM and pinned to the publisher's "
            "Liferay line, and consumers that check package contents refuse it:"
        ]
        lines.extend(f"     files.tar.gz!osgi/modules/{n}" for n in payload)
        lines.extend(f"     meta osgi_modules: {n}" for n in manifest)
        return CHECK_FAILED, lines
    if not in_source:
        return SKIPPED, [
            "SKIPPED (not run): the source project's osgi/modules held no LDM "
            "bundle when it was packaged -- its tag is outside every "
            "workaround's window -- so a package without one proves nothing "
            "about LDM-#2359."
        ]
    return 0, [
        f"ASSERTED: the source project held {', '.join(in_source)} and the "
        "package carries it neither in files.tar.gz nor in meta osgi_modules."
    ]


def main_package_bundles(package: Path, source: Path) -> int:
    """Check 4 (`--package-bundles`): the exit code is the verdict."""
    try:
        in_source = source_bundles(source)
        payload, manifest = package_bundles(package)
    except ArtefactError as e:
        print(f"ERROR: nothing was asserted -- {e}")
        return CANNOT_READ
    code, lines = judge_bundles(payload, manifest, in_source)
    for line in lines:
        print(line)
    return code


def _read_meta_member(handle, where: str) -> dict:
    """A streamed `meta` member parsed into a dict."""
    try:
        meta = json.loads(handle.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ArtefactError(f"cannot parse {where}: {e}") from e
    if not isinstance(meta, dict):
        raise ArtefactError(f"{where} is not a JSON object")
    return meta


def package_meta(package: Path) -> dict:
    """The package's top-level `meta`, streamed; nothing is extracted."""
    try:
        with tarfile.open(package, mode="r|gz") as tar:
            for member in tar:
                if member.name.removeprefix("./") != "meta" or not member.isfile():
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    break
                return _read_meta_member(handle, f"{package}!meta")
    except (OSError, tarfile.TarError, EOFError) as e:
        raise ArtefactError(f"cannot read {package}: {e}") from e
    raise ArtefactError(f"{package} has no top-level 'meta' member")


def _is_kind_stateless(meta: dict) -> bool:
    """As `ldm_core/snapshot/package_kind.py` reads it: only `stateless`."""
    return str(meta.get("package_kind") or "").strip().lower() == "stateless"


def _in_stateless_payload(name: str) -> bool:
    """Whether a payload member belongs to what a stateless package ships.

    A parent directory of an allowed tree or file (`osgi`, `files`, `.ldm`)
    counts, since `tar.add` may write one; anything below it does not.
    """
    allowed = (*STATELESS_PAYLOAD_TREES, *STATELESS_PAYLOAD_FILES)
    if any(a.startswith(f"{name}/") for a in allowed):
        return True
    if name in STATELESS_PAYLOAD_FILES:
        return True
    return any(name == t or name.startswith(f"{t}/") for t in STATELESS_PAYLOAD_TREES)


def stateless_package_contents(package: Path) -> tuple[list[str], dict, list[str]]:
    """Top-level names, `meta`, and payload member names of a whole `.ldmp`.

    One streamed pass, read to the end: a stateless package is small, and
    every top-level member must be seen to assert none is a database dump.
    Nothing is extracted to disk.
    """
    top: list[str] = []
    meta: dict | None = None
    payload: list[str] | None = None
    try:
        with tarfile.open(package, mode="r|gz") as tar:
            for member in tar:
                name = member.name.removeprefix("./")
                top.append(name)
                if not member.isfile() or name not in ("meta", "files.tar.gz"):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                if name == "meta":
                    meta = _read_meta_member(handle, f"{package}!meta")
                else:
                    with tarfile.open(fileobj=handle, mode="r|gz") as inner:
                        payload = [m.name.removeprefix("./") for m in inner]
    except (OSError, tarfile.TarError, EOFError) as e:
        raise ArtefactError(f"cannot read {package}: {e}") from e
    if meta is None:
        raise ArtefactError(f"{package} has no top-level 'meta' member")
    if payload is None:
        raise ArtefactError(f"{package} has no top-level 'files.tar.gz' payload")
    return top, meta, payload


def judge_stateless(
    top: list[str], meta: dict, payload: list[str]
) -> tuple[int, list[str]]:
    """Check 5 on the stateless package: 0 asserted, 1 a defect."""
    errors = []
    if meta.get("package_kind") != "stateless":
        errors.append(
            f"meta package_kind is {meta.get('package_kind')!r}, not 'stateless'"
        )
    for key in ("includes_database", "includes_volume_assets"):
        if str(meta.get(key) or "").lower() == "true":
            errors.append(f"meta claims {key}: true")
    if meta.get("search_snapshot"):
        errors.append("meta names a search_snapshot")
    errors.extend(
        f"top-level member {n!r} is not one a stateless package holds"
        + (" -- it is a database dump" if n.endswith(".sql") else "")
        for n in sorted(set(top) - set(STATELESS_TOP_LEVEL))
    )
    errors.extend(
        f"files.tar.gz!{n} is outside the stateless trees (snapshot state)"
        for n in sorted({n for n in payload if not _in_stateless_payload(n)})
    )
    if errors:
        return CHECK_FAILED, [
            "ERROR: the --stateless package is not stateless (LDM-#2493):",
            *(f"     {e}" for e in errors),
        ]
    return 0, [
        "ASSERTED: the --stateless package declares package_kind 'stateless' "
        "and carries no database dump, data/, osgi/state or search backup "
        f"({len(top)} top-level members, {len(payload)} payload members)."
    ]


def judge_stateful_manifest(meta: dict) -> tuple[int, list[str]]:
    """Check 5 on the stateful package's manifest, plus LDM-#2445's key."""
    errors = []
    if _is_kind_stateless(meta):
        errors.append(
            "the stateful package declares package_kind 'stateless', so it "
            "would import as assets only and restore nothing (LDM-#2493)"
        )
    # LDM-#2445: presence of the KEY only. The value is null for a project
    # with no container hostname; applying it on import is unit-tested.
    if "container_hostname" not in meta:
        errors.append("meta has no container_hostname key (LDM-#2445)")
    if errors:
        return CHECK_FAILED, [
            "ERROR: the stateful package's manifest is wrong:",
            *(f"     {e}" for e in errors),
        ]
    return 0, [
        "ASSERTED: the stateful package is not stateless (package_kind "
        f"{meta.get('package_kind')!r}) and its manifest carries the "
        "container_hostname key."
    ]


def main_package_kind(mode: str, package: Path) -> int:
    """Check 5: `--stateless-package` or `--stateful-manifest`."""
    try:
        if mode == "--stateless-package":
            code, lines = judge_stateless(*stateless_package_contents(package))
        else:
            code, lines = judge_stateful_manifest(package_meta(package))
    except ArtefactError as e:
        print(f"ERROR: nothing was asserted -- {e}")
        return CANNOT_READ
    for line in lines:
        print(line)
    return code


def main(argv: list[str]) -> int:  # noqa: PLR0911 -- one return per mode
    if len(argv) == 4 and argv[1] == "--package-bundles":
        return main_package_bundles(Path(argv[2]), Path(argv[3]))

    if len(argv) == 3 and argv[1] in ("--stateless-package", "--stateful-manifest"):
        return main_package_kind(argv[1], Path(argv[2]))

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
            "usage: check_roundtrip_env_leak.py <project_dir> | --package "
            "<file.ldmp> | --package-bundles <file.ldmp> <source_project_dir> | "
            "--stateless-package <file.ldmp> | --stateful-manifest <file.ldmp>"
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
