"""Portal JAR patch overlay for `myproject/portal-patches/` (LDM-#1264).

Liferay's `/opt/liferay/osgi/portal` holds ~1,420 core JARs. Patching one used
to mean `docker cp`-ing it into a running container by hand, which is lost on
every container recreate and invisible to anyone reading the project.

This module manages patches declared in the workspace instead. Two design
decisions are load-bearing and were both settled empirically:

**Copy, not bind-mount.** A directory bind-mount onto `osgi/portal` masks all
~1,420 core JARs and Liferay does not boot. Per-file bind-mounts avoid that but
reintroduce the container-UID ownership problems of LDM-#1255 on Linux/WSL, need
a compose regeneration per patch, and break if a host file is deleted while the
container runs. Copying adds files *into* the directory, which is exactly why the
manual `docker cp` workaround worked.

**Version policy.** A patch built for one Liferay release silently masking a core
JAR in another is the hazard the copy approach shares with a mount. Each patch
therefore carries a sidecar manifest recording the release it was introduced
against, and a mismatch is graded rather than ignored.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ldm_core.ui import UI

PATCH_DIR_NAME = "portal-patches"
CONTAINER_PORTAL_DIR = "/opt/liferay/osgi/portal"

# `YYYY.qN.P` -- the quarterly release line (e.g. 2026.q1.12-lts).
_QUARTERLY = re.compile(r"^(\d{4})\.q(\d+)\.(\d+)")

# Outcome tiers, ordered by severity.
OK = "ok"
WARN = "warn"
ABORT = "abort"


def _is_quarterly(tag):
    return bool(_QUARTERLY.match(str(tag or "")))


def _parseable(tag, parse_version):
    """A tag is usable only if it yields version components.

    `parse_version("nightly")` returns `()`, and in Python `() < (7, 4, 13)` is
    True -- so an unparseable tag silently compares as older than everything.
    Parseability is therefore checked *before* any comparison, never after.
    """
    return bool(parse_version(tag))


def classify_version_change(  # noqa: PLR0911
    introduced_in, current_tag, parse_version, max_version=None
):
    """Grades a patch's recorded release against the release now being booted.

    Returns ``(tier, reason)`` where tier is :data:`OK`, :data:`WARN` or
    :data:`ABORT`.

    The routing order matters and is fail-closed at every branch:

    1. **Parseability.** Either side unparseable (``nightly``, ``latest``, empty)
       means the tier cannot be determined, so it is treated as ``ABORT``.
       Rolling tags are precisely where a stale core JAR is most likely wrong.
    2. **Explicit ceiling.** ``current > max_version`` is ``ABORT`` regardless of
       tier -- the developer stated a known-good upper bound.
    3. **Format discrimination.** Quarterly and legacy (``7.4.x-uN``) lines have
       different component meanings, so a positional rule cannot serve both:
       index 1 is the *quarter* in one and the *minor* version in the other.
       A mixed comparison carries no meaningful tier and is ``ABORT``.
    4. **Within a line.** A change above the patch component is ``ABORT`` (OSGi
       core contracts routinely break across quarterly releases); a change in the
       patch component alone is ``WARN``.
    """
    if not _parseable(introduced_in, parse_version) or not _parseable(
        current_tag, parse_version
    ):
        return ABORT, (
            f"cannot compare '{introduced_in}' with '{current_tag}' -- "
            "a rolling or unparseable tag gives no safe basis for comparison"
        )

    if max_version:
        if not _parseable(max_version, parse_version):
            return ABORT, f"max_version '{max_version}' is not a valid version"
        if parse_version(current_tag) > parse_version(max_version):
            return ABORT, (
                f"{current_tag} is beyond the declared max_version {max_version}"
            )

    intro_q, cur_q = _is_quarterly(introduced_in), _is_quarterly(current_tag)
    if intro_q != cur_q:
        return ABORT, (
            f"'{introduced_in}' and '{current_tag}' are different release lines "
            "(quarterly vs legacy) and cannot be meaningfully compared"
        )

    intro, cur = parse_version(introduced_in), parse_version(current_tag)
    if intro == cur:
        return OK, ""

    # Compare everything above the patch component. For quarterly that is
    # (year, quarter); for legacy (7.4.13-u108) that is (major, minor, patch),
    # leaving the update component as the tolerated difference.
    depth = 2 if intro_q else 3
    if intro[:depth] != cur[:depth]:
        return ABORT, (
            f"patch was introduced against {introduced_in} but this project "
            f"now runs {current_tag} -- a release-line change, where OSGi core "
            "contracts routinely break"
        )

    return WARN, (
        f"patch was introduced against {introduced_in}, project now runs {current_tag}"
    )


def patch_dir(root):
    """Returns the project's patch directory (which need not exist)."""
    return Path(root) / PATCH_DIR_NAME


