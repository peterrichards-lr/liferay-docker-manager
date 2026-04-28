from pathlib import Path

# --- Constants & Configuration ---
# LDM_MAGIC_VERSION: 2.4.26-beta.46
VERSION = "2.4.26-beta.46"


BUILD_INFO = None
IMAGE_NAME_DXP = "liferay/dxp"
IMAGE_NAME_PORTAL = "liferay/portal"
API_BASE_DXP = "https://hub.docker.com/v2/repositories/liferay/dxp/tags?page_size=200&ordering=name"
API_BASE_PORTAL = "https://hub.docker.com/v2/repositories/liferay/portal/tags?page_size=200&ordering=name"
LIFERAY_PRODUCT_INFO_URL = (
    "https://releases-cdn.liferay.com/tools/workspace/.product_info.json"
)
META_VERSION = "2"
MIN_META_VERSION = 2
PROJECT_META_FILE = "meta"
REGISTRY_FILE = "registry.json"
TAG_PATTERN = r"^\d{4}\.q[1-4]\.\d+(-u\d+|-lts)?$"
SCRIPT_DIR = Path(__file__).parent.parent.resolve()
ELASTICSEARCH_VERSION = "8.19.1"
ELASTICSEARCH7_VERSION = "7.17.24"
TRAEFIK_VERSION = "v3.6.1"
SOCAT_IMAGE = "alpine/socat"
SEED_VERSION = "2"

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
    "no_up",
    "no_wait",
    "no_vol_cache",
    "no_jvm_verify",
    "no_tld_skip",
    "all",
    "delete",
    "infra",
    "samples",
    "cpu_limit",
    "mem_limit",
    "no_captcha",
    "external_snapshot",
    "clean_hosts",
]

# --- Static Asset Discovery ---
# Gold standard checksums for pre-warmed seeds
# Format: {filename: sha256}
KNOWN_ASSETS = {
    "seeds": {
        "seeded-7.4.13-u112-hypersonic-shared-v2.tar.gz": "d35a39775f0f3531b79872583808007a82747761030e2f5b667232230006764a",
        "seeded-2024.q1.8-hypersonic-shared-v2.tar.gz": "836ca02773229b4763134105085e3309a473a216e6aa1d34bb791deb4a8e2354",
        "seeded-2025.q1.0-hypersonic-shared-v2.tar.gz": "E632BD6CB32DD28DBF37BC388E71B119C83471C8834216E6AA1D34BB791DEB4A".lower(),
    },
    "samples": {
        "menus-collection-min.zip": "0e8c697ae1d8693e1750d5ced68e2c012c5378c9e6b03243b27f10d18f44f5f0",
    },
    "snapshots": {
        # Reserved for pre-configured demonstration states
    },
}
# BUILD_TRIGGER: Mon Apr 27 11:58:35 BST 2026
