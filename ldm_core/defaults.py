from pathlib import Path

from ldm_core.utils import (
    get_actual_home,
    load_global_config_safe,
    save_global_config_safe,
)

CONVENTION_DEFAULTS = {
    "tag": "",  # Empty forces user to pick or use latest
    "release_type": "lts",
    "db_type": "postgresql",
    "search_mode": "shared",
    "database_mode": "isolated",
    "host_name": "localhost",
    "port": "8080",
    "portal": "false",
    "target_env": "prd",
    "tag_heuristics": {r"\.q1\.\d+$": "-lts"},
    "no_color": "false",
    "no_unicode": "false",
    "ci_trigger": "release",
    # LDM-#1454: these map to HikariCP's maximumPoolSize / minimumIdle /
    # idleTimeout. They previously mapped to DBCP names Liferay does not read,
    # so they had no effect at all; every project ran on Liferay's defaults of
    # 180 / 10. 15 is deliberate for a laptop running a single project.
    #
    # `db_max_idle` is intentionally absent: HikariCP has one pool size and no
    # maximum-idle setting. An existing ~/.ldmrc carrying it keeps loading -- it
    # is simply unused, and composer.py warns once, pointing at db_idle_timeout.
    "db_max_active": "15",
    "db_min_idle": "2",
    "db_idle_timeout": "600000",
    "log_max_size": "10m",
    "log_max_file": "3",
    "elasticsearch_heap_size": "512m",
    "custom_containers": [],
    "search_kibana_enabled": "false",
    "auto_pull_nightly": "prompt",
}


class DefaultsManager:
    def __init__(self):
        self.global_path = Path("/etc/ldmrc")
        self.user_path = get_actual_home() / ".ldmrc"

        self.global_defaults = self._load(self.global_path)
        self.user_defaults = self._load(self.user_path)

    def _load(self, path):
        data = load_global_config_safe(path)
        return data.get("defaults", {}) if "defaults" in data else data

    def _save(self, path, data, existing_root=None):
        root_data = existing_root or {}
        if path.exists():
            root_data = load_global_config_safe(path)
        root_data["defaults"] = data
        return save_global_config_safe(path, root_data)

    def get_resolved(self):
        resolved = CONVENTION_DEFAULTS.copy()
        resolved.update(self.global_defaults)
        resolved.update(self.user_defaults)
        return resolved

    def get(self, key, fallback=None):
        return self.get_resolved().get(key, fallback)

    def has_explicit(self, key):
        """Whether someone SET `key`, rather than it falling back to convention.

        LDM-#1510: `get_resolved()` layers CONVENTION_DEFAULTS < /etc/ldmrc <
        ~/.ldmrc, and `get()` cannot tell the layers apart -- an explicit
        `database_mode: isolated` in `~/.ldmrc` and the convention default of
        the same value read identically. The difference is the whole basis of
        deciding whether to OFFER a setting: a developer who has chosen a
        value, including deliberately choosing the default one, has decided,
        and inviting them to reconsider is noise.

        Deliberately does not consider whether the value is *different* from
        the convention default. "I set this explicitly" is the question, and
        `ldm config database-mode isolated` is an answer.
        """
        return key in self.user_defaults or key in self.global_defaults

    def set_user_default(self, key, value):
        root = load_global_config_safe(self.user_path)
        self.user_defaults[key] = value
        return self._save(self.user_path, self.user_defaults, root)

    def remove_user_default(self, key):
        if key in self.user_defaults:
            del self.user_defaults[key]
            root = load_global_config_safe(self.user_path)
            return self._save(self.user_path, self.user_defaults, root)
        return True

    def set_global_default(self, key, value):
        root = load_global_config_safe(self.global_path)
        self.global_defaults[key] = value
        return self._save(self.global_path, self.global_defaults, root)

    def remove_global_default(self, key):
        if key in self.global_defaults:
            del self.global_defaults[key]
            root = load_global_config_safe(self.global_path)
            return self._save(self.global_path, self.global_defaults, root)
        return True
