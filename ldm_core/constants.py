import os
from pathlib import Path

# --- Constants & Configuration ---
# LDM_MAGIC_VERSION: 2.20.0-pre.3
VERSION = "2.20.0-pre.3"

# Release commit v2.11.30


BUILD_INFO = None
IMAGE_NAME_DXP = "liferay/dxp"
IMAGE_NAME_PORTAL = "liferay/portal"
API_BASE_DXP = "https://hub.docker.com/v2/repositories/liferay/dxp/tags?page_size=200&ordering=name"
API_BASE_PORTAL = "https://hub.docker.com/v2/repositories/liferay/portal/tags?page_size=200&ordering=name"
LIFERAY_PRODUCT_INFO_URL = (
    "https://releases-cdn.liferay.com/tools/workspace/.product_info.json"
)

# --- Repository & External URLs ---
REPO_OWNER = os.getenv("LDM_REPO_OWNER", "peterrichards-lr")
REPO_NAME = os.getenv("LDM_REPO_NAME", "liferay-docker-manager")
GITHUB_REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
GITHUB_API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
GITHUB_RAW_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/master"
GITHUB_DOCS_URL = f"{GITHUB_REPO_URL}/blob/master/docs"

CX_SAMPLES_REPO_URL = f"https://github.com/{REPO_OWNER}/ldm-cx-samples"
LCP_CLI_DOWNLOAD_URL = (
    "https://customer.liferay.com/downloads/-/download/liferay-cloud-cli"
)

# --- Default Project Data ---
DEFAULT_ADMIN_EMAIL = "test@liferay.com"
DEFAULT_ADMIN_PASSWORD = "test"  # pragma: allowlist secret

META_VERSION = "2"
MIN_META_VERSION = 2
PROJECT_META_FILE = "meta"
REGISTRY_FILE = "registry.json"
TAG_PATTERN = r"^(dxp-|portal-)?\d{4}\.q[1-4]\.\d+(-u\d+|-lts)?$"
SCRIPT_DIR = Path(__file__).parent.parent.resolve()
ELASTICSEARCH_VERSION = "8.19.1"
ELASTICSEARCH7_VERSION = "7.17.24"
TRAEFIK_VERSION = "v3.6.1"
SOCAT_IMAGE = "alpine/socat"

# --- Network Operation Timeouts (LDM-#1332) ---
# Bounds for operations that reach the network or the Docker daemon and can
# otherwise hang indefinitely. #1306 made BaseHandler.run_command forward a
# timeout, but several call sites bypass it and go straight to subprocess, so
# they must be bounded explicitly.
#
# Generous rather than tight: the point is to distinguish "slow" from "wedged",
# not to cap legitimate work. A ~1GB image pull is slow; an unreachable
# registry is unbounded.
IMAGE_INSPECT_TIMEOUT = 60  # local daemon query; should be near-instant
IMAGE_PULL_TIMEOUT = 1800  # 30 min -- a large image over a slow link
GIT_CLONE_TIMEOUT = 900  # 15 min -- a large workspace repository
PIP_INSTALL_TIMEOUT = 600  # 10 min -- plugin/completion dependency installs

