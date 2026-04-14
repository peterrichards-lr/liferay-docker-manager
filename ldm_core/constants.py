from pathlib import Path

# --- Constants & Configuration ---
# LDM_MAGIC_VERSION: 1.6.78
VERSION = "1.6.78"

BUILD_INFO = None
IMAGE_NAME_DXP = "liferay/dxp"
IMAGE_NAME_PORTAL = "liferay/portal"
API_BASE_DXP = "https://hub.docker.com/v2/repositories/liferay/dxp/tags?page_size=200&ordering=name"
API_BASE_PORTAL = "https://hub.docker.com/v2/repositories/liferay/portal/tags?page_size=200&ordering=name"
META_VERSION = "2"
MIN_META_VERSION = 2
PROJECT_META_FILE = ".liferay-docker.meta"
TAG_PATTERN = r"^\d{4}\.q[1-4]\.\d+(-u\d+|-lts)?$"
SCRIPT_DIR = Path(__file__).parent.parent.resolve()
ELASTICSEARCH_VERSION = "8.19.1"
ELASTICSEARCH7_VERSION = "7.17.24"
TRAEFIK_VERSION = "v3.6.1"
SOCAT_IMAGE = "alpine/socat"

# --- Sample Extension Hashes (SHA-256) ---
SAMPLE_HASHES = {
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
