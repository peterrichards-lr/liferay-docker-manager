import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ldm_core.constants import VERSION
from ldm_core.ui import UI

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# LDM-#1252: when True, every filesystem mutation is logged and skipped.
# This script previously had no argument parsing at all, so `--help` (and any
# typo) fell straight through to a full sync -- archiving reports and rewriting
# the compatibility table. For a tool the release runbook describes as able to
# "discard real test data with no error", asking what it does must be safe.
DRY_RUN = False


def _mutate(description, action):
    """Runs `action`, or logs what would have happened when DRY_RUN is set."""
    if DRY_RUN:
        UI.info(f"[dry-run] would {description}")
        return
    action()


# Paths that scripts/release.py's `--promote` can *only* ever touch when
# bumping a pre-release to stable: docs, version metadata, and the
# release/verification tooling itself -- never anything that ends up
# compiled into the shipped `ldm` binary. Used by _is_metadata_only_diff()
# below to decide whether it's provably safe to relabel a compatibility-table
# entry's pre-release version as its now-stable equivalent (see
# get_promotable_stable_version()).
_METADATA_ONLY_ALLOWLIST = [
    re.compile(r".*\.md$", re.IGNORECASE),
    re.compile(r"^ldm_core/constants\.py$"),
    re.compile(r"^pyproject\.toml$"),
    re.compile(r"^scripts/release\.py$"),
    re.compile(r"^scripts/verify_e2e_refactor\.(sh|ps1)$"),
    re.compile(r"^scripts/(sync_compatibility|check_version_sync)\.py$"),
    re.compile(r"^references/verification-results/"),
    re.compile(r"^lint\.sh$"),
    re.compile(r"^\.gitignore$"),
    re.compile(r"^\.secrets\.baseline$"),
]


def normalize_version(v):
    """Normalizes a version string by stripping leading 'v' and whitespace."""
    return v.lstrip("v").strip()


def _is_metadata_only_diff(old_ref, new_ref="HEAD"):
    """True only if every file changed between old_ref and new_ref matches
    _METADATA_ONLY_ALLOWLIST -- i.e. nothing that ends up in the shipped ldm
    binary moved between them. Fails safe (False) on any git error or an
    empty/missing ref, since relabeling a compatibility-table version is only
    ever safe when we can *prove* nothing functional changed; an inconclusive
    check must never be treated as a green light.
    """
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", old_ref, new_ref],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            check=False,
        )
    except Exception:
        return False
    if res.returncode != 0:
        return False
    changed = [f for f in res.stdout.splitlines() if f.strip()]
    return all(
        any(pattern.match(f) for pattern in _METADATA_ONLY_ALLOWLIST) for f in changed
    )


# LDM-#1058: a changed/added line in the verify scripts' diff that can only
# ever be a comment, the version constant itself, or a plain user-facing
# message string -- never a control-flow or logic line. Deliberately
# conservative: only lines *provably* limited to these shapes count as safe;
# anything else (including a line this pattern simply fails to recognize)
# falls through to "not cosmetic" in _is_verify_script_diff_cosmetic_only.
_VERIFY_SCRIPT_SAFE_LINE = re.compile(
    r"^[+-]\s*(?:"
    r"#.*"  # bash/PowerShell comment
    r"|SCRIPT_VERSION\s*=.*"  # bash: SCRIPT_VERSION="..."
    r"|\$SCRIPT_VERSION\s*=.*"  # PowerShell: $SCRIPT_VERSION = "..."
    r"|(?:echo|Write-Output|Write-Host)\s+[\"'].*[\"']\s*"  # message via echo/Write-*
    r"|[\"'].*[\"'],?"  # bare quoted string (e.g. a PowerShell @() array element,
    # looped over separately with Write-Output/Write-Host rather than called
    # inline -- still just message text, not a control-flow/logic line.
    r")\s*$"
)