# --- Release Announcements Mapping ---
# Maps major.minor or exact version keys to lists of (cmd, description) tuples.
RELEASE_ANNOUNCEMENTS = {
    "2.15": [
        (
            "ldm target <subcmd>",
            "Multi-node compute target management (add/ls/use/status)",
        ),
        (
            "ldm run --node <target>",
            "Deploy & manage projects on remote Docker compute nodes",
        ),
        (
            "ldm system prune",
            "Reclaim orphaned database containers & system assets",
        ),
        ("ldm link <path>", "Linked workspaces local integration (replaces init-from)"),
        ("ldm clone <url>", "Clone and setup a remote Git workspace repository"),
    ],
    "2.16": [
        (
            "myproject/portal-patches/",
            "Overlay patched core JARs onto osgi/portal on every boot",
        ),
        (
            "ldm run --force-portal-patches",
            "Apply portal patches despite a Liferay release-line mismatch",
        ),
        ("ldm run --vanilla", "Start a completely fresh Liferay, bypassing all seeds"),
        (
            "ldm run --nightly",
            "Target the latest Liferay DXP nightly build from Docker Hub",
        ),
        ("ldm prune", "Reclaim LDM's own named volumes via ownership labels"),
    ],
    "2.17": [
        (
            "ldm run --vanilla",
            "Now an intent flag: rejects --samples/--snapshot instead of "
            "silently contradicting them",
        ),
        (
            "Non-ASCII project names",
            "Name a project in any script; metadata keeps it verbatim while "
            "Docker gets a transcoded ASCII name",
        ),
        (
            "Bounded Docker probes",
            "A stalled pull or wedged mount now fails with a diagnosis "
            "instead of hanging silently",
        ),
    ],
    "2.18": [
        (
            "ldm run --search-mode shared",
            "Shared Elasticsearch now works end to end: the flag is honoured, "
            "the global service is provisioned, and index names match",
        ),
        (
            "Remote node awareness",
            "Commands say which projects target a remote node before waiting "
            "on one, instead of appearing to hang",
        ),
        (
            "Unreachable node diagnosis",
            "An SSH failure names the node, user and cause instead of printing "
            "a raw HTTP/SSH blob",
        ),
        (
            "ldm info",
            "Reports the container and database names actually in effect, "
            "sanitised exactly as Docker sees them",
        ),
        (
            "ldm stop/start/restart/down --all",
            "One unreachable project no longer abandons every project after it",
        ),
    ],
    "2.20": [
        (
            "ldm ai",
            "Opens an interactive troubleshooting session when given no "
            "question, keeping context between follow-ups instead of starting "
            "fresh each time",
        ),
        (
            "ldm run <project>",
            "Projects named with non-ASCII characters now boot: their volumes "
            "were addressed by the verbatim name while Docker held the "
            "transcoded one, so the seed never reached the container",
        ),
        (
            "ldm doctor",
            "Reports a seeded project as seeded -- the flag was written and "
            "then overwritten later in the same run, so a freshly seeded "
            "project showed as vanilla",
        ),
        (
            "ldm completion",
            "Explains what to add to your shell profile, and where: anything "
            "that re-runs compinit afterwards discards the registration",
        ),
        (
            "ldm prune",
            "Reclaims LDM's own throwaway helper containers, which are now "
            "labelled and so can be told apart from anything you started",
        ),
    ],
    "2.19": [
        (
            "ldm run --db mysql --database-mode shared",
            "Shared MySQL/MariaDB: one database cluster serving every project, "
            "alongside the existing PostgreSQL support",
        ),
        (
            "ldm run --jvm-heap-max 8g",
            "Tune one JVM setting without losing the rest -- heap, metaspace "
            "and young generation are overridable per project, per user or on "
            "the command line, and anything you leave unset stays adaptive",
        ),
        (
            "ldm info",
            "Shows the JVM arguments actually in effect and which layer each "
            "value came from, so a surprising heap size is traceable",
        ),
        (
            "ldm doctor",
            "Checks the host filesystem as well as Docker's own: Docker can "
            "report ample space while the disk backing it is full",
        ),
        (
            "ldm rm --delete",
            "Removes the project's volumes rather than leaving them for "
            "'ldm prune', including when the compose file has already gone",
        ),
    ],
}

# --- Global Infrastructure ---
INFRA_SERVICES = [
    ("liferay-proxy-global", "SSL Proxy (Traefik)"),
    ("liferay-search-global", "Search (ES)"),
    ("liferay-docker-proxy", "macOS Socket Bridge"),
]

