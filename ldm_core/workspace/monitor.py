import os
import platform
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from ldm_core.ui import UI
from ldm_core.utils import (
    atomic_copy,
)


def cmd_monitor(self, source_path=None, project_id=None):  # noqa: C901, PLR0912, PLR0915
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
        from watchdog.observers.polling import PollingObserverVFS
    except ImportError:
        UI.die("watchdog required: pip install watchdog")

    project_id = (
        project_id
        or getattr(self.manager.args, "project", None)
        or self.manager.detect_project_path()
    )
    if not project_id:
        UI.die(
            "No project specified and no project found in current directory. "
            "Use 'ldm monitor <project_name>' or navigate to a project folder."
        )

    paths = self.manager.setup_paths(project_id)
    project_meta = self.manager.read_meta(paths["root"])

    if not source_path:
        source_path = project_meta.get("workspace_path")
        if not source_path:
            UI.die("No workspace path provided and project is not linked to a source.")
        UI.detail(f"Using linked workspace: {source_path}")

    source = Path(source_path).resolve()
    from ldm_core.utils import is_lcp_workspace

    workspace_root = (
        source / "liferay"
        if (source / "liferay").exists() and is_lcp_workspace(source)
        else source
    )
    UI.heading(f"Monitoring: {workspace_root.name}")

    class WorkspaceEventHandler(FileSystemEventHandler):
        def __init__(self, manager, workspace_root, paths, project_meta, delay):
            (
                self.manager,
                self.workspace_root,
                self.paths,
                self.project_meta,
                self.delay,
            ) = (manager, workspace_root, paths, project_meta, delay)
            self.timer, self.pending_files, self.lock = (
                None,
                set(),
                threading.Lock(),
            )

        def on_created(self, event):
            if not event.is_directory:
                self._handle_event(event.src_path)

        def on_modified(self, event):
            if not event.is_directory:
                self._handle_event(event.src_path)

        def _handle_event(self, path):
            p = Path(path)

            # Performance Optimization: Skip massive build/node_modules directories early
            if any(x in p.parts for x in ["build", "node_modules", ".gradle", ".lsp"]):
                # We only care about the specific artifacts in 'dist' or 'libs'
                if not any(x in p.parts for x in ["dist", "libs"]):
                    UI.detail(f"Monitor: Skipping deep build file: {p.name}")
                    return

            # Refined Filtering Logic:
            # 1. client-extensions/**/*.zip
            # 2. fragments/**/*.zip
            # 3. modules/*/build/libs/*.jar

            is_valid = False
            if p.suffix.lower() == ".zip":
                if "client-extensions" in p.parts or "fragments" in p.parts:
                    is_valid = True
            elif p.suffix.lower() in [".jar", ".war"] and (
                "modules" in p.parts and "build" in p.parts and "libs" in p.parts
            ):
                is_valid = True

            if is_valid:
                UI.detail(f"Monitor: Detected valid artifact: {p.name}")
                with self.lock:
                    self.pending_files.add(p)
                    if self.timer:
                        self.timer.cancel()
                    self.timer = threading.Timer(self.delay, self._process_pending)
                    self.timer.start()
            else:
                UI.detail(f"Monitor: Ignoring non-artifact change: {p.name}")

        def _process_pending(self):
            with self.lock:
                files, self.pending_files, self.timer = (
                    list(self.pending_files),
                    set(),
                    None,
                )
            if not files:
                return

            updated_services = set()
            for f in files:
                # 1. Determine action based on type
                if f.suffix.lower() == ".zip":
                    # Client Extension
                    self.manager._sync_cx_artifact(f, self.paths)
                    if "client-extensions" in f.parts:
                        # Only trigger targeted deploy if it's a Docker-based service (SSCE)
                        svc_id = f.stem.lower().replace("_", "-")
                        target_folder = self.paths["ce_dir"] / f.stem
                        if (target_folder / "Dockerfile").exists():
                            updated_services.add(svc_id)
                else:
                    # JARs for Liferay modules (sync to deploy)
                    dest_path = self.paths["deploy"] / f.name
                    UI.detail(f"Syncing Module: {f.name}")
                    atomic_copy(f, dest_path)

            # 3. Trigger deployment from the project's internal state
            if updated_services:
                for svc in updated_services:
                    self.manager.runtime.cmd_deploy(service=svc)
            else:
                self.manager.runtime.cmd_deploy()

            UI.success("Deployment complete.")

    # On macOS, Native Observer (Kqueue) often hits file descriptor limits
    # because it requires an open file handle for every directory.
    # PollingObserver is much safer for large workspace monitoring.
    # Polling Optimization: Exclude massive dependencies to reduce file/stat overhead
    ignored_dirs = {
        "node_modules",
        "build",
        ".gradle",
        ".git",
        ".idea",
        ".vscode",
        "backup",
        "ci",
        "database",
        "search",
        "webserver",
    }

    def filtered_scandir(path=None):
        for entry in os.scandir(path):
            if entry.is_dir(follow_symlinks=False) and entry.name in ignored_dirs:
                continue
            yield entry

    is_mac = platform.system().lower() == "darwin"
    delay = float(getattr(self.manager.args, "delay", 2.0))

    if is_mac:
        # Proactively increase file descriptor limits for this process
        try:
            import resource

            _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)

            # Try to set a generous limit (e.g., 4096)
            new_soft = min(hard, 4096)
            resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))

            if self.manager.verbose:
                UI.detail(f"OS File Limits: Soft={new_soft}, Hard={hard}")
        except Exception:
            pass

        if self.manager.verbose:
            UI.detail("Using PollingObserver for macOS stability.")
        observer = PollingObserverVFS(
            stat=os.stat,
            listdir=filtered_scandir,
            polling_interval=delay,  # type: ignore[arg-type]
        )
    else:
        observer = Observer()  # type: ignore[assignment]

    UI.detail("Scanning for workspace branches...")
    watch_targets = []
    allowed_branches = ["client-extensions", "modules", "fragments"]

    for branch in allowed_branches:
        target = workspace_root / branch
        if target.exists():
            watch_targets.append(target)
            UI.detail(f"  + Watching: {branch}")

    if not watch_targets:
        watch_targets = [workspace_root]

    handler = WorkspaceEventHandler(
        self,
        workspace_root,
        paths,
        project_meta,
        float(getattr(self.manager.args, "delay", 2.0)),
    )

    for target in watch_targets:
        try:
            # We now watch branches recursively but the handler filters precisely
            observer.schedule(handler, str(target), recursive=True)
        except OSError as e:
            if e.errno == 24:  # Too many open files
                if not is_mac:
                    UI.error("Hit system file limit. Switching to PollingObserver...")
                    # Switch to polling for this and future targets
                    if not isinstance(observer, PollingObserverVFS):
                        observer.stop()
                        observer = PollingObserverVFS(
                            stat=os.stat,
                            listdir=filtered_scandir,
                            polling_interval=delay,
                        )
                        observer.schedule(handler, str(target), recursive=True)
                        observer.start()
                else:
                    UI.die(
                        f"Fatal: OS file limit reached even with Polling. Path: {target}"
                    )
            else:
                raise e

    observer.start()

    try:
        UI.detail("Watching for changes (Press Ctrl+C to stop)...")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        UI.detail("Monitor stopped.")
    observer.join()
