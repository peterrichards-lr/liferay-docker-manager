import json
from pathlib import Path

from ldm_core.utils import get_actual_home

CONVENTION_DEFAULTS = {
    "tag": "",  # Empty forces user to pick or use latest
    "release_type": "lts",
    "db_type": "postgresql",
    "search_mode": "sidecar",
    "host_name": "localhost",
    "port": "8080",
    "portal": "false",
    "target_env": "prd",
}


class DefaultsManager:
    def __init__(self):
        self.global_path = Path("/etc/ldmrc")
        self.user_path = get_actual_home() / ".ldmrc"

        self.global_defaults = self._load(self.global_path)
        self.user_defaults = self._load(self.user_path)

    def _load(self, path):
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return data.get("defaults", {}) if "defaults" in data else data
            except Exception:
                pass
        return {}

    def _save(self, path, data, existing_root=None):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            root_data = existing_root or {}
            root_data["defaults"] = data
            path.write_text(json.dumps(root_data, indent=4))
            return True
        except Exception:
            return False

    def get_resolved(self):
        resolved = CONVENTION_DEFAULTS.copy()
        resolved.update(self.global_defaults)
        resolved.update(self.user_defaults)
        return resolved

    def get(self, key, fallback=None):
        return self.get_resolved().get(key, fallback)

    def set_user_default(self, key, value):
        root = {}
        if self.user_path.exists():
            try:
                root = json.loads(self.user_path.read_text())
            except Exception:
                pass
        self.user_defaults[key] = value
        return self._save(self.user_path, self.user_defaults, root)

    def remove_user_default(self, key):
        if key in self.user_defaults:
            del self.user_defaults[key]
            root = {}
            if self.user_path.exists():
                try:
                    root = json.loads(self.user_path.read_text())
                except Exception:
                    pass
            return self._save(self.user_path, self.user_defaults, root)
        return True

    def set_global_default(self, key, value):
        root = {}
        if self.global_path.exists():
            try:
                root = json.loads(self.global_path.read_text())
            except Exception:
                pass
        self.global_defaults[key] = value
        return self._save(self.global_path, self.global_defaults, root)

    def remove_global_default(self, key):
        if key in self.global_defaults:
            del self.global_defaults[key]
            root = {}
            if self.global_path.exists():
                try:
                    root = json.loads(self.global_path.read_text())
                except Exception:
                    pass
            return self._save(self.global_path, self.global_defaults, root)
        return True