def sidecar_path(jar_path):
    """Returns the manifest path for a patch JAR."""
    return Path(str(jar_path) + ".json")


def load_or_create_sidecar(jar_path, current_tag):
    """Reads a patch's manifest, creating it on first sight.

    Orphaned sidecars are **never** deleted when a JAR is removed. A developer
    temporarily pulling a JAR out to check whether a bug still reproduces is
    routine; pruning the manifest would mean the JAR silently re-attaches with
    ``introduced_in`` reset to whatever release is current, disarming the very
    guard this exists to provide -- and losing the JIRA reference with it.
    """
    path = sidecar_path(jar_path)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")), False
        except (OSError, ValueError) as e:
            UI.warning(f"Could not read {path.name} ({e}); treating as unknown origin.")
            # Fail closed: an unreadable manifest must not be silently replaced
            # with one claiming the current release.
            return {"introduced_in": None, "jira": "", "unreadable": True}, False

    manifest = {
        "jira": "",
        "introduced_in": current_tag,
        "max_version": None,
        "fail_on_mismatch": False,
    }
    try:
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        UI.warning(
            f'Created {path.name} with "introduced_in": "{current_tag}". '
            "Verify this matches the Liferay version the patch was compiled "
            "against -- it records when LDM first saw the file, not what it "
            "was built for."
        )
    except OSError as e:
        UI.warning(f"Could not write {path.name}: {e}")
    return manifest, True


def discover_patches(root, current_tag):
    """Returns ``[(jar_path, manifest)]`` for every JAR in `portal-patches/`."""
    directory = patch_dir(root)
    if not directory.is_dir():
        return []

    found = []
    for jar in sorted(directory.glob("*.jar")):
        manifest, _created = load_or_create_sidecar(jar, current_tag)
        found.append((jar, manifest))
    return found


def _tier_for(jar, manifest, current_tag, parse_version):
    """Grades one patch, treating an unreadable manifest as unknown origin."""
    if manifest.get("unreadable"):
        return ABORT, f"{jar.name}: manifest unreadable, origin unknown"
    tier, reason = classify_version_change(
        manifest.get("introduced_in"),
        current_tag,
        parse_version,
        manifest.get("max_version"),
    )
    return tier, f"{jar.name}: {reason}" if reason else ""


def plan_patches(manager, root, current_tag, force=False):
    """Decides whether patches apply, enforcing the version policy.

    Returns the list of ``(jar, manifest)`` to copy, or ``[]`` when there is
    nothing to do. Aborts the run on a release-line mismatch unless `force`.
    """
    patches = discover_patches(root, current_tag)
    if not patches:
        return []

    parse_version = manager.parse_version
    aborts, warns = [], []
    for jar, manifest in patches:
        tier, reason = _tier_for(jar, manifest, current_tag, parse_version)
        if tier == ABORT:
            aborts.append(reason)
        elif tier == WARN:
            warns.append((reason, manifest))

    if aborts:
        for reason in aborts:
            UI.error(f"  {reason}")
        if not force:
            UI.die(
                f"{len(aborts)} portal patch(es) were built against a different "
                "Liferay release line. Re-patch against the current release, or "
                "use '--force-portal-patches' to apply them anyway.",
                exit_code=1,
            )
        UI.warning("--force-portal-patches: applying despite the mismatch above.")

    if warns:
        strict = _strict_mode()
        for reason, manifest in warns:
            UI.warning(f"  {reason}")
            if manifest.get("fail_on_mismatch"):
                strict = True
        if strict and not force:
            UI.die(
                "Stale portal patches detected and strict mode is enabled "
                "(fail_on_mismatch / LDM_FAIL_ON_STALE_PATCHES). Use "
                "'--force-portal-patches' to override.",
                exit_code=1,
            )
        # Interactive only -- returns immediately under -y/CI, where the
        # warnings above are the record instead.
        UI.interruptible_pause(5, "Press CTRL+C to cancel ")

    UI.detail(f"Applying {len(patches)} portal patch(es) from {PATCH_DIR_NAME}/.")
    return patches


def _strict_mode():
    return os.environ.get("LDM_FAIL_ON_STALE_PATCHES") == "1"


