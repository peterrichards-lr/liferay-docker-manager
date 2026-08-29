import json
import math
import os
import platform
import re
from pathlib import Path
from typing import ClassVar

from ldm_core.ui import UI
from ldm_core.utils import (
    dict_to_yaml,
    resolve_dependency_version,
    sanitize_id,
    shared_database_name,
)


def _volume_role(volume_name):
    """Classifies a named volume by how destructive removing it would be (LDM-#1267).

    Consumed by `ldm prune` so that reclaiming disposable storage can never
    silently take a database with it:

    - ``state``   OSGi bundle state. Regenerated on the next boot; removing it
                  only costs a slower startup. LDM already wipes this itself
                  when the Liferay tag changes (`pipelines/run.py`).
    - ``data``    Database and project data. Removing it is destructive.
    - ``unknown`` Anything unrecognised. Deliberately NOT treated as disposable
                  -- an unclassified volume must default to the safe side, so a
                  future volume suffix cannot become sweepable by omission.

    Order matters: `<project>-db-db-data` ends in `-data`, so `data` is tested
    before any broader match.
    """
    name = str(volume_name)
    if name.endswith("-data"):
        return "data"
    if name.endswith("-state"):
        return "state"
    return "unknown"


def _named_volume_definition(safe_vol_key, project_name, project_uuid=None):
    """Builds the compose definition for one named volume (LDM-#1267).

    LDM-424: `name` is set explicitly so Docker does not prefix the volume with
    the compose project name (which breaks hydration).

    The labels mirror what services already receive via `_inject_ldm_labels`,
    so a volume's owner is recoverable from `docker volume inspect` rather than
    guessed from its name.
    """
    return {
        "name": safe_vol_key,
        "labels": {
            "com.liferay.ldm.project": project_name,
            "com.liferay.ldm.managed": "true",
            "com.liferay.ldm.role": _volume_role(safe_vol_key),
            # LDM-#1395: the name label is only as stable as the name. A renamed
            # project's volumes would keep the old one and belong to nothing,
            # and two projects sharing a name share the label entirely. The UUID
            # makes ownership exact. The name label stays -- it is what a human
            # reads in `docker volume inspect`.
            **({"com.liferay.ldm.project.uuid": project_uuid} if project_uuid else {}),
        },
    }