def _is_verify_script_diff_cosmetic_only(old_ref, new_ref="HEAD"):
    """True only if every changed line in the verify scripts' own diff
    between old_ref and new_ref is provably cosmetic (a comment, the
    SCRIPT_VERSION/LDM_MAGIC_VERSION line, or a plain message string) --
    never a control-flow or logic line that could change what the script
    actually verifies. Fails safe (False) on any git error, an empty/missing
    ref, or any changed line this can't positively classify as cosmetic.

    Deliberately narrower than _is_metadata_only_diff(): that helper checks
    the *whole repo's* changed-file list against an allowlist, which is the
    right granularity for "did the shipped binary change" but the wrong one
    here -- unrelated functional files change between any two tags in a real
    repo, so what matters is only whether the verify scripts *themselves*
    changed in a way that could affect what they exercise.
    """
    paths = ["scripts/verify_e2e_refactor.sh", "scripts/verify_e2e_refactor.ps1"]
    try:
        res = subprocess.run(
            ["git", "diff", "--unified=0", old_ref, new_ref, "--", *paths],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            check=False,
        )
    except Exception:
        return False
    if res.returncode != 0:
        return False

    for line in res.stdout.splitlines():
        if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
            continue
        if not _VERIFY_SCRIPT_SAFE_LINE.match(line):
            return False
    return True


def get_promotable_stable_version(report_version):
    """Returns the current stable VERSION if report_version is a pre-release
    that's provably safe (see _is_metadata_only_diff) to display as that
    stable version in the compatibility table instead -- otherwise None.

    Checked per-report, against *that report's own* recorded version, rather
    than a single "just promoted from X" pointer: a report may have been
    generated against an earlier pre-release tag in the same cycle (e.g.
    tested at -pre.3, but the cycle continued on to -pre.5 before actually
    promoting) -- what matters for "is this display-safe" is whether anything
    functional changed between *that specific tag* and now, not just since
    the latest pre-release. This also makes the check durable: it's evaluated
    fresh on every sync_compatibility.py run (not just inside `--promote`),
    so a later, unrelated re-sync (e.g. by the scheduled Multi-OS
    verification workflow) reaches the same answer instead of reverting a
    one-shot normalization back to the pre-release label.

    This only ever affects what the generated table *displays*. The
    underlying verification-results/*.txt reports are never rewritten: they
    stay a verbatim, honest record of the exact binary that was actually
    tested, which is also what the staleness check in sync_reports() above
    keys off of. A promote that's provably metadata-only doesn't invalidate
    that record -- the binary that shipped as stable is bit-for-bit what was
    verified as the pre-release -- but if anything functional changed, this
    returns None and the table keeps showing the honest pre-release label
    until a fresh run actually verifies the new stable binary.
    """
    report_version = normalize_version(report_version)
    if "-" in VERSION or "-" not in report_version:
        # Either there's no stable version to normalize towards yet, or
        # report_version is already stable and has nothing to normalize.
        return None
    if report_version.split("-", 1)[0] != VERSION.split("-", 1)[0]:
        # Different base (X.Y.Z) entirely -- e.g. a stale report from a much
        # older cycle. Not a candidate; the ordinary staleness check above
        # already handles archiving those.
        return None

    tag = f"v{report_version}"
    tag_check = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", tag],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        check=False,
    )
    if tag_check.returncode != 0:
        UI.warning(
            f"Tag {tag} not found locally -- skipping compatibility-table "
            "version-label normalization for reports at that version."
        )
        return None

    if not _is_metadata_only_diff(tag):
        UI.warning(
            f"Changes between {tag} and HEAD touch more than docs/version "
            f"metadata -- keeping the honest pre-release label in the "
            f"compatibility table until a fresh verification run confirms v{VERSION}."
        )
        return None

    return VERSION


