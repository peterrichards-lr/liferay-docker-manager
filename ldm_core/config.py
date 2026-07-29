"""Target configuration models and registry store for LDM Multi-Node Orchestration."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ldm_core.utils import (
    get_actual_home,
    load_global_config_safe,
    save_global_config_safe,
)


@dataclass
class TargetNode:
    """Represents a local or remote compute target node for LDM project execution."""

    name: str
    host: str = "localhost"
    user: str = ""
    key_path: str = ""
    is_default: bool = False
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "user": self.user,
            "key_path": self.key_path,
            "is_default": self.is_default,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "TargetNode":
        if not isinstance(data, dict):
            data = {}
        return cls(
            name=name,
            host=str(data.get("host", "localhost")),
            user=str(data.get("user", "")),
            key_path=str(data.get("key_path", "")),
            is_default=bool(data.get("is_default", False)),
            created_at=str(data.get("created_at", "")),
        )


def _get_config_path(config_path: Path | None = None) -> Path:
    if config_path is not None:
        return config_path
    return get_actual_home() / ".ldmrc"


def load_targets(config_path: Path | None = None) -> dict[str, TargetNode]:
    """Load all configured targets from ~/.ldmrc, ensuring 'local' default target exists."""
    target_file = _get_config_path(config_path)
    config_data = load_global_config_safe(target_file)
    raw_targets = config_data.get("targets", {})

    targets: dict[str, TargetNode] = {}
    if isinstance(raw_targets, dict):
        for name, data in raw_targets.items():
            if isinstance(data, dict):
                targets[name] = TargetNode.from_dict(name, data)

    has_default = any(t.is_default for t in targets.values())

    # Always ensure 'local' exists as a fallback target
    if "local" not in targets:
        targets["local"] = TargetNode(
            name="local",
            host="localhost",
            user="",
            key_path="",
            is_default=not has_default,
            created_at=datetime.now().isoformat(),
        )
    elif not has_default:
        targets["local"].is_default = True

    return targets


def save_target_node(target: TargetNode, config_path: Path | None = None) -> TargetNode:
    """Save or update a TargetNode in ~/.ldmrc using lock-protected atomic saver."""
    target_file = _get_config_path(config_path)
    config_data = load_global_config_safe(target_file)

    existing_targets = load_targets(config_path)
    raw_targets: dict = {}
    for t_name, t_node in existing_targets.items():
        raw_targets[t_name] = t_node.to_dict()

    # If this target is set to default, clear default status from all existing targets
    if target.is_default:
        for name, node_data in raw_targets.items():
            if isinstance(node_data, dict):
                node_data["is_default"] = name == target.name

    raw_targets[target.name] = target.to_dict()
    config_data["targets"] = raw_targets

    save_global_config_safe(target_file, config_data)
    return target


def delete_target_node(name: str, config_path: Path | None = None) -> bool:
    """Delete a TargetNode by name from ~/.ldmrc (cannot delete built-in 'local' target)."""
    if name == "local":
        return False

    target_file = _get_config_path(config_path)
    config_data = load_global_config_safe(target_file)

    raw_targets = config_data.get("targets", {})
    if not isinstance(raw_targets, dict) or name not in raw_targets:
        return False

    was_default = raw_targets[name].get("is_default", False)
    del raw_targets[name]

    if was_default:
        if "local" in raw_targets and isinstance(raw_targets["local"], dict):
            raw_targets["local"]["is_default"] = True
        elif raw_targets:
            first_key = next(iter(raw_targets))
            if isinstance(raw_targets[first_key], dict):
                raw_targets[first_key]["is_default"] = True

    config_data["targets"] = raw_targets
    save_global_config_safe(target_file, config_data)
    return True


def set_default_target(name: str, config_path: Path | None = None) -> bool:
    """Set the specified target node as default."""
    targets = load_targets(config_path)
    if name not in targets:
        return False

    target_file = _get_config_path(config_path)
    config_data = load_global_config_safe(target_file)
    raw_targets = config_data.get("targets", {})

    if not isinstance(raw_targets, dict):
        raw_targets = {}

    for t_name, data in raw_targets.items():
        if isinstance(data, dict):
            data["is_default"] = t_name == name

    if name not in raw_targets and name in targets:
        raw_targets[name] = targets[name].to_dict()
        raw_targets[name]["is_default"] = True

    config_data["targets"] = raw_targets
    save_global_config_safe(target_file, config_data)
    return True


def get_active_target(
    project_target: str | None = None, config_path: Path | None = None
) -> TargetNode:
    """Resolve active target node for a project or global default."""
    targets = load_targets(config_path)
    if project_target and project_target in targets:
        return targets[project_target]

    for target in targets.values():
        if target.is_default:
            return target

    return targets.get("local") or TargetNode(
        name="local", host="localhost", is_default=True
    )