# --- Orchestration Configuration ---
RUN_ATTRS = [
    "tag",
    "tag_prefix",
    "project",
    "container",
    "follow",
    "release_type",
    "db",
    "jdbc_username",
    "jdbc_password",
    "recreate_db",
    "port",
    "host_network",
    "host_name",
    "disable_zip64",
    "delete_state",
    "remove_after",
    "portal",
    "refresh",
    "ssl",
    "force_ssl",
    "timeout",
    "rebuild",
    "env",
    "vars",
    "service",
    "remove",
    "import_env",
    "no_stop",
    "pg_host",
    "pg_port",
    "my_host",
    "my_port",
    "files_only",
    "index",
    "checkpoint",
    "sidecar",
    "feature",
    "internal_state",
    "no_up",
    "no_wait",
    "mount_logs",
    "gogo_port",
    "jvm_args",
    # LDM-#1449: per-setting tuning overrides. Persisted so a project keeps the
    # tuning it was created with, rather than silently re-resolving from
    # whatever the machine's config says on a later run.
    "jvm_heap_min",
    "jvm_heap_max",
    "jvm_metaspace",
    "jvm_new_size",
    "jvm_tiered_stop_at_level",
    "no_vol_cache",
    "no_jvm_verify",
    "no_tld_skip",
    "no_seed",
    "vanilla",
    "seeded",
    "seed_version",
    "seed_config",
    "samples",
    "service_scale",
    "env_type",
    "cpu_limit",
    "mem_limit",
    "bundle",
    "category",
    "level",
    "list",
    "url",
    "env_id",
    "list_envs",
    "list_backups",
    "download",
    "restore",
    "sync_env",
    "logs",
    "volumes",
    "delete",
    "infra",
    "all_projects",
    "fix_hosts",
    "scale",
    "no_osgi_seed",
]

# --- Seeded State Configuration ---
# Increment this version whenever the logic for generating seeds changes
# (e.g. DB schema changes, driver updates, or hardening logic).
# LDM-#1514: the tag used when a package does not declare one and there is
# nothing to ask. Previously spelled literally in importer.py, quickstart.py
# and assets.py, which is three places to update and two to forget.
FALLBACK_LIFERAY_TAG = "2026.q1.4-lts"

SEED_VERSION = "2"

# --- Sample Extension Hashes (SHA-256) ---
SAMPLE_HASHES: dict[str, dict[str, str]] = {
    "client-extensions": {
        # "liferay-meridian-theme-css.zip": "23ace3256bdf52e0f36e518857b1e21fc4d72cddc86817ee65493a15804bbb66",
        # "liferay-meridian-theme-spritemap.zip": "d615e2db87065ffb9ee699aa9f83cc14b37c209bd33a9ab7d7d5f43cf275c15a",
        # "modern-intranet-language-batch-cx.zip": "3df5b1c6469e42e328c2622b029c02f5ac71a512ed2b97359afc18cd86d4730e",
        # "responsive-menus-language-batch-cx.zip": "27b5a27ed7834fdab61e94d0070114f9809ac20df073ccda7ee02fc54cb7f16a",
    },
    "deploy": {
        # "modern-intranet-collection-min.zip": "09a4c6723c84c481a98df5550cab93abd0daf4eef38f5729038c57c4afa8434a",
        # "responsive-menus-collection-min.zip": "0e8c697ae1d8693e1750d5ced68e2c012c5378c9e6b03243b27f10d18f44f5f0",
    },
    "snapshots": {
        # Reserved for pre-configured demonstration states
    },
}


# Characters NFKD cannot transcode correctly, shared by `sanitize_id` (Docker
# names) and `UI.to_ascii_readable` (console fallback) so the two agree on what
# a name degrades to (LDM-#1308, LDM-#1484).
#
# Two groups, two reasons:
#  1. German umlauts and Eszett DO decompose under NFKD, but to the wrong thing
#     for German -- NFKD gives "u" for "ü" where the convention is "ue".
#  2. Stroked and barred letters do NOT decompose at all. "ł" (U+0142) is an
#     atomic codepoint, so NFKD leaves it and an ASCII step then drops it --
#     "Żółć" silently became "Zoc", losing a letter.
ASCII_TRANSCODE_MAP = {
    # German convention (NFKD would strip rather than expand these)
    "ä": "ae",
    "Ä": "AE",
    "ö": "oe",
    "Ö": "OE",
    "ü": "ue",
    "Ü": "UE",
    "ß": "ss",
    # Atomic stroked/barred letters -- NFKD cannot decompose these, so
    # without an explicit mapping they vanish.
    "ł": "l",
    "Ł": "L",
    "đ": "d",
    "Đ": "D",
    "ø": "o",
    "Ø": "O",
    "ð": "d",
    "Ð": "D",
    "þ": "th",
    "Þ": "TH",
    "ħ": "h",
    "Ħ": "H",
    "ŧ": "t",
    "Ŧ": "T",
}