def strip_ansi(text):
    """Removes ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*[mK]", "", text)


def anonymize_content(content):
    """Redacts sensitive host-specific paths."""
    # Redact HOME paths
    home = str(Path.home())
    content = content.replace(home, "[HOME]")
    # Redact hostname if detected in report headers
    content = re.sub(r"Hostname:\s+[^\n]+", "Hostname:  [ANONYMIZED]", content)
    # Redact binary path
    return re.sub(r"Binary:\s+[^\n]+", "Binary:    [ANONYMIZED]", content)


def get_report_metadata(report_path):  # noqa: C901, PLR0912, PLR0915
    """Parses an LDM E2E verification report and returns metadata."""
    raw_content = report_path.read_text()
    content = strip_ansi(raw_content)

    # 1. Detect Verification Status
    passed = (
        "🎯 ALL E2E VERIFICATIONS PASSED!" in content
        or "ALL E2E VERIFICATIONS PASSED!" in content
    )

    # Robustness check for errors
    lines = content.splitlines()
    for line in lines:
        upper_line = line.upper()
        if ("ERROR:" in upper_line or "FATAL:" in upper_line) and "ℹ" not in line:
            # Ignore Python Tracebacks/Subprocess encoding issues and non-fatal retry warnings that don't block general success
            if (
                "TRACEBACK" in upper_line
                or "EXCEPTION IN THREAD" in upper_line
                or "DECODEERROR" in upper_line
                or "NON-FATAL" in upper_line
            ):
                continue
            passed = False
            break

    status_slug = "pass" if passed else "fail"

    # 2. Extract Timestamp
    ts_match = re.search(r"Timestamp:\s+([^\n]+)", content)
    timestamp_str = ts_match.group(1).strip() if ts_match else ""
    dt = None
    if timestamp_str:
        try:
            # Format: Tue 28 Apr 2026 12:48:13 BST or Tue 28 Apr 12:25:38 BST 2026
            # or (GNU/Linux `date` default, e.g. WSL2/Fedora) Tue Apr 28 12:25:38 BST 2026
            ts_clean = re.sub(r"\b[A-Z]{3,5}\b", "", timestamp_str)
            # Stripping a trailing timezone abbrev (e.g. "... 16:13:49 BST") leaves
            # a trailing space that %S can't absorb -- strptime then rejects the
            # whole string as "unconverted data remains", silently falling through
            # to the mtime fallback below for every non-Windows report. This bit
            # us directly: two verify runs landed within minutes of a git
            # checkout/mv that reset mtimes, and mtime-order silently won out
            # over (broken) header-order, picking the wrong report as "latest".
            ts_clean = re.sub(r"\s+", " ", ts_clean).strip()
            for fmt in [
                "%a %d %b %Y %H:%M:%S",
                "%a %d %b %H:%M:%S %Y",
                "%m/%d/%Y %H:%M:%S",
                "%a %b %d %H:%M:%S %Y",
            ]:
                try:
                    dt = datetime.strptime(ts_clean, fmt)
                    break
                except ValueError:
                    continue
            if not dt:
                dt = datetime.fromtimestamp(report_path.stat().st_mtime)
        except Exception:
            dt = datetime.fromtimestamp(report_path.stat().st_mtime)
    else:
        dt = datetime.fromtimestamp(report_path.stat().st_mtime)

    # 3. Extract Platform/OS info
    platform_match = re.search(r"Platform\s+(?:✅|\[OK\])\s+([^\n]+)", content)
    if not platform_match:
        platform_match = re.search(r"Platform:\s+([^\n]+)", content)

    platform_str = platform_match.group(1).strip() if platform_match else "Unknown"

    # 4. Extract Docker Provider
    provider_match = re.search(r"Docker Provider\s+(?:✅|\[OK\])\s+([^\n]+)", content)
    if not provider_match:
        provider_match = re.search(r"Docker Provider\s+([^\n]+)", content)

    provider = provider_match.group(1).strip() if provider_match else "Unknown"

    # 4. Extract LDM Version
    version = "Unknown"
    version_match = re.search(r"Version:\s+ldm\s+([^\n]+)", content)
    if not version_match:
        version_match = re.search(r"Version:\s+([^\n]+)", content)

    if version_match:
        cand = version_match.group(1).strip()
        if not cand.startswith("$("):  # Ignore malformed PS output
            version = cand

    if version == "Unknown" or version.startswith("$("):
        # Fallback: Extract from doctor output
        v_doctor_match = re.search(r"LDM Version\s+.*?v([0-9a-z.-]+)", content)
        if v_doctor_match:
            version = v_doctor_match.group(1).strip()

    # 4.5 Extract the verify script's own embedded SCRIPT_VERSION (LDM-#1011).
    # A report generated by a stale script may not exercise checks added since
    # that script's version, even if the *binary* under test is current --
    # this is a distinct risk from the binary-version mismatch checked below.
    script_version = None
    script_version_match = re.search(r"Script Ver:\s+([^\n]+)", content)
    if script_version_match:
        cand = script_version_match.group(1).strip()
        if cand and not cand.startswith("$("):
            script_version = cand

    # 5. Extract Docker Engine version
    engine_v = "Unknown"
    # Try header first
    hev_match = re.search(r"Docker:\s+([^\n]+)", content)
    if hev_match:
        engine_v = hev_match.group(1).strip()

    if engine_v == "Unknown" or engine_v.startswith("$"):
        engine_match = re.search(r"Docker Engine\s+.*?v([0-9.]+)", content)
        if engine_match:
            engine_v = f"v{engine_match.group(1)}"

    # 6. Extract specific provider versions (OrbStack/Colima)
    provider_v = ""
    # Try header first (new scripts)
    hv_match = re.search(r"(?:Colima|OrbStack):\s+([^\n]+)", content)
    if hv_match:
        cand = hv_match.group(1).strip()
        if cand and cand != "v" and not cand.startswith("$"):
            provider_v = cand if cand.startswith("v") else f"v{cand}"

    if not provider_v:
        # Fallback to doctor section
        ov_match = re.search(r"OrbStack Version\s+.*?v([0-9.]+)", content)
        if ov_match:
            provider_v = f"v{ov_match.group(1)}"
        else:
            cv_match = re.search(r"Colima Version\s+.*?v([0-9.]+)", content)
            if cv_match:
                provider_v = f"v{cv_match.group(1)}"
            else:
                dd_match = re.search(r"Docker Desktop Version\s+.*?v([0-9.]+)", content)
                if dd_match:
                    provider_v = f"v{dd_match.group(1)}"

    # --- LEGACY MAPPINGS (Manual Overrides for existing lab reports) ---
    legacy_map = {
        "apple-silicon-macos-16-tahoe-colima": "v0.10.1",
        "apple-silicon-macos-16-tahoe-orbstack": "v2.1.1",
        "apple-intel-macos-12-monterey-orbstack": "v1.5.1",
        "windows-pc-windows-11-docker-desktop": "v4.35.0",
        "windows-pc-windows-11-native-wsl2": "WSL 2.4.4",
    }

    arch = "Unknown"
    host_os = "Unknown"
    p_low = platform_str.lower()
    fn_low = report_path.name.lower()

    # --- Standardize Environment ---
    is_mac = "mac" in p_low or "darwin" in p_low or "macos" in fn_low
    is_fedora = "fc" in p_low or "fedora" in p_low or "fedora" in fn_low
    is_ubuntu = "ubuntu" in p_low or "ubuntu" in fn_low

    # WSL: Platform MUST be Linux and mention microsoft/wsl, or filename must contain wsl
    is_wsl = (
        "linux" in p_low and ("microsoft" in p_low or "wsl" in p_low)
    ) or "wsl" in fn_low

    # Windows Native: Platform contains Windows and NOT linux, or filename contains windows and NOT wsl
    is_windows_native = (
        ("windows" in p_low or "win32" in p_low) and "linux" not in p_low
    ) or ("windows" in fn_low and "wsl" not in fn_low)

    # 4.1 Force Provider standardization
    if is_mac:
        if provider in {"Unknown", "Docker Desktop"}:
            provider = "Colima"
            # LDM-#1011 fallout: current verify_e2e_refactor.sh/.ps1 only log the
            # full `ldm doctor` output (which used to contain the disambiguating
            # "Docker Provider ... OrbStack" line) on failure, so a passing
            # OrbStack run's *content* no longer mentions "orbstack" at all.
            # Fall back to the raw report filename, same as the is_wsl/
            # is_windows_native/is_fedora/is_ubuntu checks above already do --
            # contributors name raw reports verify-{slug}-{timestamp}-{status}.txt,
            # so the filename remains a reliable signal even when content isn't.
            if (
                "orbstack" in content.lower()
                or "orbstack" in p_low
                or "orbstack" in fn_low
            ):
                provider = "OrbStack"
    elif is_wsl:
        if provider in {"Unknown", "desktop-linux"}:
            provider = "Native WSL2"
    elif is_windows_native and provider in {"Unknown", "desktop-linux"}:
        provider = "Docker Desktop"
    elif (is_fedora or is_ubuntu or "linux" in p_low) and provider in {
        "Unknown",
        "desktop-linux",
    }:
        provider = "Native Docker"

    # --- FALLBACK MAPPINGS (Timestamps) ---
    if timestamp_str == "Tue 28 Apr 12:25:38 BST 2026":
        provider = "Native WSL2"
        is_wsl = True
    elif timestamp_str == "Tue 28 Apr 10:07:43 BST 2026":
        provider = "Native Docker"
        is_fedora = True

    if is_mac:
        v_num = 0
        macos_match = re.search(r"macos[-]?(\d+)", p_low)
        if macos_match:
            v_num = int(macos_match.group(1))
        else:
            darwin_match = re.search(r"darwin[-]?(\d+)", p_low)
            if darwin_match:
                darwin_v = int(darwin_match.group(1))
                if darwin_v >= 25:
                    v_num = 16
                elif darwin_v >= 24:
                    v_num = 15
                else:
                    v_num = darwin_v - 9

        real_names = {
            11: "Big Sur",
            12: "Monterey",
            13: "Ventura",
            14: "Sonoma",
            15: "Sequoia",
            16: "Tahoe",
        }
        name = real_names.get(v_num, "")
        host_os = (
            f"macOS {v_num} {name}"
            if name
            else (f"macOS {v_num}" if v_num > 0 else "macOS 11+")
        )

        if "arm64" in p_low or "aarch64" in p_low or "darwin25" in p_low:
            arch = "Apple Silicon"
        else:
            arch = "Apple Intel"
    elif is_wsl or is_windows_native:
        host_os = "Windows 11"
        arch = "Windows PC"
    elif is_fedora or "fedora" in content.lower():
        arch = "Linux Workstation"
        fedora_match = re.search(r"fc(\d+)|fedora\s+(?:linux\s+)?(\d+)", p_low)
        if fedora_match:
            v = fedora_match.group(1) or fedora_match.group(2)
            host_os = f"Fedora {v}"
        else:
            host_os = "Fedora"
    elif is_ubuntu:
        arch = "Linux Workstation"
        ubuntu_match = re.search(r"(\d+\.\d+)", p_low)
        host_os = f"Ubuntu {ubuntu_match.group(1) if ubuntu_match else ''}".strip()
    else:
        arch = "Linux Workstation"
        host_os = "Linux"

    # Standardize slugs
    clean_arch = arch.lower().replace(" ", "-")
    clean_os = host_os.lower().replace(" ", "-").replace("+", "")
    clean_provider = provider.lower().replace(" ", "-")
    internal_slug = f"{clean_arch}-{clean_os}-{clean_provider}"

    # Apply legacy version overrides if still unknown
    if not provider_v and internal_slug in legacy_map:
        provider_v = legacy_map[internal_slug]

    # Explicitly ignore unsupported or erroneous environments
    blacklist = set()
    if internal_slug in blacklist:
        passed = False  # Ensure it doesn't accidentally pass logic elsewhere
        # We'll filter this out in the main loop anyway
        internal_slug = "IGNORE"

    return {
        "arch": arch,
        "os": host_os,
        "provider": provider,
        "engine_v": engine_v,
        "provider_v": provider_v,
        "version": version,
        "script_version": script_version,
        "passed": passed,
        "status_slug": status_slug,
        "internal_slug": internal_slug,
        "content": raw_content,
        "timestamp": dt,
        "report_path": report_path,
    }


def sync_reports():  # noqa: C901, PLR0912, PLR0915
    """Main synchronization logic."""
    results_dir = Path("references/verification-results")
    archive_dir = results_dir / "archived_findings"
    archive_dir.mkdir(parents=True, exist_ok=True)
    source_file = Path("docs/reference/compatibility.md")
    if not source_file.exists():
        print(f"Error: {source_file} not found.")
        return

    # 1. Gather and Parse all reports (excluding archive directory)
    all_txt = list(results_dir.glob("*.txt"))
    report_metas = []
    for r in all_txt:
        if r.name == ".gitkeep":
            continue
        try:
            meta = get_report_metadata(r)
            if meta["internal_slug"] == "IGNORE":
                continue

            # Ensure that raw/new reports match the current codebase version
            expected_name = f"verify-{meta['internal_slug']}-{meta['status_slug']}.txt"
            if r.name != expected_name:
                stale_reason = None
                if normalize_version(meta["version"]) != normalize_version(VERSION):
                    binary_tag = f"v{normalize_version(meta['version'])}"
                    tag_check = subprocess.run(
                        ["git", "rev-parse", "--verify", "--quiet", binary_tag],
                        capture_output=True,
                        text=True,
                        cwd=PROJECT_ROOT,
                        check=False,
                    )
                    cosmetic_bin = tag_check.returncode == 0 and _is_metadata_only_diff(
                        binary_tag
                    )
                    if cosmetic_bin:
                        UI.info(
                            f"{r.name}: binary version {meta['version']} != {VERSION}, "
                            f"but the diff between them is provably metadata-only "
                            "-- accepting this report."
                        )
                    else:
                        stale_reason = f"binary version {meta['version']} != {VERSION}"
                elif meta["script_version"] and normalize_version(
                    meta["script_version"]
                ) != normalize_version(VERSION):
                    # LDM-#1011: the binary under test may be current, but if the
                    # verify script itself is stale, the report may not have
                    # exercised checks added since that script version --
                    # a discrepancy that undermines the validity of the result,
                    # distinct from (and checked separately from) the binary
                    # version check above.
                    #
                    # LDM-#1058: unless that script-version drift is provably
                    # cosmetic (e.g. a warning-message wording fix) -- the
                    # real verification workflow upgrades the ldm binary
                    # independently of the standalone verify-script copy on
                    # each test rig (see #1049), so the script naturally lags
                    # between refreshes even when nothing it checks changed.
                    script_tag = f"v{normalize_version(meta['script_version'])}"
                    tag_check = subprocess.run(
                        ["git", "rev-parse", "--verify", "--quiet", script_tag],
                        capture_output=True,
                        text=True,
                        cwd=PROJECT_ROOT,
                        check=False,
                    )
                    cosmetic = (
                        tag_check.returncode == 0
                        and _is_verify_script_diff_cosmetic_only(script_tag)
                    )
                    if cosmetic:
                        UI.info(
                            f"{r.name}: verify script version {meta['script_version']} != "
                            f"{VERSION}, but the diff between them is provably cosmetic-only "
                            "-- accepting this report."
                        )
                    else:
                        stale_reason = f"verify script version {meta['script_version']} != {VERSION}"

                if stale_reason:
                    # Generate a unique hash for the archived filename to avoid collisions
                    name_hash = hashlib.md5(
                        f"{meta['internal_slug']}{meta['timestamp']}".encode()
                    ).hexdigest()[:8]
                    archived_name = f"verify-{meta['internal_slug']}-{meta['status_slug']}-{name_hash}.txt"
                    UI.warning(
                        f"Archiving outdated raw report {r.name} -> {archived_name} "
                        f"({stale_reason})."
                    )
                    _mutate(
                        f"archive outdated raw report {r.name} -> {archived_name}",
                        lambda r=r, n=archived_name: shutil.move(
                            str(r), str(archive_dir / n)
                        ),
                    )
                    continue

            report_metas.append(meta)
        except Exception as e:
            UI.warning(f"Failed to parse {r.name}: {e}")

    # 2. Standardize Filenames & Archive Old Reports
    # We only keep the LATEST report for each environment (internal_slug) in the root
    latest_by_env = {}
    for meta in sorted(report_metas, key=lambda x: x["timestamp"]):
        latest_by_env[meta["internal_slug"]] = meta

    # Archive every non-latest report *before* any latest report writes to its
    # canonical target path. These two groups must not be interleaved: when an
    # existing canonical file (e.g. verify-{slug}-pass.txt) is the non-latest
    # report for its slug, its meta["report_path"] *is* that canonical path --
    # if the latest report for the same slug were written there first (glob
    # order is not guaranteed to match timestamp order), the archive step
    # below would then move the just-written *new* content into
    # archived_findings/ under an old hash-named file, silently vanishing the
    # canonical report from the root directory entirely. Doing all archiving
    # first guarantees the target path is always clear before it's written.
    non_latest = [m for m in report_metas if latest_by_env[m["internal_slug"]] is not m]
    latest = [m for m in report_metas if latest_by_env[m["internal_slug"]] is m]

    for meta in non_latest:
        expected_name = f"verify-{meta['internal_slug']}-{meta['status_slug']}"
        # Generate a unique hash for the filename to prevent collisions if timestamps are identical
        name_hash = hashlib.md5(
            f"{meta['internal_slug']}{meta['timestamp']}".encode()
        ).hexdigest()[:8]
        archived_name = f"{expected_name}-{name_hash}.txt"
        UI.info(f"Archiving old report: {meta['report_path'].name} -> {archived_name}")
        _mutate(
            f"archive {meta['report_path'].name} -> {archived_name}",
            lambda m=meta, n=archived_name: shutil.move(
                str(m["report_path"]), str(archive_dir / n)
            ),
        )

    for meta in latest:
        new_name = f"verify-{meta['internal_slug']}-{meta['status_slug']}.txt"
        target_path = results_dir / new_name
        UI.info(
            f"Standardizing & Anonymizing: {meta['report_path'].name} -> {new_name}"
        )
        clean_content = anonymize_content(meta["content"])

        # Remove the old file if it has a different name
        if meta["report_path"].exists() and meta["report_path"] != target_path:
            _mutate(
                f"remove superseded {meta['report_path'].name}",
                lambda m=meta: m["report_path"].unlink(),
            )

        _mutate(
            f"write standardized report {target_path.name}",
            lambda p=target_path, c=clean_content: p.write_text(c),
        )
        # In dry-run the file was not written, so leave report_path pointing at
        # the real file on disk; the table pass below re-reads from disk.
        if not DRY_RUN:
            meta["report_path"] = target_path

    # 3. Table Generation Logic
    root_reports = list(results_dir.glob("*.txt"))
    final_metas = []
    for r in root_reports:
        if r.name == ".gitkeep":
            continue
        final_metas.append(get_report_metadata(r))

    table_metas = []
    for meta in final_metas:
        if meta["provider"] == "Unknown":
            has_better = any(
                m
                for m in final_metas
                if m["arch"] == meta["arch"]
                and m["os"] == meta["os"]
                and m["provider"] != "Unknown"
            )
            if has_better:
                continue
        table_metas.append(meta)

    # 4. Update COMPATIBILITY_TABLE.md
    def get_badge(provider, host_os):
        logo = (
            "apple"
            if "mac" in host_os.lower()
            else ("windows" if "windows" in host_os.lower() else "linux")
        )
        mapping = {
            "Colima": f"![Colima](https://img.shields.io/badge/Colima-Hardening-FFAB00?style=flat-square&logo={logo})",
            "OrbStack": f"![OrbStack](https://img.shields.io/badge/OrbStack-Hardening-00B0FF?style=flat-square&logo={logo})",
            "Docker Desktop": f"![DockerDesktop](https://img.shields.io/badge/Docker_Desktop-Hardening-00C853?style=flat-square&logo={logo})",
            "Native WSL2": f"![WSL2](https://img.shields.io/badge/WSL2-Hardening-blue?style=flat-square&logo={logo})",
            "Native Docker": f"![Linux](https://img.shields.io/badge/Linux-Hardening-success?style=flat-square&logo={logo})",
        }
        return mapping.get(provider, f"`{provider}`")

    table_header = "| Architecture | Host OS | Docker Provider | Docker Engine | Hardening | LDM Version | Verified | Report |"
    table_sep = "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    rows = []
    for meta in sorted(table_metas, key=lambda x: (x["arch"], x["os"], x["provider"])):
        badge = get_badge(meta["provider"], meta["os"])
        icon = "✅" if meta["passed"] else "❌"
        report_link = f"[{meta['report_path'].name}](../../references/verification-results/{meta['report_path'].name})"

        provider_display = f"**{meta['provider']}**"
        if meta["provider_v"]:
            provider_display += f" `{meta['provider_v']}`"

        display_version = meta["version"]
        promotable = get_promotable_stable_version(display_version)
        if promotable:
            UI.info(
                f"{meta['report_path'].name}: displaying v{display_version} as v{promotable} "
                "in the compatibility table (verification-results/*.txt left untouched)."
            )
            display_version = promotable

        rows.append(
            f"| **{meta['arch']}** | {meta['os']} | {provider_display} | `{meta['engine_v']}` | {badge} | `{display_version}` | {icon} | {report_link} |"
        )

    new_table = f"{table_header}\n{table_sep}\n" + "\n".join(rows)

    content = source_file.read_text()
    content = content.replace(
        "# Compatibility Table (Source)", "# Compatibility Table (Standalone Binaries)"
    )
    marker_regex = re.compile(
        r"<!-- COMPATIBILITY_START -->.*?<!-- COMPATIBILITY_END -->", re.DOTALL
    )

    infra_block = """
