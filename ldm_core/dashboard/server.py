import logging
import subprocess
import sys
from pathlib import Path

from flask import Flask, jsonify, request

from ldm_core.utils import run_command

app = Flask(__name__)
# Suppress Flask default logging
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)


def start_server(manager, host, port):
    app.config["MANAGER"] = manager
    app.run(host=host, port=port, debug=False)


def _find_project_path(project_name):
    manager = app.config["MANAGER"]
    roots = manager.find_dxp_roots()
    for r in roots:
        path = r["path"]
        meta = manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )
        if name == project_name:
            return path
    return None


def get_dir_size(path):
    total: float = 0.0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except Exception:
        return "unknown"
    for unit in ["B", "KB", "MB", "GB"]:
        if total < 1024:
            return f"{total:.1f} {unit}"
        total /= 1024
    return f"{total:.1f} TB"


def run_background_ldm_cmd(args_list):
    cmd = [sys.executable, str(Path(sys.argv[0]).resolve()), *args_list]
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            cmd,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


@app.route("/api/projects")
def api_projects():
    manager = app.config["MANAGER"]
    roots = manager.find_dxp_roots()
    projects = []

    for r in roots:
        path = r["path"]
        meta = manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )

        status = "Stopped"
        containers_status = run_command(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name=^{name}$",
                "--format",
                "{{.State}}",
            ],
            check=False,
        )
        if containers_status:
            states = containers_status.splitlines()
            if states:
                status = states[0].capitalize()

        host_name = meta.get("host_name", "localhost")
        port = meta.get("port", "8080")
        ssl = str(meta.get("ssl", "false")).lower() == "true"
        url = f"https://{host_name}" if ssl else f"http://{host_name}:{port}"

        projects.append(
            {
                "name": name,
                "version": r["version"],
                "status": status,
                "url": url,
                "path": str(path),
                "db_type": meta.get("db_type", "N/A"),
                "archetype": meta.get("archetype", "None"),
            }
        )

    return jsonify(projects)


@app.route("/api/projects/<project_name>/start", methods=["POST"])
def api_start_project(project_name):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    path = _find_project_path(project_name)
    if not path:
        return jsonify({"error": "Project not found"}), 404

    run_background_ldm_cmd(["run", project_name, "-y"])
    return jsonify(
        {"status": "Starting", "message": "Project startup initiated in background."}
    )


@app.route("/api/projects/<project_name>/stop", methods=["POST"])
def api_stop_project(project_name):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    path = _find_project_path(project_name)
    if not path:
        return jsonify({"error": "Project not found"}), 404

    run_background_ldm_cmd(["stop", project_name, "-y"])
    return jsonify(
        {"status": "Stopping", "message": "Project shutdown initiated in background."}
    )


@app.route("/api/projects/<project_name>/snapshots")
def api_list_snapshots(project_name):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    path = _find_project_path(project_name)
    if not path:
        return jsonify({"error": "Project not found"}), 404

    manager = app.config["MANAGER"]
    paths = manager.setup_paths(path)
    backups_dir = paths.get("backups")

    if not backups_dir or not backups_dir.exists():
        return jsonify([])

    backups = sorted(
        [d for d in backups_dir.iterdir() if d.is_dir()],
        key=lambda x: x.name,
        reverse=True,
    )

    result = []
    for b in backups:
        meta = manager.read_meta(b / "meta") or {}
        size = get_dir_size(b)
        result.append(
            {
                "id": b.name,
                "name": meta.get("name", "Untitled"),
                "timestamp": b.name,
                "size": size,
            }
        )

    return jsonify(result)


@app.route("/api/projects/<project_name>/snapshot", methods=["POST"])
def api_create_snapshot(project_name):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    path = _find_project_path(project_name)
    if not path:
        return jsonify({"error": "Project not found"}), 404

    data = request.get_json(silent=True) or {}
    snapshot_name = data.get("name")

    args = ["snapshot", project_name, "-y"]
    if snapshot_name:
        args += ["--name", snapshot_name]

    run_background_ldm_cmd(args)
    return jsonify(
        {"status": "Creating", "message": "Snapshot creation initiated in background."}
    )


@app.route("/api/projects/<project_name>/restore/<snapshot_id>", methods=["POST"])
def api_restore_snapshot(project_name, snapshot_id):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400
    if not re.match(r"^[a-zA-Z0-9_.-]+$", snapshot_id):
        return jsonify({"error": "Invalid snapshot ID format"}), 400

    path = _find_project_path(project_name)
    if not path:
        return jsonify({"error": "Project not found"}), 404

    manager = app.config["MANAGER"]
    paths = manager.setup_paths(path)
    backups_dir = paths.get("backups")

    if not backups_dir or not backups_dir.exists():
        return jsonify({"error": "No snapshots found for this project"}), 404

    backups = sorted(
        [d for d in backups_dir.iterdir() if d.is_dir()],
        key=lambda x: x.name,
        reverse=True,
    )

    try:
        idx = [b.name for b in backups].index(snapshot_id) + 1
    except ValueError:
        return jsonify({"error": "Snapshot not found"}), 404

    run_background_ldm_cmd(["restore", project_name, "--index", str(idx), "-y"])
    return jsonify(
        {
            "status": "Restoring",
            "message": "Snapshot restoration initiated in background.",
        }
    )


@app.route("/api/logs/<project_name>")
def api_logs(project_name):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    manager = app.config["MANAGER"]
    # Verify the project exists
    roots = manager.find_dxp_roots()
    project_path = None
    container_name = None

    for r in roots:
        path = r["path"]
        meta = manager.read_meta(path)
        name = (
            meta.get("liferay_container_name")
            or meta.get("container_name")
            or path.name
        )
        if name == project_name:
            project_path = path
            container_name = str(name)
            break

    if not project_path or not container_name:
        return jsonify({"error": "Project not found"}), 404

    logs = run_command(
        ["docker", "logs", "--tail", "200", container_name],
        check=False,
    )
    return jsonify({"logs": logs or "No logs available or container not running."})


@app.route("/")
def index():
    from ldm_core.constants import SCRIPT_DIR

    html_path = SCRIPT_DIR / "ldm_core" / "resources" / "dashboard" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "Dashboard UI not found.", 404