class ComposerService:
    """Service for Stack Composition and Metadata translation."""

    def __init__(self, manager=None):
        self.manager = manager

    def get_physical_host_memory_bytes(self) -> int:
        """Auto-detects the host physical memory in bytes."""
        import sys

        # Unix-like platforms
        try:
            if hasattr(os, "sysconf"):
                pagesize = os.sysconf("SC_PAGE_SIZE")
                num_pages = os.sysconf("SC_PHYS_PAGES")
                if pagesize > 0 and num_pages > 0:
                    return pagesize * num_pages
        except (ValueError, AttributeError, OSError):
            pass

        # Windows platform
        if sys.platform == "win32":
            try:
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(stat)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                    return stat.ullTotalPhys
            except Exception:
                pass

        # Global fallback (16 GB)
        return 16 * 1024 * 1024 * 1024

    # LDM-#1449: named profiles are DATA, not code.
    #
    # `lean` used to be an early `return` of a fixed string, which meant it
    # bypassed the adaptive calculation entirely -- the same all-or-nothing
    # shape as `--jvm-args`, just with better-chosen numbers. Expressed as
    # overrides it goes through the same merge as every other layer, so a
    # profile that sets four keys leaves the rest adaptive, and a later fix to
    # the adaptive sizing reaches lean too.
    #
    # Values are ABSOLUTE, not caps. A profile key replaces the adaptive value
    # outright, which is what `lean` needs: on a 64 GB runner it must still
    # produce a small heap, and an absolute does that. Cap/floor semantics
    # ("adaptive, but no more than X") would be a separate key shape and can be
    # added later without breaking absolutes -- doing it the other way round
    # could not.
    #
    # `new_size: None` means "omit the flag", which is what lean did by
    # returning a string without it.
    TUNING_PROFILES: ClassVar[dict] = {
        "lean": {
            # Optimized for 7GB GitHub Runners (2GB heap)
            "heap_min_mb": 1536,
            "heap_max_mb": 2048,
            "metaspace": "512m",
            "new_size_mb": None,
            "tiered_stop_at_level": True,
        },
    }

    # Config keys, in the LDM-#1449 cascade, mapped to the internal setting they
    # override. Names follow the existing style (`db_max_active`,
    # `elasticsearch_heap_size`).
    _TUNING_CONFIG_KEYS: ClassVar[dict] = {
        "jvm_heap_min": "heap_min_mb",
        "jvm_heap_max": "heap_max_mb",
        "jvm_metaspace": "metaspace",
        "jvm_new_size": "new_size_mb",
        "jvm_tiered_stop_at_level": "tiered_stop_at_level",
    }

    def get_default_jvm_args(self, target_name=None):
        """Calculates recommended JVM arguments based on available host and Docker RAM.

        LDM-#1449: the calculation now produces a settings mapping which the
        cascade layers over, rather than a string assembled in one place. The
        rendered output is unchanged -- `test_tuning_cascade.py` pins it
        byte-for-byte across memory tiers, platforms and `--lean`, because
        `--lean` is applied implicitly whenever GITHUB_ACTIONS=true and a change
        there would silently alter what CI runs.
        """
        return self._render_jvm_args(self._resolve_tuning(target_name))

    def _resolve_tuning(self, target_name=None, origins_out=None):
        """Merges the tuning layers, most specific last (LDM-#1449).

            adaptive calculation   base, unchanged behaviour
            profile                --lean / TUNING_PROFILES
            /etc/ldmrc             per-machine    ) both via the defaults
            ~/.ldmrc               per-user       ) cascade
            project meta           per-project
            CLI flag               most specific

        An unset key keeps the adaptive value. That single property is what
        separates this from `--jvm-args`, which replaces everything.
        """
        settings = self._adaptive_tuning(target_name)
        # LDM-#1458: record which layer supplied each value. A five-layer
        # cascade means a value can change because of a file the user is not
        # looking at; without attribution, "why is my heap this size?" means
        # reading three config files and knowing the adaptive tiers. Same
        # reasoning as LDM-#1351, which made `ldm info` report the names
        # actually applied rather than the ones requested.
        origins = dict.fromkeys(settings, "calculated")

        # LDM-385: 'Lean' profile for CI or low-memory environments.
        is_lean = (
            getattr(self.manager.args, "lean", False)
            or os.getenv("GITHUB_ACTIONS") == "true"
        )
        if is_lean:
            profile = self.TUNING_PROFILES["lean"]
            settings.update(profile)
            origins.update(dict.fromkeys(profile, "profile (lean)"))

        defaults = getattr(self.manager, "defaults", None)
        meta = getattr(self.manager, "meta", None) or {}
        for config_key, setting in self._TUNING_CONFIG_KEYS.items():
            value = None
            if defaults is not None:
                value = self._tuning_value(config_key, defaults.get(config_key))
                if value is not None:
                    origins[setting] = "ldm config"
            if isinstance(meta, dict):
                from_meta = self._tuning_value(config_key, meta.get(config_key))
                if from_meta is not None:
                    value = from_meta
                    origins[setting] = "project meta"
            cli_value = self._tuning_value(
                config_key, getattr(self.manager.args, config_key, None)
            )
            if cli_value is not None:
                value = cli_value
                origins[setting] = "command line"
            if value is not None:
                settings[setting] = value

        if origins_out is not None:
            origins_out.update(origins)
        return settings

    # Sizes accept a bare number of megabytes or a JVM-style suffix, matching
    # what a user would write in `-Xmx`.
    _SIZE_RE = re.compile(r"^\d+[kKmMgG]?$")

    @classmethod
    def _tuning_value(cls, config_key, raw):
        """Validates one configured tuning value, or returns None (LDM-#1449).

        Two jobs, both protecting the adaptive calculation:

        1. **Reject non-scalars.** Config values arrive from YAML, JSON or
           argparse and are always scalars. Anything else -- most often a test
           double whose `.get()` returns a Mock rather than None -- means "not
           configured". Without this a Mock renders as `-Xmx1m` and the adaptive
           sizing is silently discarded.

        2. **Reject values that cannot mean what the setting needs.** A bad
           value must not reach the renderer: `int("huge")` raises
           `ValueError: invalid literal for int()` at container start, naming
           neither the setting nor the layer it came from. Warn, ignore it, and
           keep the adaptive value -- a project that starts on sane defaults is
           better than one that will not start at all.
        """
        if raw is None:
            return None
        if config_key == "jvm_tiered_stop_at_level":
            return cls._tuning_flag(config_key, raw)
        return cls._tuning_size(config_key, raw)

    @staticmethod
    def _tuning_flag(config_key, raw):
        """A boolean tuning setting."""
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
            return raw.strip().lower() == "true"
        if isinstance(raw, int):
            return bool(raw)
        UI.warning(
            f"Ignoring '{config_key}={raw}': expected true or false (LDM-#1449)."
        )
        return None

    @classmethod
    def _tuning_size(cls, config_key, raw):
        """A size setting, normalised to megabytes where the renderer needs it.

        `jvm_metaspace` is rendered verbatim into `-XX:MetaspaceSize`, which
        accepts a JVM suffix. The heap and new-size settings are rendered as
        `...m`, so a suffixed value must be normalised here -- otherwise
        `int("8g")` raises at container start.
        """
        if isinstance(raw, bool):
            UI.warning(f"Ignoring '{config_key}={raw}': expected a size.")
            return None

        text = str(raw).strip()
        if not cls._SIZE_RE.match(text):
            UI.warning(
                f"Ignoring '{config_key}={raw}': expected a size such as 2048, "
                "512m or 8g. Keeping the value LDM calculated (LDM-#1449)."
            )
            return None

        if config_key == "jvm_metaspace":
            return text

        unit = text[-1].lower()
        if unit.isdigit():
            return int(text)
        number = int(text[:-1])
        multipliers = {"g": 1024, "m": 1, "k": 0}
        if unit == "k":
            return max(1, number // 1024)
        return number * multipliers[unit]

    @staticmethod
    def _render_jvm_args(settings):
        """Renders the settings mapping into the JVM argument string.

        Flag order is load-bearing only in that it must not change: the
        byte-identical assertions in test_tuning_cascade.py compare against
        output captured before this refactor.
        """
        parts = [
            f"-Xms{int(settings['heap_min_mb'])}m",
            f"-Xmx{int(settings['heap_max_mb'])}m",
            f"-XX:MaxMetaspaceSize={settings['metaspace']}",
            f"-XX:MetaspaceSize={settings['metaspace']}",
        ]
        new_size = settings.get("new_size_mb")
        if new_size is not None:
            parts.append(f"-XX:NewSize={int(new_size)}m")
            parts.append(f"-XX:MaxNewSize={int(new_size)}m")
        if settings.get("tiered_stop_at_level"):
            parts.append("-XX:TieredStopAtLevel=1")
        return " ".join(parts)

    def _adaptive_tuning(self, target_name=None):
        """The unchanged adaptive calculation, as a settings mapping."""
        try:
            # Get Docker memory limit if available
            docker_mem = 0
            try:
                # LDM-#1133: sizing must reflect the RAM of whichever daemon
                # will actually run the container -- a remote target's host
                # can have wildly different available memory than the
                # orchestrating machine's local Docker Desktop/Colima VM.
                target_name = getattr(self.manager, "target", None) or target_name
                from ldm_core.docker_service import DockerService

                docker_prefix = DockerService.get_docker_cmd_prefix(target_name)

                # We use self.manager.run_command from the base mixin
                docker_info_raw = self.manager.run_command(
                    [*docker_prefix, "info", "--format", "{{json .}}"], check=False
                )
                if docker_info_raw:
                    info = json.loads(docker_info_raw)
                    docker_mem = info.get("MemTotal", 0)
            except Exception:
                pass

            # Get host physical memory
            host_mem = self.get_physical_host_memory_bytes()

            # Effective memory is the minimum of both (or host_mem if docker_mem is zero/invalid)
            if docker_mem > 0:
                mem_bytes = min(host_mem, docker_mem)
            else:
                mem_bytes = host_mem

            mem_gb = mem_bytes / (1024**3)

            # Adaptive Tiers for Low-Memory environments
            if mem_gb <= 4:
                max_heap_gb = 2.0
                min_heap_gb = 1.0
                metaspace = "384m"
            elif mem_gb <= 8:
                max_heap_gb = 3.0
                min_heap_gb = 2.0
                metaspace = "512m"
            else:
                # 80/20 DESIGN: Leave room for Sidecar and OS
                max_heap_gb = max(4.0, float(math.floor(mem_gb * 0.50)))
                min_heap_gb = max(2.0, float(math.floor(mem_gb * 0.25)))
                max_heap_gb = min(max_heap_gb, 12.0 if mem_gb < 24 else 32.0)
                min_heap_gb = min(min_heap_gb, 4.0)
                metaspace = "768m" if mem_gb <= 16 else "1024m"

            new_size_mb = max(512, math.floor((max_heap_gb * 1024) * 0.33))

            # LDM-#1464: `-XX:TieredStopAtLevel=1` is no longer applied by
            # default on macOS/Windows.
            #
            # It was added on the stated grounds that it "speeds up bundle
            # resolution significantly". Measured over five full starts per arm
            # on macOS 26.6.2 / Colima with 2026.q1.7-lts, that benefit is not
            # reproducible -- warm median time-to-ready was **130.5s either
            # way**, with identical means and identical spread (129-135s).
            #
            # The cost is real and was measured in LDM-#1448: the flag drops the
            # JVM's ergonomic ReservedCodeCacheSize from 240 MB to **48 MB**,
            # the figure Liferay's tuning guidance calls out as harmful. The
            # reindex path (LDM-422/423) already had to undo the flag and raise
            # the cache to 512m to avoid VirtualMachineError -- a symptom of
            # this same cause.
            #
            # Capping at C1 is also a throughput trade that time-to-ready cannot
            # see, so the unmeasured cost points the same way as the measured
            # one.
            #
            # Anyone relying on the previous behaviour keeps it with a single
            # config key: `jvm_tiered_stop_at_level=true`, or
            # `--jvm-tiered-stop-at-level true` (LDM-#1449). `--lean` still sets
            # it, unchanged: that profile deliberately trades throughput for a
            # small footprint.
            tiered = False

            return {
                "heap_min_mb": int(min_heap_gb * 1024),
                "heap_max_mb": int(max_heap_gb * 1024),
                "metaspace": metaspace,
                "new_size_mb": new_size_mb,
                "tiered_stop_at_level": tiered,
            }
        except Exception:
            # Unchanged fallback, as a mapping.
            return {
                "heap_min_mb": 4096,
                "heap_max_mb": 12288,
                "metaspace": "768m",
                "new_size_mb": None,
                "tiered_stop_at_level": True,
            }

    def _is_ssl_active(self, host_name, meta):
        """Determines if SSL/Proxy routing should be enabled for a project."""
        is_literal_localhost = host_name == "localhost"

        # Priority: 1. CLI Arg, 2. Meta 'ssl', 3. Meta 'use_ssl', 4. Default (True for custom)
        ssl_arg = getattr(self.manager.args, "ssl", None)
        meta_ssl = meta.get("ssl", meta.get("use_ssl"))

        if ssl_arg is not None:
            active = ssl_arg
        elif meta_ssl is not None:
            active = str(meta_ssl).lower() == "true"
        else:
            active = not is_literal_localhost

        if is_literal_localhost:
            return False

        return active

    def _update_tunnel_env_file(
        self, project_name, meta, subdomain, token, server_url, share_domain
    ):
        import re
        from pathlib import Path

        if hasattr(self.manager, "detect_project_path"):
            project_path = self.manager.detect_project_path(project_name)
            if project_path and isinstance(project_path, (str, Path)):
                if share_domain:
                    meta["share_domain"] = share_domain
                    self.manager.write_meta(project_path, meta)

                env_file = Path(project_path) / ".env"
                env_content = ""
                if env_file.exists():
                    env_content = env_file.read_text()

                if "LFT_SUBDOMAIN=" in env_content:
                    env_content = re.sub(
                        r"LFT_SUBDOMAIN=.*",
                        f"LFT_SUBDOMAIN={subdomain}",
                        env_content,
                    )
                else:
                    env_content = (
                        env_content.rstrip() + f"\nLFT_SUBDOMAIN={subdomain}\n"
                    )

                if "LFT_CLIENT_TOKEN=" in env_content:
                    env_content = re.sub(
                        r"LFT_CLIENT_TOKEN=.*",
                        f"LFT_CLIENT_TOKEN={token}",
                        env_content,
                    )
                else:
                    env_content = env_content.rstrip() + f"\nLFT_CLIENT_TOKEN={token}\n"

                if server_url:
                    if "LFT_SERVER_URL=" in env_content:
                        env_content = re.sub(
                            r"LFT_SERVER_URL=.*",
                            f"LFT_SERVER_URL={server_url}",
                            env_content,
                        )
                    else:
                        env_content = (
                            env_content.rstrip() + f"\nLFT_SERVER_URL={server_url}\n"
                        )

                env_file.write_text(env_content.strip() + "\n")

    def write_docker_compose(  # noqa: C901, PLR0912, PLR0915
        self, paths, meta, liferay_env=None, target_context=None
    ):
        """Generates the docker-compose.yml file using the Builder Pattern.

        `target_context` (a `ldm_core.config.TargetContext`) is normally
        supplied by the caller -- the `run`/`init` pipeline resolves it once
        in `ProjectInitializationStage` and threads it through, exactly so
        this function doesn't re-resolve (and re-pin) a target independently
        of the rest of the command. Direct/legacy callers that don't have
        one yet get a correct resolution here too (see below), just without
        the pinning side effect -- that's the pipeline's job, not this
        function's.
        """
        # Ensure paths is a dictionary for subscripting
        if not isinstance(paths, dict):
            paths = self.manager.setup_paths(paths)

        from ldm_core.utils import sanitize_id

        original_name = meta.get("container_name") or paths["root"].name
        project_name = sanitize_id(original_name)

        if original_name != project_name and getattr(
            self.manager.args, "verbose", False
        ):
            UI.detail(
                f"Project name '{original_name}' contains invalid characters for Docker. Using '{project_name}' for container names."
            )

        host_name = meta.get("host_name", "localhost")
        ssl_enabled = self._is_ssl_active(host_name, meta)

        # LDM-#1134/#1121/#1135: bind-mount sources must reference the
        # *remote* host's filesystem when a --node target is active, not
        # this host's -- otherwise Docker auto-creates them empty (and
        # root-owned) on the remote engine. `paths` is still used unchanged
        # everywhere else in the builders below (local filesystem reads);
        # `mount_paths` is the remote-mapped equivalent, used exclusively
        # for bind-mount source strings. See
        # docs/explanation/remote-node-architecture.md for why this goes
        # through the shared resolver rather than an ad-hoc lookup here.
        if target_context is None:
            from ldm_core.config import resolve_target_context

            target_context = resolve_target_context(
                explicit_target=getattr(self.manager, "target", None),
                meta=meta,
                project_root=paths["root"],
                pin=False,
            )

        if target_context.is_remote and not target_context.remote_root:
            UI.warning(
                f"Could not resolve the home directory on remote target "
                f"'{target_context.target.name}' -- bind mounts may point at the "
                "wrong host's paths. Check SSH connectivity."
            )

        mount_paths = {
            key: (target_context.map_path(value) if isinstance(value, Path) else value)
            for key, value in paths.items()
        }

        services = {}

        # Build individual services
        services["liferay"] = self._build_liferay_service(
            paths, meta, host_name, project_name, ssl_enabled, liferay_env, mount_paths
        )

        search_service = self._build_search_service(meta)
        if search_service:
            services["search"] = search_service

        db_service = self._build_db_service(meta, project_name)
        if db_service:
            db_service_name = f"{project_name}-db"
            services[db_service_name] = db_service
            # Add dependency from liferay to db
            services["liferay"]["depends_on"] = {
                db_service_name: {"condition": "service_healthy"}
            }

        # Append Microservices/Client Extensions
        ext_services = self._build_extensions_services(
            paths, meta, host_name, project_name, ssl_enabled, mount_paths
        )
        services.update(ext_services)

        custom_containers = meta.get("custom_containers")
        if not custom_containers:
            custom_containers = self.manager.defaults.get("custom_containers", [])
        elif isinstance(custom_containers, str):
            import json

            try:
                custom_containers = json.loads(custom_containers)
            except Exception:
                custom_containers = []

        if custom_containers:
            custom_services = self._build_custom_containers(
                custom_containers, host_name, project_name, ssl_enabled, meta
            )
            services.update(custom_services)

        search_kibana_enabled = (
            str(
                meta.get(
                    "search_kibana_enabled",
                    getattr(self.manager.defaults, "global_defaults", {}).get(
                        "search_kibana_enabled", "false"
                    ),
                )
            ).lower()
            == "true"
        )
        if search_kibana_enabled:
            kibana_service = self._build_kibana_service(meta, project_name)
            if kibana_service:
                services["kibana"] = kibana_service

        ngrok_service = self._build_ngrok_service(host_name, meta)
        if ngrok_service:
            services["ngrok"] = ngrok_service

        lfr_tunnel_service = self._build_lfr_tunnel_service(paths, meta, project_name)
        if lfr_tunnel_service:
            services["lfr-tunnel"] = lfr_tunnel_service

        compose = {
            # LDM-#1307: set the Compose project name explicitly.
            #
            # Without this, Compose derives it from the working directory,
            # lowercasing and discarding anything outside [a-z0-9_-]. For a
            # directory with no ASCII alphanumerics at all -- "Żółć", "日本語",
            # any Cyrillic, Greek or Arabic name -- that derivation yields an
            # empty string and Compose refuses to run:
            #
            #     Error Details: project name must not be empty
            #
            # so the project could not start at all. `project_name` is the
            # sanitize_id() form already used for container names and volume
            # prefixes, so naming the project after it keeps every Docker-facing
            # identifier consistent, while the human-readable name stays in the
            # project metadata and directory. Docker never has to understand
            # the original; LDM does the translating.
            "name": project_name,
            "services": services,
            "networks": {"liferay-net": {"external": True}},
        }

        project_uuid = meta.get("uuid") if isinstance(meta, dict) else None
        self._inject_ldm_labels(services, project_name, project_uuid)

        # LDM-369: Add top-level volumes for Named Volumes (data/state)
        named_volumes: dict[str, dict] = {}
        for svc in services.values():
            for vol in svc.get("volumes", []):
                if ":" in vol:
                    parts = vol.split(":")
                    if len(parts) >= 2:
                        # Handle Windows drive letters (e.g., C:/path or C:\path)
                        if (
                            len(parts[0]) == 1
                            and parts[0].isalpha()
                            and (parts[1].startswith("/") or parts[1].startswith("\\"))
                        ):
                            host_side = parts[0] + ":" + parts[1]
                        else:
                            host_side = parts[0]
                    else:
                        host_side = vol

                    # If it doesn't look like a path, it's a named volume
                    if not (
                        host_side.startswith(".")
                        or host_side.startswith("/")
                        or "/" in host_side
                        or "\\" in host_side
                    ):
                        # Ensure the volume identifier contains no spaces
                        safe_vol_key = sanitize_id(host_side)
                        # LDM-424: Force explicit volume naming to prevent Docker from prefixing
                        # with the project name (which causes hydration mismatches).
                        #
                        # LDM-#1267: carry the same ownership labels the services
                        # get (see _inject_ldm_labels). Without them a volume is
                        # anonymous as to origin, so nothing can distinguish an
                        # abandoned LDM volume from a third-party one -- which is
                        # why `ldm prune` has no safe way to reclaim them (#1266).
                        named_volumes[safe_vol_key] = _named_volume_definition(
                            safe_vol_key, project_name, project_uuid
                        )

        if named_volumes:
            compose["volumes"] = named_volumes

        self._merge_archetype_overlay(meta, compose)

        self._inject_logging_limits(compose)

        from ldm_core.utils import safe_write_text

        safe_write_text(paths["compose"], dict_to_yaml(compose))

    def is_using_named_volumes(self):
        """Returns True if the current platform/configuration uses Docker Named Volumes for data/state."""
        # Current policy: Named Volumes are used on all platforms to prevent locking errors.
        return True

    def _resolve_liferay_image(self, meta):
        # LDM-381: Determine base image and sanitized tag
        tag = str(meta.get("tag") or "latest")
        is_portal = str(meta.get("portal", "false")).lower() == "true"

        # Explicit tag prefixes take precedence and are stripped
        if tag.startswith("dxp-"):
            is_portal = False
            tag = tag[4:]
        elif tag.startswith("portal-"):
            is_portal = True
            tag = tag[7:]

        # Heuristic: Is it a legacy portal update tag? (e.g. 7.4.13-u102)
        is_legacy_portal_u_tag = (
            "u" in tag and "." in tag and tag.index("u") > tag.rindex(".")
        )

        image = meta.get("image_tag")
        if not image:
            # LDM-381: Portal is deprecated, default to DXP
            if is_portal or is_legacy_portal_u_tag:
                image = f"liferay/portal:{tag}"
            else:
                image = f"liferay/dxp:{tag}"
        elif str(image).startswith("-"):
            # It's a suffix
            suffix = str(image)
            image_base = (
                "liferay/portal"
                if (is_portal or is_legacy_portal_u_tag)
                else "liferay/dxp"
            )
            image = f"{image_base}:{tag}{suffix}"
        return image

    def _build_liferay_service(  # noqa: C901, PLR0912, PLR0915
        self,
        paths,
        meta,
        host_name,
        project_name,
        ssl_enabled,
        base_env,
        mount_paths=None,
    ):
        """Constructs the primary Liferay service definition.

        LDM-#1134: `paths` is used for BOTH local filesystem reads (e.g.
        checking whether a custom ES config file already exists on this
        host) AND bind-mount source construction -- those must NOT be the
        same thing when a remote --node target is active, since the
        bind-mount source needs to be a path that exists on the *remote*
        host, not this one. `mount_paths` (defaults to `paths` for local
        targets/callers that don't pass it) is used exclusively for the
        bind-mount source strings below; `paths` continues to be used for
        every local filesystem check.
        """
        if mount_paths is None:
            mount_paths = paths
        tag = str(meta.get("tag") or "latest")
        scale = int(meta.get("scale_liferay", 1))
        port = meta.get("port", 8080)
        from ldm_core.utils import resolve_infrastructure_mode

        search_mode = resolve_infrastructure_mode(
            "search_mode", meta, self.manager.defaults
        )
        use_shared_search = search_mode == "shared"
        if use_shared_search:
            UI.detail("Utilizing Global Shared Infrastructure")

        jvm_opts = str(meta.get("jvm_args", ""))
        if "-Dfile.encoding" not in jvm_opts:
            jvm_opts += " -Dfile.encoding=UTF8"
        if "-Duser.timezone" not in jvm_opts:
            jvm_opts += " -Duser.timezone=GMT"

        mandatory_opens = [
            "java.base/java.lang=ALL-UNNAMED",
            "java.base/java.lang.invoke=ALL-UNNAMED",
            "java.base/java.lang.reflect=ALL-UNNAMED",
            "java.base/java.net=ALL-UNNAMED",
            "java.base/java.util=ALL-UNNAMED",
            "java.base/java.util.concurrent=ALL-UNNAMED",
            "java.base/java.text=ALL-UNNAMED",
            "java.base/java.time=ALL-UNNAMED",
            "java.base/sun.net.www.protocol.http=ALL-UNNAMED",
            "java.base/sun.net.www.protocol.https=ALL-UNNAMED",
            "java.base/sun.nio.ch=ALL-UNNAMED",
            "java.base/sun.security.action=ALL-UNNAMED",
            "java.base/sun.security.ssl=ALL-UNNAMED",
            "java.base/sun.security.util=ALL-UNNAMED",
            "java.base/sun.security.x509=ALL-UNNAMED",
            "java.base/sun.util.calendar=ALL-UNNAMED",
            "java.management/sun.management=ALL-UNNAMED",
            "java.rmi/sun.rmi.transport=ALL-UNNAMED",
            "jdk.management/com.sun.management.internal=ALL-UNNAMED",
            "jdk.zipfs/jdk.nio.zipfs=ALL-UNNAMED",
        ]
        for opt in mandatory_opens:
            flag = f"--add-opens={opt}"
            if flag not in jvm_opts:
                jvm_opts += f" {flag}"

        if "-Djdk.util.zip.disableZip64ExtraFieldValidation=true" not in jvm_opts:
            jvm_opts += " -Djdk.util.zip.disableZip64ExtraFieldValidation=true"

        # LDM-422/423: Self-Tuning JVM for Reindexing (Performance & Stability Win)
        # If a reindex is scheduled, we must scale up the compiler resources.
        reindex_active = str(meta.get("reindex_required", "false")).lower() == "true"
        if reindex_active:
            # 1. Disable TieredStopAtLevel (Enable C2 compiler for reindex performance)
            if "-XX:TieredStopAtLevel=1" in jvm_opts:
                jvm_opts = jvm_opts.replace("-XX:TieredStopAtLevel=1", "")

            # 2. Increase CodeCache (Prevent NoSuchMethodException/VirtualMachineError)
            if "-XX:ReservedCodeCacheSize" not in jvm_opts:
                jvm_opts += " -XX:ReservedCodeCacheSize=512m"
        elif "-Xms" in jvm_opts and "-XX:TieredStopAtLevel=1" not in jvm_opts:
            # ONLY apply these to Darwin/Windows VMs where bundle resolution is slow
            if platform.system().lower() in ["darwin", "windows"]:
                jvm_opts += " -XX:TieredStopAtLevel=1"

        # LDM-369: JVM argument deduplication
        # We use a dictionary-style merge where the last flag wins for any duplicated key
        opt_map = {}
        for opt in jvm_opts.split(" "):
            if not opt:
                continue
            if opt.startswith("-D"):
                key = opt.split("=", 1)[0]
                opt_map[key] = opt
            elif opt.startswith("-Xm"):
                # Use 4 chars to distinguish -Xms and -Xmx
                key = opt[:4]
                opt_map[key] = opt
            elif opt.startswith("-XX:"):
                key = opt.split("=", 1)[0]
                opt_map[key] = opt
            else:
                opt_map[opt] = opt

        liferay_env = []
        liferay_env.append(f"LIFERAY_JVM_OPTS={' '.join(opt_map.values())}")
        liferay_env.append(
            "LIFERAY_LOG4J2_CONFIGURATION_FILE=/opt/liferay/osgi/log4j/portal-log4j-ext.xml"
        )

        if use_shared_search:
            liferay_env.extend(
                [
                    "LIFERAY_ELASTICSEARCH_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=true",
                    "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=false",
                    "LIFERAY_ELASTICSEARCH_PERIOD_CONNECTION_PERIOD_URL=http://liferay-search-global:9200",
                    f"LIFERAY_ELASTICSEARCH_PERIOD_INDEX_PERIOD_NAME_PERIOD_PREFIX=ldm-{project_name}-",
                ]
            )
            self._write_shared_search_config(paths, meta, project_name)
        else:
            # Check if user has explicitly provided custom remote search configs
            is_es8 = self.manager.parse_version(meta.get("tag")) >= (2024, 1, 0)
            es_ver = "8" if is_es8 else "7"
            es_main_conf = (
                paths.get("configs", paths["root"] / "osgi" / "configs")
                / f"com.liferay.portal.search.elasticsearch{es_ver}.configuration.ElasticsearchConfiguration.config"
            )
            has_custom_remote = es_main_conf.exists()

            if not has_custom_remote:
                # LDM-Sidecar: We must explicitly tell Liferay which ports to use for Sidecar
                # because LDM defaults to 9201 to avoid global search collisions.
                # We use portal-ext.properties to ensure these take precedence over .config files.
                es_port = int(meta.get("es_port", 9201))
                tcp_port = es_port + 100

                def get_es_props(ver):
                    base = f"module.framework.properties.com.liferay.portal.search.elasticsearch{ver}.configuration.ElasticsearchConfiguration"
                    return {
                        f"{base}.operationMode": "EMBEDDED",
                        f"{base}.sidecarHttpPort": str(es_port),
                        f"{base}.sidecarTransportTcpPort": str(tcp_port),
                        f"{base}.transportTcpPort": str(tcp_port),
                        f"{base}.sidecarNetworkHost": "0.0.0.0",  # nosec B104
                    }

                self.manager.config.update_portal_ext(paths, get_es_props(7))
                self.manager.config.update_portal_ext(paths, get_es_props(8))

                liferay_env.extend(
                    [
                        "LIFERAY_ELASTICSEARCH_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=false",
                        "LIFERAY_ELASTICSEARCH_PERIOD_SIDECAR_PERIOD_ENABLED=true",
                        "LIFERAY_ELASTICSEARCH_PERIOD_OPERATION_PERIOD_MODE=EMBEDDED",
                    ]
                )

            if "-Dliferay.auto.deploy.interval" not in jvm_opts:
                jvm_opts += " -Dliferay.auto.deploy.interval=5000"

        # LDM-422: Automatic Reindex on Startup
        if str(meta.get("reindex_required", "false")).lower() == "true":
            liferay_env.append("LIFERAY_INDEX_PERIOD_ON_PERIOD_STARTUP=true")
            liferay_env.append("LIFERAY_INDEX_PERIOD_ON_PERIOD_STARTUP_PERIOD_DELAY=30")

        # LDM-424: Inject Smart Store Implementation
        dl_store = meta.get("dl_store_impl")
        if dl_store:
            liferay_env.append(f"LIFERAY_DL_PERIOD_STORE_PERIOD_IMPL={dl_store}")

        custom_env_val = meta.get("custom_env", "{}")
        if isinstance(custom_env_val, dict):
            custom_env_dict = custom_env_val
        else:
            try:
                custom_env_dict = json.loads(custom_env_val or "{}")
            except Exception:
                custom_env_dict = {}
                if isinstance(custom_env_val, str) and custom_env_val:
                    for pair in custom_env_val.split(","):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            custom_env_dict[k.strip()] = v.strip()

        has_jdbc_env = False
        for k, v in custom_env_dict.items():
            liferay_env.append(f"{k}={v}")
            if k.startswith("LIFERAY_JDBC_PERIOD_"):
                has_jdbc_env = True

        self._inject_liferay_extensions_routes(paths, meta, project_name, liferay_env)

        db_type, db_mode = self._inject_liferay_db_env(
            paths, meta, project_name, tag, has_jdbc_env, liferay_env
        )

        port_list = self._inject_liferay_share_env(
            paths, meta, host_name, project_name, ssl_enabled, port
        )

        image = self._resolve_liferay_image(meta)

        depends_on = []
        if db_type not in ["hypersonic", "external"] and db_mode != "shared":
            depends_on.append(f"{project_name}-db")

        # 80/20 DESIGN: SELinux compatibility for Fedora/RHEL
        z_label = ":z" if platform.system().lower() == "linux" else ""

        service = {
            "image": image,
            "ports": port_list,
            "environment": liferay_env,
            "labels": [
                f"com.liferay.ldm.project={project_name}",
                "com.liferay.ldm.managed=true",
            ],
            "volumes": [
                f"{mount_paths['deploy'].as_posix()}:/mnt/liferay/deploy{z_label}",
                f"{mount_paths['files'].as_posix()}:/mnt/liferay/files{z_label}",
                f"{mount_paths['scripts'].as_posix()}:/mnt/liferay/scripts{z_label}",
                f"{mount_paths.get('routes', mount_paths['root'] / 'routes').as_posix()}:/workspace/routes{z_label}",
                f"{project_name}-data:/opt/liferay/data",
                f"{mount_paths['modules'].as_posix()}:/opt/liferay/osgi/modules{z_label}",
                # LDM-#1364: restored. `df59dea6` ("isolate configuration
                # volumes", v2.7.2) removed BOTH this and the osgi/modules
                # mount above; `57fd4b9f` brought modules back and this was
                # overlooked, so from v2.7.2 onwards nothing a user put in
                # <project>/osgi/configs ever reached Liferay.
                #
                # That was invisible because seven code paths still READ the
                # directory and change LDM's behaviour on what they find --
                # run.py even prints "Custom Elasticsearch OSGi configs
                # detected" and offers a choice, about a file the container
                # could not see. Confirmed by experiment: an
                # ElasticsearchConfiguration.config here had no effect and
                # Liferay started its embedded sidecar, while the identical
                # file placed where the container could read it connected to
                # the global cluster and indexed immediately.
                f"{mount_paths.get('configs', mount_paths['root'] / 'osgi' / 'configs').as_posix()}:/opt/liferay/osgi/configs{z_label}",
                f"{mount_paths['cx'].as_posix()}:/opt/liferay/osgi/client-extensions{z_label}",
                f"{mount_paths['portal_log4j'].as_posix()}:/opt/liferay/osgi/log4j{z_label}",
            ],
            "networks": ["liferay-net"],
        }
        if depends_on:
            service["depends_on"] = depends_on

        cpu_limit = meta.get("cpu_limit")
        mem_limit = meta.get("mem_limit")
        if cpu_limit or mem_limit:
            service["deploy"] = {"resources": {"limits": {}}}
            if cpu_limit:
                service["deploy"]["resources"]["limits"]["cpus"] = str(cpu_limit)
            if mem_limit:
                service["deploy"]["resources"]["limits"]["memory"] = (
                    str(mem_limit) + "M"
                )

        if scale == 1:
            liferay_container = sanitize_id(
                meta.get("liferay_container_name") or project_name
            )
            service["container_name"] = liferay_container

            # Host-mapped state if requested
            is_persist_osgi = str(meta.get("persist_osgi", "false")).lower() == "true"
            if is_persist_osgi:
                state_mapping = f"{mount_paths['state'].as_posix()}:/opt/liferay/osgi/state{z_label}"
            else:
                safe_volume_prefix = sanitize_id(liferay_container)
                state_mapping = f"{safe_volume_prefix}-state:/opt/liferay/osgi/state"

            service["volumes"].extend(
                [
                    state_mapping,
                    f"{mount_paths['logs'].as_posix()}:/opt/liferay/logs{z_label}",
                ]
            )
        else:
            liferay_env.extend(
                [
                    "LIFERAY_CLUSTER_PERIOD_LINK_PERIOD_ENABLED=true",
                    "LIFERAY_LUCENE_PERIOD_REPLICATE_PERIOD_WRITE=true",
                ]
            )

        if ssl_enabled:
            traefik_id = f"{project_name}-main"
            service["labels"].extend(
                [
                    "traefik.enable=true",
                    "traefik.docker.network=liferay-net",
                    f"traefik.http.routers.{traefik_id}.rule=Host(`{host_name}`)",
                    f"traefik.http.routers.{traefik_id}.tls=true",
                    f"traefik.http.routers.{traefik_id}.entrypoints=websecure",
                    f"traefik.http.routers.{traefik_id}.tls.domains[0].main={host_name}",
                    f"traefik.http.routers.{traefik_id}.tls.domains[0].sans=*.{host_name}",
                    f"traefik.http.services.{traefik_id}.loadbalancer.server.port=8080",
                ]
            )

        return service

    def _build_liferay_jvm_opts(self, meta):
        jvm_opts = str(meta.get("jvm_args", ""))
        if "-Dfile.encoding" not in jvm_opts:
            jvm_opts += " -Dfile.encoding=UTF8"
        if "-Duser.timezone" not in jvm_opts:
            jvm_opts += " -Duser.timezone=GMT"

        mandatory_opens = [
            "java.base/java.lang=ALL-UNNAMED",
            "java.base/java.lang.invoke=ALL-UNNAMED",
            "java.base/java.lang.reflect=ALL-UNNAMED",
            "java.base/java.net=ALL-UNNAMED",
            "java.base/java.util=ALL-UNNAMED",
            "java.base/java.util.concurrent=ALL-UNNAMED",
            "java.base/java.text=ALL-UNNAMED",
            "java.base/java.time=ALL-UNNAMED",
            "java.base/sun.net.www.protocol.http=ALL-UNNAMED",
            "java.base/sun.net.www.protocol.https=ALL-UNNAMED",
            "java.base/sun.nio.ch=ALL-UNNAMED",
            "java.base/sun.security.action=ALL-UNNAMED",
            "java.base/sun.security.ssl=ALL-UNNAMED",
            "java.base/sun.security.util=ALL-UNNAMED",
            "java.base/sun.security.x509=ALL-UNNAMED",
            "java.base/sun.util.calendar=ALL-UNNAMED",
            "java.management/sun.management=ALL-UNNAMED",
            "java.rmi/sun.rmi.transport=ALL-UNNAMED",
            "jdk.management/com.sun.management.internal=ALL-UNNAMED",
            "jdk.zipfs/jdk.nio.zipfs=ALL-UNNAMED",
        ]
        for opt in mandatory_opens:
            flag = f"--add-opens={opt}"
            if flag not in jvm_opts:
                jvm_opts += f" {flag}"

        return self._merge_jvm_opts(jvm_opts)

    def _merge_jvm_opts(self, jvm_opts):
        opt_map = {}
        for opt in jvm_opts.split(" "):
            if opt:
                if "=" in opt:
                    key = opt.split("=")[0]
                    opt_map[key] = opt
                else:
                    opt_map[opt] = opt

        return f"LIFERAY_JVM_OPTS={' '.join(opt_map.values())}"

    def _write_shared_search_config(self, paths, meta, project_name):
        """Writes the OSGi config that actually configures shared search (LDM-#1353).

        The `LIFERAY_ELASTICSEARCH*` environment variables above do **not**
        reach Liferay. `indexNamePrefix`, `productionModeEnabled` and the
        sidecar toggle are OSGi configuration on
        `com.liferay.portal.search.elasticsearch{N}.configuration.ElasticsearchConfiguration`
        -- confirmed from the metatype shipped in the image -- while `LIFERAY_*`
        variables map to *portal properties*, and `portal.properties` contains
        no `elasticsearch.*` key at all.

        Measured on a running project: with only the env vars set, Liferay
        started its embedded sidecar (`Sidecar Elasticsearch … started at
        127.0.0.1:9201`) and the global cluster stayed empty for 360s. The same
        values written here produced 18 `ldm-<project>-*` indices in
        `liferay-search-global` with no sidecar.

        The env vars are left in place deliberately: removing configuration
        that currently does nothing is tidying, not this fix, and it would
        inflate the diff.

        `indexNamePrefix` is written in the project's own case; Liferay
        lowercases it via `CompanyIdIndexNameBuilder.setIndexNamePrefix`
        (`StringUtil.trim` then `StringUtil.toLowerCase`), which is why the
        observed indices were `ldm-sharedidx-*` from a configured
        `ldm-SharedIdx-`.
        """
        from ldm_core.utils import safe_write_text, search_index_prefix

        is_es8 = self.manager.parse_version(meta.get("tag")) >= (2024, 1, 0)
        es_ver = "8" if is_es8 else "7"
        configs_dir = paths.get("configs") or (paths["root"] / "osgi" / "configs")
        target = (
            configs_dir
            / f"com.liferay.portal.search.elasticsearch{es_ver}.configuration.ElasticsearchConfiguration.config"
        )

        # Only meaningful because #1364 restored the osgi/configs mount; before
        # that this directory never reached the container.
        try:
            configs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # pragma: no cover - filesystem edge case
            UI.warning(f"Could not create {configs_dir}: {exc}")
            return

        contents = (
            'productionModeEnabled=B"true"\n'
            'networkHostAddresses=["http://liferay-search-global:9200"]\n'
            f'indexNamePrefix="{search_index_prefix(project_name)}"\n'
        )
        safe_write_text(target, contents)
        UI.debug(f"Wrote shared-search OSGi config: {target.name}")

    def _inject_liferay_search_env(
        self, meta, project_name, use_shared_search, liferay_env
    ):
        if use_shared_search:
            from ldm_core.utils import sanitize_id

            safe_project_name = sanitize_id(project_name)
            liferay_env.append(
                f"LIFERAY_ELASTICSEARCH7_PERIOD_INDEX_PERIOD_NAME_PERIOD_PREFIX=ldm_{safe_project_name}"
            )
            liferay_env.append(
                f"LIFERAY_ELASTICSEARCH8_PERIOD_INDEX_PERIOD_NAME_PERIOD_PREFIX=ldm_{safe_project_name}"
            )
            liferay_env.append(
                "LIFERAY_ELASTICSEARCH7_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=true"
            )
            liferay_env.append(
                "LIFERAY_ELASTICSEARCH8_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=true"
            )
            liferay_env.append(
                "LIFERAY_ELASTICSEARCH7_PERIOD_NETWORK_PERIOD_HOST_PERIOD_ADDRESSES=liferay-search-global:9200"
            )
            liferay_env.append(
                "LIFERAY_ELASTICSEARCH8_PERIOD_NETWORK_PERIOD_HOST_PERIOD_ADDRESSES=liferay-search-global:9200"
            )
        else:
            liferay_env.append(
                "LIFERAY_ELASTICSEARCH7_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=true"
            )
            liferay_env.append(
                "LIFERAY_ELASTICSEARCH8_PERIOD_PRODUCTION_PERIOD_MODE_PERIOD_ENABLED=true"
            )
            liferay_env.append(
                "LIFERAY_ELASTICSEARCH7_PERIOD_NETWORK_PERIOD_HOST_PERIOD_ADDRESSES=search:9200"
            )
            liferay_env.append(
                "LIFERAY_ELASTICSEARCH8_PERIOD_NETWORK_PERIOD_HOST_PERIOD_ADDRESSES=search:9200"
            )

    def _inject_liferay_custom_env(self, meta, liferay_env, base_env):
        if str(meta.get("reindex_required", "false")).lower() == "true":
            liferay_env.append("LIFERAY_INDEX_PERIOD_ON_PERIOD_STARTUP=true")

        has_jdbc_env = False
        if base_env:
            for env in base_env:
                if env.startswith("LIFERAY_JDBC_PERIOD_DEFAULT_PERIOD_"):
                    has_jdbc_env = True
                liferay_env.append(env)
        return has_jdbc_env

    def _inject_liferay_extensions_routes(self, paths, meta, project_name, liferay_env):
        extensions = []
        if self.manager and hasattr(self.manager, "workspace"):
            extensions = self.manager.workspace.scan_client_extensions(
                paths["root"],
                paths.get("cx"),
                paths.get("ce_dir"),
                host_name=meta.get("host_name"),
            )
        elif self.manager:
            from ldm_core.handlers.workspace import WorkspaceService

            cx_handler = WorkspaceService(self.manager)
            extensions = cx_handler.scan_client_extensions(
                paths["root"],
                paths.get("cx"),
                paths.get("ce_dir"),
                host_name=meta.get("host_name"),
            )

        for ext in extensions:
            if ext.get("deploy") and ext.get("is_service"):
                ext_id = ext.get("id")
                if ext_id:
                    svc_id = f"{project_name}-{ext_id}"
                    ms_port = next(
                        (
                            p.get("port")
                            for p in ext.get("ports", [])
                            if isinstance(p, dict) and p.get("external")
                        ),
                        ext.get("loadBalancer", {}).get("targetPort", 8080),
                    )
                    env_key = f"LIFERAY_ROUTES_CLIENT_EXTENSION_{ext_id.replace('-', '_').upper()}"
                    liferay_env.append(f"{env_key}=http://{svc_id}:{ms_port}")

    def _inject_liferay_db_env(
        self, paths, meta, project_name, tag, has_jdbc_env, liferay_env
    ):
        db_type = meta.get("db_type", "postgresql")
        baseline_default = "isolated"
        ldm_version = meta.get("ldm_version")
        if ldm_version and self.manager.parse_version(ldm_version) >= (2, 11, 75):
            baseline_default = "shared"

        if hasattr(self.manager, "defaults") and self.manager.defaults is not None:
            baseline_default = self.manager.defaults.get(
                "database_mode", baseline_default
            )

        from ldm_core.utils import resolve_infrastructure_mode

        # LDM-#1359: the CLI override MUST be passed here. `_build_db_service`
        # passes it and this did not, so within one run the two functions
        # disagreed about the mode: `_build_db_service` saw "shared" and
        # correctly omitted the per-project database service, while this fell
        # through to "isolated" and both wrote the isolated JDBC URL and left
        # `depends_on: <project>-db` in place (see the depends_on guard below,
        # which keys off this same `db_mode`). The result was a compose file
        # referencing an undefined service -- `ldm run --database-mode shared`
        # failed at `docker compose config` for every project, capitalised or
        # not, which is why #1354 and #1357 were never reached.
        db_mode = resolve_infrastructure_mode(
            "database_mode",
            meta,
            self.manager.defaults,
            getattr(getattr(self.manager, "args", None), "database_mode", None),
        )

        if db_mode == "shared":
            from ldm_core.ui import UI

            UI.detail("Utilizing Global Shared Infrastructure")

        if db_type == "hypersonic" and db_mode == "shared":
            from ldm_core.ui import UI

            UI.warning(
                "Hypersonic database cannot be shared. Enforcing 'isolated' mode."
            )
            db_mode = "isolated"

        db_updates = {}
        if db_type == "external":
            if not has_jdbc_env:
                db_updates = {
                    "jdbc.default.url": meta.get("jdbc_url", ""),
                    "jdbc.default.username": meta.get("jdbc_user", ""),
                    "jdbc.default.password": meta.get("jdbc_pass", ""),
                }
            liferay_env.append("LIFERAY_HSQL_PERIOD_ENABLED=false")
        elif db_type in ["mysql", "mariadb"]:
            from ldm_core.utils import resolve_dependency_version

            driver = (
                resolve_dependency_version(tag, "jdbc_driver_mysql")
                or "org.mariadb.jdbc.Driver"
            )
            # LDM-#1361: resolve the dialect the same way the driver and the
            # PostgreSQL branch already do. It was hardcoded to MariaDB103Dialect
            # while compatibility.json maps `jdbc_dialect_mysql` per tag range
            # and returns MySQL8Dialect for older ranges -- so an older tag was
            # given a dialect its mapping does not specify.
            dialect = (
                resolve_dependency_version(tag, "jdbc_dialect_mysql")
                or "org.hibernate.dialect.MariaDB103Dialect"
            )
            host = f"{project_name}-db"
            db_name = "lportal"
            if db_mode == "shared":
                # LDM-#1361: was hardcoded to `liferay-db-global`, i.e. this
                # MariaDB URL named the PostgreSQL container on port 3306 and
                # could never connect (LDM-#1357).
                from ldm_core.utils import shared_database_container

                host = shared_database_container(db_type)
                db_name = shared_database_name(project_name)

            url = (
                f"jdbc:mariadb://{host}:3306/{db_name}?"
                "characterEncoding=UTF-8"
                "&dontTrackOpenResources=true"
                "&holdResultsOpenOverStatementClose=true"
                "&serverTimezone=GMT"
                "&useFastDateParsing=false"
                "&useUnicode=true"
                "&useSSL=false"
                "&allowPublicKeyRetrieval=true"
                "&rewriteBatchedStatements=true"
                "&prepStmtCacheSize=1000"
                "&prepStmtCacheSqlLimit=2048"
                "&useLocalSessionState=true"
                "&useLocalTransactionState=true"
                "&permitMysqlScheme=true"
            )
            if not has_jdbc_env:
                db_updates = {
                    "jdbc.default.driverClassName": driver,
                    "jdbc.default.url": url,
                    "jdbc.default.username": "lportal",
                    "jdbc.default.password": "test",  # nosec B105
                    "hibernate.dialect": dialect,
                }
            liferay_env.append("LIFERAY_HSQL_PERIOD_ENABLED=false")
        elif db_type == "postgresql":
            from ldm_core.utils import resolve_dependency_version

            driver = (
                resolve_dependency_version(tag, "jdbc_driver_postgresql")
                or "org.postgresql.Driver"
            )
            url = f"jdbc:postgresql://{project_name}-db:5432/lportal"
            if db_mode == "shared":
                from ldm_core.utils import shared_database_container

                db_name = shared_database_name(project_name)
                url = f"jdbc:postgresql://{shared_database_container(db_type)}:5432/{db_name}"

            dialect = (
                resolve_dependency_version(tag, "jdbc_dialect_postgresql")
                or "org.hibernate.dialect.PostgreSQL10Dialect"
            )
            if not has_jdbc_env:
                db_updates = {
                    "jdbc.default.driverClassName": driver,
                    "jdbc.default.url": url,
                    "jdbc.default.username": "lportal",
                    "jdbc.default.password": "test",  # nosec B105
                    "hibernate.dialect": dialect,
                }
            liferay_env.append("LIFERAY_HSQL_PERIOD_ENABLED=false")

        if db_updates:
            # LDM-#1454: write the names Liferay actually reads.
            #
            # These were `jdbc.default.maxActive` / `minIdle` / `maxIdle`, which
            # are DBCP / Tomcat-JDBC names. Liferay uses HikariCP: verified by
            # extracting portal.properties from liferay/dxp:2026.q1.7-lts, where
            # the documented pool block is maximumPoolSize / minimumIdle /
            # connectionTimeout / idleTimeout / maxLifetime, and the three names
            # LDM wrote appear nowhere in all 12,085 lines.
            #
            # So the settings were accepted, written to portal-ext.properties,
            # and silently ignored -- every project ran on Liferay's built-in
            # defaults (maximumPoolSize=180, minimumIdle=10). Correcting the
            # names therefore gives these values effect for the FIRST time; the
            # figures below were chosen deliberately for a laptop running one
            # project, not inherited from keys nobody could have tested.
            #
            # `maxIdle` has no HikariCP equivalent -- Hikari has a single pool
            # size and governs idle connections with `idleTimeout` -- so
            # `db_max_idle` is superseded by `db_idle_timeout` and warned about
            # below rather than dropped, to avoid breaking an existing ~/.ldmrc.
            db_updates.update(self._hikari_pool_settings())
            self.manager.config.update_portal_ext(paths, db_updates)

        return db_type, db_mode

    def _hikari_pool_settings(self):
        """Connection-pool properties, under the names Liferay reads (LDM-#1454).

        Extracted from `_inject_liferay_db_env` rather than inlined: that method
        imports `UI` inside a conditional, which makes the name function-local,
        so an earlier reference raises UnboundLocalError. Keeping this separate
        also keeps that method under ruff's complexity limit.
        """
        db_max_active = "15"
        db_min_idle = "2"
        db_idle_timeout = "600000"

        defaults = getattr(self.manager, "defaults", None)
        if defaults is not None:
            db_max_active = defaults.get("db_max_active", "15")
            db_min_idle = defaults.get("db_min_idle", "2")
            db_idle_timeout = defaults.get("db_idle_timeout", "600000")

            # Superseded, not silently dropped: AGENTS.md forbids breaking an
            # existing ~/.ldmrc, but a setting that quietly stops working is the
            # defect this issue is about.
            if defaults.get("db_max_idle") is not None:
                UI.warning(
                    "'db_max_idle' no longer has an effect: Liferay uses "
                    "HikariCP, which has no maximum-idle setting. Use "
                    "'db_idle_timeout' (milliseconds) instead (LDM-#1454)."
                )

        return {
            "jdbc.default.maximumPoolSize": db_max_active,
            "jdbc.default.minimumIdle": db_min_idle,
            "jdbc.default.idleTimeout": db_idle_timeout,
        }

    def _inject_liferay_share_env(
        self, paths, meta, host_name, project_name, ssl_enabled, port
    ):
        is_share = (
            getattr(self.manager.args, "share", False) is True
            or str(meta.get("share", "false")).lower() == "true"
        )
        share_provider = (
            getattr(self.manager.args, "share_provider", None)
            or meta.get("share_provider")
            or "lfr-tunnel"
        )
        share_subdomain = (
            getattr(self.manager.args, "share_subdomain", None)
            or meta.get("share_subdomain")
            or project_name
        )

        share_host = None
        if is_share and share_provider in ["lfr-tunnel", "lfr-tunnel-docker"]:
            public_url = self.manager.share.resolve_public_tunnel_url(share_subdomain)
            if public_url:
                from urllib.parse import urlparse

                parsed = urlparse(public_url)
                share_host = parsed.netloc or parsed.path

        port_list = []
        if host_name == "localhost" or not ssl_enabled:
            bind_ip = self.manager.get_resolved_ip(host_name) or "127.0.0.1"
            port_list.append(f"{bind_ip}:{port}:8080")

        tunnel_managed_cors = getattr(
            self.manager.args, "tunnel_managed_cors", False
        ) or self.manager.config.get_global_config().get("tunnel_managed_cors", False)

        valid_hosts = f"localhost,127.0.0.1,{host_name},liferay"
        if not tunnel_managed_cors:
            # LDM-#1077: dynamic, not hardcoded -- a self-hosted Liferay
            # Tunnel deployment (~/.ldmrc "tunnel_base_domains" override)
            # needs its own gateway domain(s) allow-listed here too, or
            # requests proxied through it get rejected by Liferay's own
            # virtual-host validation.
            for known_domain in self.manager.share.get_known_tunnel_base_domains():
                valid_hosts += f",*.{known_domain}"

        forwarded_props = {
            "web.server.forwarded.host.header": "X-Forwarded-Host",
            "web.server.forwarded.port.header": "X-Forwarded-Port",
            "web.server.forwarded.proto.header": "X-Forwarded-Proto",
            "virtual.hosts.valid.hosts": valid_hosts,
        }

        if not tunnel_managed_cors and share_host:
            forwarded_props.update(
                {
                    "web.server.host": share_host,
                    "web.server.https.port": "443",
                    "web.server.protocol": "https",
                }
            )
        elif not tunnel_managed_cors and ssl_enabled:
            forwarded_props.update(
                {
                    "web.server.host": host_name,
                    "web.server.https.port": "443",
                    "web.server.protocol": "https",
                }
            )
        else:
            forwarded_props.update(
                {
                    "web.server.host": "",
                    "web.server.https.port": "",
                    "web.server.protocol": "",
                }
            )

        self.manager.config.update_portal_ext(paths, forwarded_props)
        return port_list

    def _build_ngrok_service(self, host_name, meta):
        is_expose = (
            getattr(self.manager.args, "expose", False) is True
            or str(meta.get("expose", "false")).lower() == "true"
        )
        if is_expose:
            auth_token = self.manager.config.get_ngrok_auth_token()
            if auth_token:
                return {
                    "image": "ngrok/ngrok:latest",
                    "networks": ["liferay-net"],
                    "environment": [f"NGROK_AUTHTOKEN={auth_token}"],
                    "command": [
                        "http",
                        "https://proxy:443",
                        f"--host-header={host_name}",
                    ],
                }
            from ldm_core.ui import UI

            UI.warning("ngrok authtoken not found, ngrok service will not be started.")
        return None

    def _build_lfr_tunnel_service(self, paths, meta, project_name):
        is_share = (
            getattr(self.manager.args, "share", False) is True
            or str(meta.get("share", "false")).lower() == "true"
        )
        share_provider = (
            getattr(self.manager.args, "share_provider", None)
            or meta.get("share_provider")
            or "lfr-tunnel"
        )
        if is_share and share_provider == "lfr-tunnel-docker":
            token = self.manager.share._get_auth_token()
            if token:
                subdomain = (
                    getattr(self.manager.args, "share_subdomain", None)
                    or meta.get("share_subdomain")
                    or project_name
                )
                import os

                share_domain = getattr(
                    self.manager.args, "share_domain", None
                ) or meta.get("share_domain")
                if not share_domain:
                    _, share_domain = self.manager.share.resolve_share_config(meta)

                server_url = os.environ.get("LFT_SERVER_URL")
                if not server_url and share_domain:
                    # LDM-#1077: the gateway itself is always a real Liferay
                    # base domain -- a custom vanity domain is public-URL-only.
                    server_url = self.manager.share.resolve_tunnel_gateway_url(
                        share_domain
                    )

                self._update_tunnel_env_file(
                    project_name, meta, subdomain, token, server_url, share_domain
                )

                share_inspector = (
                    getattr(self.manager.args, "share_inspector", False) is True
                    or str(meta.get("share_inspector", "false")).lower() == "true"
                )

                lfr_env = [
                    f"LFT_CLIENT_TOKEN=${{LFT_CLIENT_TOKEN:-{token}}}",
                    "LFT_TARGET_HOST=liferay",
                    f"LFT_CLIENT_SUBDOMAIN=${{LFT_SUBDOMAIN:-{subdomain}}}",
                    "LFT_PRESERVE_HOST=true",
                ]
                lfr_env.append("LFT_INSPECTOR_BIND=${LFT_INSPECTOR_BIND:-0.0.0.0}")

                if server_url:
                    lfr_env.append(
                        f"LFT_CLIENT_SERVER=${{LFT_SERVER_URL:-{server_url}}}"
                    )
                else:
                    # LDM-#1077: dynamic default, not a hardcoded literal --
                    # honors a ~/.ldmrc "tunnel_base_domains" override for
                    # self-hosted Liferay Tunnel deployments.
                    default_gateway = self.manager.share.get_default_tunnel_domain()
                    lfr_env.append(
                        f"LFT_CLIENT_SERVER=${{LFT_SERVER_URL:-https://tunnel.{default_gateway}}}"
                    )

                image = (
                    getattr(self.manager.args, "share_image", None)
                    or meta.get("share_image")
                    or "peterjrichards/lfr-tunnel:latest"
                )

                from ldm_core.utils import sanitize_id

                logs_dir = str(paths["root"] / "logs")
                tunnel_svc = {
                    "image": image,
                    "pull_policy": "always",
                    "container_name": sanitize_id(
                        meta.get("tunnel_container_name")
                        or f"{project_name}-lfr-tunnel"
                    ),
                    "networks": ["liferay-net"],
                    "environment": lfr_env,
                    "volumes": [f"{logs_dir}:/opt/liferay/logs"],
                    "entrypoint": [
                        "/bin/sh",
                        "-c",
                        f"./lfr-tunnel -ports {meta.get('share_ports', '8080')} 2>&1 | tee /opt/liferay/logs/lfr-tunnel.log",
                    ],
                    "deploy": {
                        "resources": {
                            "limits": {
                                "cpus": "0.10",
                                "memory": "50M",
                            },
                            "reservations": {
                                "cpus": "0.05",
                                "memory": "20M",
                            },
                        }
                    },
                    "depends_on": {"liferay": {"condition": "service_healthy"}},
                }
                if share_inspector:
                    tunnel_svc["ports"] = ["4040:4040"]
                return tunnel_svc
            from ldm_core.ui import UI

            UI.warning(
                "Liferay Tunnel token not found, lfr-tunnel service will not be started."
            )
        return None

    def _inject_ldm_labels(self, services, project_name, project_uuid=None):
        for _, svc_data in services.items():
            if "labels" not in svc_data:
                svc_data["labels"] = []

            if isinstance(svc_data["labels"], dict):
                svc_data["labels"] = [f"{k}={v}" for k, v in svc_data["labels"].items()]

            standard_labels = [
                f"com.liferay.ldm.project={project_name}",
                "com.liferay.ldm.managed=true",
            ]
            # LDM-#1395: see _named_volume_definition. Applied here rather than
            # at each individual builder because this is the one place that
            # sweeps every service.
            if project_uuid:
                standard_labels.append(f"com.liferay.ldm.project.uuid={project_uuid}")
            for label in standard_labels:
                if label not in svc_data["labels"]:
                    svc_data["labels"].append(label)

    def _merge_archetype_overlay(self, meta, compose):
        archetype_name = meta.get("archetype")
        if archetype_name:
            from ldm_core.constants import SCRIPT_DIR

            archetype_overlay_path = (
                SCRIPT_DIR
                / "ldm_core"
                / "resources"
                / "archetypes"
                / archetype_name
                / "compose-overlay.yml"
            )
            if archetype_overlay_path.exists():
                import yaml

                def deep_merge(dict1, dict2):
                    for key, val in dict2.items():
                        if isinstance(val, dict):
                            dict1[key] = deep_merge(dict1.get(key, {}), val)
                        elif isinstance(val, list):
                            dict1[key] = dict1.get(key, []) + val
                        else:
                            dict1[key] = val
                    return dict1

                try:
                    overlay_data = (
                        yaml.safe_load(
                            archetype_overlay_path.read_text(encoding="utf-8")
                        )
                        or {}
                    )
                    compose = deep_merge(compose, overlay_data)

                    if (
                        "liferay2" in compose["services"]
                        and "liferay" in compose["services"]
                    ):
                        compose["services"]["liferay2"]["image"] = compose["services"][
                            "liferay"
                        ]["image"]

                except Exception as e:
                    from ldm_core.ui import UI

                    UI.error(f"Failed to merge archetype overlay: {e}")

    def _inject_logging_limits(self, compose):
        max_size = "10m"
        max_file = "3"
        if hasattr(self.manager, "defaults") and self.manager.defaults is not None:
            max_size = self.manager.defaults.get("log_max_size", "10m")
            max_file = str(self.manager.defaults.get("log_max_file", "3"))

        logging_block = {
            "driver": "json-file",
            "options": {
                "max-size": max_size,
                "max-file": max_file,
            },
        }
        for svc_conf in compose.get("services", {}).values():
            if "logging" not in svc_conf:
                svc_conf["logging"] = logging_block

    def _build_search_service(self, meta):
        """Constructs the Sidecar Elasticsearch service if required."""
        # LDM-369: If sidecar is active, we do NOT want a separate search container.
        # Liferay will use its internal sidecar search inside the main container.
        return

    def _build_db_service(self, meta, project_name):
        """Constructs the Database service (MySQL/PostgreSQL) if required."""
        db_type = meta.get("db_type", "postgresql")
        from ldm_core.utils import resolve_infrastructure_mode

        db_mode = resolve_infrastructure_mode(
            "database_mode",
            meta or {},
            self.manager.defaults,
            getattr(self.manager.args, "database_mode", None),
        )

        if db_type == "external" or db_mode == "shared":
            if db_mode == "shared":
                UI.detail("Utilizing Global Shared Infrastructure")
            return None

        tag = str(meta.get("tag") or "latest")
        db_container = sanitize_id(
            meta.get("db_container_name") or f"{project_name}-db"
        )
        scale = int(meta.get("scale_db", 1))

        if db_type in ["postgresql", "postgres"]:
            pg_ver = resolve_dependency_version(tag, "postgresql") or "16"
            service = {
                "image": f"postgres:{pg_ver}",
                "command": [
                    "postgres",
                    "-c",
                    "shared_buffers=1024MB",
                    "-c",
                    "max_connections=200",
                ],
                "environment": {
                    "POSTGRES_PASSWORD": "test",  # nosec B105
                    "POSTGRES_USER": "lportal",
                    "POSTGRES_DB": "lportal",
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U lportal"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 10,
                    "start_period": "60s",
                },
                "networks": ["liferay-net"],
                "volumes": [f"{db_container}-db-data:/var/lib/postgresql/data"],
                "labels": [f"com.liferay.ldm.project={project_name}"],
            }
            if scale == 1:
                service["container_name"] = db_container
            else:
                service["deploy"] = {"replicas": scale}
            return service
        if db_type in ["mysql", "mariadb"]:
            is_modern = False
            try:
                major_ver = int(tag.split(".", maxsplit=1)[0])
                if major_ver >= 2024:
                    is_modern = True
            except (ValueError, IndexError):
                pass

            target_mysql = resolve_dependency_version(tag, "mysql")
            target_mariadb = resolve_dependency_version(tag, "mariadb")

            auth_flags = []
            if db_type == "mysql":
                mysql_image_ver = (
                    target_mysql if target_mysql else ("8.4" if is_modern else "5.7")
                )
                try:
                    ver_parts = mysql_image_ver.split(".")
                    if (
                        len(ver_parts) >= 2
                        and int(ver_parts[0]) == 8
                        and int(ver_parts[1]) >= 4
                    ):
                        auth_flags = ["--mysql-native-password=ON"]
                    else:
                        auth_flags = [
                            "--default-authentication-plugin=mysql_native_password"
                        ]
                except ValueError:
                    auth_flags = [
                        "--default-authentication-plugin=mysql_native_password"
                    ]

            image = (
                f"mysql:{mysql_image_ver}"
                if db_type == "mysql"
                else (f"mariadb:{target_mariadb}" if target_mariadb else "mariadb:10.6")
            )
            service = {
                "image": image,
                "command": [
                    "mysqld",
                    "--character-set-server=utf8mb4",
                    "--collation-server=utf8mb4_unicode_ci",
                    "--character-set-filesystem=utf8mb4",
                    "--lower_case_table_names=1",
                    "--bind-address=0.0.0.0",
                    "--skip-name-resolve",
                    *auth_flags,
                ],
                "environment": {
                    "MYSQL_ROOT_PASSWORD": "test",  # nosec B105
                    "MYSQL_USER": "lportal",
                    "MYSQL_PASSWORD": "test",  # nosec B105
                    "MYSQL_DATABASE": "lportal",
                    "MYSQL_TCP_PORT": "3306",
                },
                "healthcheck": {
                    "test": [
                        "CMD",
                        "mysqladmin",
                        "ping",
                        "-h",
                        "127.0.0.1",
                        "-uroot",
                        "-ptest",
                    ],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 10,
                    "start_period": "60s",
                },
                "networks": ["liferay-net"],
                "volumes": [f"{db_container}-db-data:/var/lib/mysql"],
                "labels": [f"com.liferay.ldm.project={project_name}"],
            }
            if scale == 1:
                service["container_name"] = db_container
            else:
                service["deploy"] = {"replicas": scale}
            return service
        return None

    def _build_extensions_services(  # noqa: C901, PLR0912
        self, paths, meta, host_name, project_name, ssl_enabled, mount_paths=None
    ):
        # LDM-#1134: see _build_liferay_service's docstring -- `paths` is
        # used for local filesystem scanning (scan_client_extensions must
        # read the actual extensions present on this host), `mount_paths`
        # is used exclusively for the `routes` bind-mount source below.
        if mount_paths is None:
            mount_paths = paths

        # 4. Append Microservices/Client Extensions
        services = {}
        extensions = []
        if self.manager and hasattr(self.manager, "workspace"):
            extensions = self.manager.workspace.scan_client_extensions(
                paths["root"],
                paths.get("cx"),
                paths.get("ce_dir"),
                host_name=meta.get("host_name"),
            )
        else:
            # Fallback for standalone/mock usage
            from ldm_core.handlers.workspace import WorkspaceService

            cx_handler = WorkspaceService(self.manager)
            extensions = cx_handler.scan_client_extensions(
                paths["root"],
                paths.get("cx"),
                paths.get("ce_dir"),
                host_name=meta.get("host_name"),
            )
        for ext in extensions:
            if ext.get("deploy") and ext.get("is_service"):
                ext_id = ext.get("id")
                svc_id = f"{project_name}-{ext_id}"
                ms_port = next(
                    (
                        p.get("port")
                        for p in ext.get("ports", [])
                        if isinstance(p, dict) and p.get("external")
                    ),
                    (ext.get("loadBalancer") or {}).get("targetPort", 8080),
                )
                scale = int(meta.get(f"scale_{ext_id}", 1))

                labels = [
                    "traefik.enable=true",
                    f"com.liferay.ldm.project={project_name}",
                ]
                services[svc_id] = {
                    "image": f"{svc_id}:latest",
                    "build": {"context": Path(ext["path"]).as_posix()},
                    "pull_policy": "build",
                    "networks": ["liferay-net"],
                    "labels": labels,
                    "volumes": [
                        f"{mount_paths.get('routes', mount_paths['root'] / 'routes').as_posix()}:/workspace/routes",
                    ],
                }

                env_vars = ext.get("env", {})
                env_list = [f"{k}={v}" for k, v in env_vars.items()]
                # Fix Node.js microservice SSL issues against local mkcert Traefik proxy
                if not any(
                    e.startswith("NODE_TLS_REJECT_UNAUTHORIZED=") for e in env_list
                ):
                    env_list.append("NODE_TLS_REJECT_UNAUTHORIZED=0")

                services[svc_id]["environment"] = env_list

                if scale == 1:
                    services[svc_id]["container_name"] = svc_id
                else:
                    services[svc_id]["deploy"] = {"replicas": scale}

                traefik_svc_id = f"{svc_id}-svc"
                labels.extend(
                    [
                        "traefik.docker.network=liferay-net",
                        f"traefik.http.routers.{traefik_svc_id}.rule=Host(`{ext['id']}.{host_name}`)",
                        f"traefik.http.services.{traefik_svc_id}.loadbalancer.server.port={ms_port}",
                    ]
                )
                if ssl_enabled:
                    labels.append(f"traefik.http.routers.{traefik_svc_id}.tls=true")

                services[svc_id]["labels"] = labels

                if not ssl_enabled:
                    bind_ip = meta.get("bind_ip", "0.0.0.0")  # nosec B104
                    resolved_port_str = meta.get(f"port_{ext_id}")
                    safe_host_port = 8080
                    if resolved_port_str:
                        try:
                            safe_host_port = int(resolved_port_str)
                        except ValueError:
                            if ms_port is not None:
                                try:
                                    safe_host_port = int(str(ms_port))
                                except ValueError:
                                    pass
                    elif ms_port is not None:
                        try:
                            safe_host_port = int(str(ms_port))
                        except ValueError:
                            pass

                    if safe_host_port in [80, 443, "80", "443"]:
                        safe_host_port = int(str(safe_host_port)) + 10000
                    elif safe_host_port in [8080, "8080"]:
                        safe_host_port = 28080
                    services[svc_id]["ports"] = [
                        f"{bind_ip}:{safe_host_port}:{ms_port}"
                    ]
        return services

    def _build_kibana_service(self, meta, project_name):
        """Constructs an optional Kibana service for index debugging."""
        from ldm_core.utils import resolve_infrastructure_mode

        search_mode = resolve_infrastructure_mode(
            "search_mode", meta, self.manager.defaults
        )

        # Determine Elasticsearch URL based on mode
        # By default, Sidecar search runs within the Liferay container itself, but
        # shared search runs in the global search container.
        if search_mode == "shared":
            es_url = "http://liferay-search-global:9200"
        else:
            # Sidecar exposes on liferay container
            es_url = f"http://{project_name}-liferay:9200"

        # Try to resolve a matching Kibana version
        tag = str(meta.get("tag") or "latest")
        from ldm_core.utils import resolve_dependency_version

        kibana_ver = resolve_dependency_version(tag, "elasticsearch") or "8.11.1"

        return {
            "image": f"kibana:{kibana_ver}",
            "container_name": f"{project_name}-kibana",
            "ports": ["5601:5601"],
            "environment": [f"ELASTICSEARCH_HOSTS={es_url}"],
            "networks": ["liferay-net"],
            "labels": [
                f"com.liferay.ldm.project={project_name}",
                "com.liferay.ldm.managed=true",
            ],
        }

    def _build_custom_containers(
        self, custom_containers, host_name, project_name, ssl_enabled, meta
    ):
        from typing import Any

        services: dict[str, Any] = {}
        if not isinstance(custom_containers, list):
            return services

        for container in custom_containers:
            image = container.get("image")
            if not image:
                continue

            c_name = container.get("service_name")
            if not c_name:
                continue

            svc_id = f"{project_name}-{c_name}"
            scale = int(meta.get(f"scale_{c_name}", 1))

            service = {
                "image": image,
                "networks": ["liferay-net"],
                "labels": [f"com.liferay.ldm.project={project_name}"],
            }

            if scale == 1:
                service["container_name"] = svc_id
            else:
                service["deploy"] = {"replicas": scale}

            depends_on_list = container.get("depends_on", [])
            if depends_on_list:
                service["depends_on"] = {
                    dep: {"condition": "service_started"} for dep in depends_on_list
                }

            env_vars = container.get("environment", [])
            if env_vars:
                if isinstance(env_vars, dict):
                    service["environment"] = [f"{k}={v}" for k, v in env_vars.items()]
                elif isinstance(env_vars, list):
                    service["environment"] = env_vars

            ports = container.get("ports", [])
            if ports:
                service["ports"] = ports

            volumes = container.get("volumes", [])
            if volumes:
                service["volumes"] = volumes

            subdomain = container.get("subdomain")
            if subdomain:
                service["labels"].append("traefik.enable=true")
                service["labels"].append("traefik.docker.network=liferay-net")
                traefik_svc_id = f"{svc_id}-svc"
                service["labels"].append(
                    f"traefik.http.routers.{traefik_svc_id}.rule=Host(`{subdomain}.{host_name}`)"
                )
                if ssl_enabled:
                    service["labels"].append(
                        f"traefik.http.routers.{traefik_svc_id}.tls=true"
                    )

            services[c_name] = service

        return services
