import logging

from flask import Flask, jsonify

from ldm_core.utils import run_command

app = Flask(__name__)
# Suppress Flask default logging
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)


def start_server(manager, host, port):
    app.config["MANAGER"] = manager
    app.run(host=host, port=port, debug=False)


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


@app.route("/api/logs/<project_name>")
def api_logs(project_name):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    manager = app.config["MANAGER"]
    # Verify the project exists
    roots = manager.find_dxp_roots()
    project_path = None
    container_name = project_name

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
            container_name = name
            break

    if not project_path:
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
