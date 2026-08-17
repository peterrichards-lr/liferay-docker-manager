"""Target configuration models and registry store for LDM Multi-Node Orchestration."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from ldm_core.utils import (
    get_actual_home,
    load_global_config_safe,
    run_command,
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
    if project_target:
        if not isinstance(project_target, str):
            return targets.get("local") or TargetNode(
                name="local", host="localhost", is_default=True
            )
        if project_target in targets:
            return targets[project_target]
        if project_target == "local":
            return targets.get("local") or TargetNode(
                name="local", host="localhost", is_default=True
            )
        return TargetNode(name=project_target, host=project_target)

    for target in targets.values():
        if target.is_default:
            return target

    return targets.get("local") or TargetNode(
        name="local", host="localhost", is_default=True
    )


def resolve_remote_home(target: TargetNode) -> str | None:
    """Resolves a remote target's absolute home directory via SSH.

    LDM-#1134: Docker bind-mount sources must be absolute paths -- the
    remote daemon does not shell-expand `~`. `sync_project_to_target`'s
    own SSH-executed mkdir/rsync commands can use a literal `~` because
    the remote shell expands it, but the *compose file* itself needs a
    real absolute string to hand to the Docker Engine API, so this
    resolves it explicitly, once, via a trivial `echo $HOME` round trip.
    """
    if target.name == "local" or target.host in ("localhost", "127.0.0.1", ""):
        return None
    target_spec = f"{target.user}@{target.host}" if target.user else target.host
    ssh_opts = ["-i", target.key_path] if target.key_path else []
    result = run_command(
        [
            "ssh",
            *ssh_opts,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            target_spec,
            "echo $HOME",
        ],
        check=False,
    )
    home = result.strip() if result else None
    return home or None


def get_remote_project_root(target: TargetNode, project_name: str) -> str | None:
    """Returns the absolute remote project directory, matching the exact
    destination convention `sync_project_to_target` rsyncs/tars into
    (`~/.liferay-docker/projects/{project_name}`) -- there must be a
    single source of truth for this path so the compose file's bind-mount
    sources actually point at where the project files really land."""
    home = resolve_remote_home(target)
    if not home:
        return None
    return f"{home}/.liferay-docker/projects/{project_name}"


def sync_project_to_target(
    project_path: Path,
    target_name: str | None = None,
    config_path: Path | None = None,
) -> bool:
    import shutil

    target = get_active_target(project_target=target_name, config_path=config_path)

    # Local execution needs no remote directory sync
    if target.name == "local" or target.host in ("localhost", "127.0.0.1", ""):
        return True

    project_name = project_path.name
    dest_dir = f"~/.liferay-docker/projects/{project_name}"
    target_spec = f"{target.user}@{target.host}" if target.user else target.host

    # Ensure remote directory exists
    ssh_opts = ["-i", target.key_path] if target.key_path else []
    mkdir_cmd = ["ssh", *ssh_opts, target_spec, f"mkdir -p {dest_dir}"]
    run_command(mkdir_cmd, check=False)

    exclusions = [
        "--exclude=.git",
        "--exclude=node_modules",
        "--exclude=build",
        "--exclude=.gradle",
        "--exclude=.tmp",
        "--exclude=*.log",
    ]

    # Use rsync if available locally
    if shutil.which("rsync"):
        rsync_cmd = ["rsync", "-avz", "--delete", *exclusions]
        if target.key_path:
            rsync_cmd.extend(["-e", f"ssh -i {target.key_path}"])
        rsync_cmd.extend([f"{project_path!s}/", f"{target_spec}:{dest_dir}/"])
        res = run_command(rsync_cmd, check=False)
        return res is not None

    # Fallback to SSH tar stream
    tar_cmd = ["tar", "-czf", "-", "-C", str(project_path), "."]
    res = run_command(
        [*tar_cmd, "|", "ssh", *ssh_opts, target_spec, f"tar -xzf - -C {dest_dir}"],
        check=False,
    )
    return res is not None


@dataclass
class TargetContext:
    """The single resolved answer to "what compute target does this command
    use, and how do I reach it" -- see docs/explanation/remote-node-architecture.md
    for the full design rationale.

    Resolved exactly once per command via `resolve_target_context()`. Every
    other module reads this object instead of independently re-deriving
    target resolution -- that re-derivation is exactly what produced the
    same "hardcoded local before checking the persisted default" bug
    independently in base.py, composer.py, and info.py in the same session.
    """

    target: TargetNode
    is_remote: bool
    docker_prefix: list[str]
    compose_prefix: list[str]
    conflict_overridden: bool = False
    newly_pinned: bool = False
    local_root: Path | None = None
    remote_root: str | None = None

    def map_path(self, local_path: Path) -> Path | PurePosixPath:
        """Local targets: identity. Remote targets: rewrite `local_path`
        (which must be `local_root` or a descendant of it) onto the
        already-synced remote project root. Replaces the hand-rolled
        `mount_paths` dict composer.py built inline for the same purpose."""
        if not self.is_remote or not self.remote_root or self.local_root is None:
            return local_path
        try:
            rel = (
                Path(local_path).resolve().relative_to(Path(self.local_root).resolve())
            )
        except ValueError:
            return local_path
        return PurePosixPath(self.remote_root) / rel.as_posix()


def resolve_target_context(
    explicit_target: str | None = None,
    meta: dict | None = None,
    project_root: Path | None = None,
    config_path: Path | None = None,
    pin: bool = True,
) -> TargetContext:
    """The one function every command calls to find out what compute target
    it's running against. Nothing else should re-implement this precedence
    chain, the conflict check, or the pinning write -- see
    docs/explanation/remote-node-architecture.md.

    Precedence, most to least specific:
      1. `explicit_target` -- an explicit --node/--target CLI flag for this
         one invocation.
      2. `meta["target"]` -- this project's own pinned assignment (set via
         `ldm target set`, or pinned automatically by this function the
         first time an unpinned project resolves a target -- see below).
      3. The persisted global default (`ldm target use`), via
         `get_active_target()`'s own fallback.
      4. `local`.

    Conflict handling: if `explicit_target` disagrees with an
    ALREADY-PINNED `meta["target"]`, this warns and gives the user a
    CTRL+C window before proceeding with the override for this run only --
    the project's synced files/named volumes/state may only exist on the
    pinned node, so silently switching is exactly the kind of thing that
    produces confusing "it's not there" failures. The override is
    deliberately NOT written back as a new pin; a one-off --node flag
    should not silently and permanently reassign a project. Use `ldm
    target set`/`ldm target migrate` for that.

    Pinning: if the project has NO pinned target yet, whatever this
    resolves to -- whether from `explicit_target` or from falling through
    to the global default -- is written back into `meta` (and persisted to
    disk if `project_root` is given). This stops a project's effective
    target from silently drifting if the global default is changed later;
    without this, a project that only ever inherited the ambient default
    would appear to move to a different node the moment someone runs `ldm
    target use` for something unrelated, even though its actual files and
    volumes never moved.

    `pin=False` resolves and returns the same answer but skips the
    write-back entirely (not even into the in-memory `meta` dict). This is
    for callers that need a correct resolution -- e.g. to compute remote
    bind-mount paths -- but aren't the command's designated pinning point.
    Pinning should happen exactly once per command, at the earliest point
    the project's metadata is known (see `docs/explanation/
    remote-node-architecture.md`); a utility function generating one
    artifact shouldn't also be deciding, as a side effect, where a project
    lives forever.
    """
    meta = meta if isinstance(meta, dict) else {}
    pinned_target = meta.get("target")
    conflict_overridden = False

    if explicit_target and pinned_target and explicit_target != pinned_target:
        from ldm_core.ui import UI

        UI.warning(
            f"This project is assigned to target '{pinned_target}', but "
            f"'{explicit_target}' was explicitly requested for this run."
        )
        UI.interruptible_pause(5, "Press CTRL+C to cancel ")
        conflict_overridden = True

    chosen = explicit_target or pinned_target
    active_target = get_active_target(chosen, config_path=config_path)

    newly_pinned = False
    if not pinned_target and pin:
        meta["target"] = active_target.name
        newly_pinned = True
        if project_root is not None:
            # write_meta() treats its `path` argument literally as the file
            # to write -- project_root is the project's *directory*, not
            # its meta file, so this must go through the same
            # directory-to-meta-file resolution BaseHandler.write_meta()
            # uses. Passing project_root straight to write_meta() silently
            # clobbers the project directory itself with a file.
            from ldm_core.utils import resolve_meta_file_path, safe_mkdir, write_meta

            target = resolve_meta_file_path(project_root)
            safe_mkdir(target.parent, parents=True, exist_ok=True)
            write_meta(target, meta)

    is_remote = active_target.name != "local" and active_target.host not in (
        "localhost",
        "127.0.0.1",
        "",
    )

    # Local import: docker_service.py imports get_active_target/TargetNode
    # from this module at module scope, so importing it back at module
    # scope here would be circular.
    from ldm_core.docker_service import DockerService

    docker_prefix = DockerService.get_docker_cmd_prefix(active_target.name)
    compose_prefix = [*docker_prefix, "compose"]

    remote_root = None
    if is_remote and project_root is not None:
        remote_root = get_remote_project_root(active_target, project_root.name)

    return TargetContext(
        target=active_target,
        is_remote=is_remote,
        docker_prefix=docker_prefix,
        compose_prefix=compose_prefix,
        conflict_overridden=conflict_overridden,
        newly_pinned=newly_pinned,
        local_root=project_root,
        remote_root=remote_root,
    )