@contextlib.contextmanager
def _world_readable(jar):
    """Yields a path to `jar` that is guaranteed readable inside the container.

    `docker cp` preserves the host file's mode and stamps the host UID onto the
    result, so a patch JAR that happens to be mode 600 on the host lands as
    `-rw------- 501 root` next to its `-rw-r--r-- liferay liferay` neighbours.
    Liferay runs as uid 1000 and simply cannot read it -- OSGi then fails to
    resolve that one bundle while the container still boots, which is precisely
    the silent-failure mode this feature exists to remove. Observed directly
    against liferay/dxp:2026.q1.12-lts.

    The container is not running at copy time (that is the whole point of the
    create -> cp -> start ordering), so `docker exec ... chown` is unavailable
    and chowning to uid 1000 on the host would need root. Normalising the *mode*
    is enough: portal JARs are only ever read.

    The common case -- an already-readable JAR -- is yielded untouched.
    """
    if jar.stat().st_mode & 0o044 == 0o044:
        yield jar
        return

    UI.detail(f"Staging {jar.name} with readable permissions for the container.")
    tmp_dir = tempfile.mkdtemp(prefix="ldm-portal-patch-")
    try:
        staged = Path(tmp_dir) / jar.name
        shutil.copyfile(jar, staged)
        staged.chmod(0o644)
        yield staged
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def copy_patches_into(manager, patches, container_name, docker_prefix, force=False):
    """Copies patch JARs into a *created* container's osgi/portal directory.

    Must run between `compose create` and `compose start`: OSGi resolves bundles
    at boot, so copying into an already-running container would need a second
    restart and would briefly run the unpatched JAR.

    Each JAR is first probed for upstream existence. `docker cp` out of a created
    container fails for a missing path, which makes the check free -- and a patch
    whose target was *removed* upstream is a sharper problem than a merely stale
    one, since copying it would reintroduce a file the new release deliberately
    dropped.
    """
    copied = 0
    for jar, _manifest in patches:
        target = f"{CONTAINER_PORTAL_DIR}/{jar.name}"
        # `cp ... -` streams a tar of the file to stdout, which for a real JAR
        # is megabytes of binary we have no use for -- it is discarded rather
        # than captured. The result is `""` when the path exists and `None`
        # when it does not, so the `is None` test below is load-bearing: a
        # truthiness check would read every successful probe as a failure.
        probe = manager.run_command(
            [*docker_prefix, "cp", f"{container_name}:{target}", "-"],
            check=False,
            capture_output=False,
            stdout_file=subprocess.DEVNULL,
        )
        if probe is None:
            msg = (
                f"{jar.name} does not exist in {CONTAINER_PORTAL_DIR} for this "
                "image -- it may have been renamed or removed upstream"
            )
            if not force:
                UI.die(
                    f"{msg}. Use '--force-portal-patches' to copy it anyway.",
                    exit_code=1,
                )
            UI.warning(f"{msg}; copying anyway (--force-portal-patches).")

        with _world_readable(jar) as source:
            manager.run_command(
                [*docker_prefix, "cp", str(source), f"{container_name}:{target}"],
                check=True,
            )
        copied += 1

    UI.success(f"Applied {copied} portal patch(es) to {CONTAINER_PORTAL_DIR}.")
    return copied


def recreate_with_patches(
    manager, root, meta, compose_base, docker_prefix, service=None, capture=True
):
    """Runs a `--force-recreate` boot with portal patches re-applied.

    Returns ``True`` when it handled the boot, ``False`` when there are no
    patches and the caller should run its own command.

    `ldm start`/`ldm restart` bypass the run pipeline and issue compose commands
    directly. Their plain forms are safe -- `docker cp` writes to the container's
    writable layer, which survives stop/start -- but `--force-recreate` replaces
    the container and would silently drop every patch, leaving a developer
    debugging against a JAR they believe they have replaced.
    """
    force = getattr(getattr(manager, "args", None), "force_portal_patches", False)
    tag = meta.get("tag") if isinstance(meta, dict) else None
    patches = plan_patches(manager, root, tag, force=force)
    if not patches:
        return False

    create_cmd = [*compose_base, "create", "--force-recreate", "--remove-orphans"]
    if service:
        create_cmd.append(service)
    manager.run_command(create_cmd, capture_output=capture, cwd=str(root))

    container = (
        meta.get("container_name") if isinstance(meta, dict) else None
    ) or Path(root).name
    copy_patches_into(manager, patches, container, docker_prefix, force=force)

    start_cmd = [*compose_base, "start"]
    if service:
        start_cmd.append(service)
    manager.run_command(start_cmd, capture_output=capture, cwd=str(root))
    return True
