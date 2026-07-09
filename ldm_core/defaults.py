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
    "db_max_active": "15",
    "db_min_idle": "2",
    "db_max_idle": "5",
    "log_max_size": "10m",
    "log_max_file": "3",
    "elasticsearch_heap_size": "512m",
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