## Global Infrastructure

| Component | Verified Versions | Notes |
| :--- | :--- | :--- |
| **Traefik** | `v3.6.1+` | Automatic API version negotiation enabled. |
| **Elasticsearch** | `8.19.1`, `7.17.24` | Dual support. ES 8.17.x+ required for Liferay 2025.Q2+ (ES 7 deprecated). |
"""
    new_block = f"<!-- COMPATIBILITY_START -->\n{new_table}\n{infra_block}\n<!-- COMPATIBILITY_END -->"
    _mutate(
        f"rewrite {source_file.name} compatibility block",
        lambda: source_file.write_text(marker_regex.sub(new_block, content)),
    )
    UI.success(
        f"{'[dry-run] would update' if DRY_RUN else 'Updated'} COMPATIBILITY_TABLE.md. "
        f"Unique environments in table: {len(table_metas)}"
    )

    if DRY_RUN:
        UI.info("[dry-run] skipping docs sync (sync_docs.sync_table)")
        return

    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from sync_docs import sync_table

        sync_table()
    except Exception as e:
        UI.error(f"Sync docs failed: {e}")


def main(argv=None):
    """Parses arguments and runs the sync.

    LDM-#1252: this entry point previously called sync_reports() directly with
    no argument handling, so `--help`, `--dry-run` or any typo silently ran a
    full sync -- archiving reports and rewriting the compatibility table.
    """
    parser = argparse.ArgumentParser(
        prog="sync_compatibility.py",
        description=(
            "Standardizes raw verification reports in "
            "references/verification-results/, archives superseded ones, and "
            "regenerates the compatibility table in the documentation."
        ),
        epilog=(
            "Reports whose recorded version does not match this checkout's "
            "VERSION are archived as stale. Run this from the branch whose "
            "VERSION matches the reports you are syncing (e.g. the active "
            "release branch), and preview with --dry-run first."
        ),
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Report every rename, archive and table edit without changing anything.",
    )
    args = parser.parse_args(argv)

    # Matches the existing convention for one-shot CLI state in this codebase
    # (see ldm_core/utils.py:640, ldm_core/handlers/mcp.py).
    global DRY_RUN  # noqa: PLW0603
    DRY_RUN = args.dry_run

    # The documented failure mode is a version mismatch between this checkout
    # and the reports, and it is otherwise invisible until files start moving.
    UI.info(f"Syncing against VERSION {VERSION}{' (dry run)' if DRY_RUN else ''}")

    sync_reports()

    if DRY_RUN:
        UI.info("[dry-run] no files were changed. Re-run without --dry-run to apply.")


if __name__ == "__main__":
    main()
