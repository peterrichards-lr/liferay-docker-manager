import logging
import secrets
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, Flask, abort, current_app, jsonify, request

from ldm_core.utils import run_command

bp = Blueprint("dashboard", __name__)


def create_app(manager, secret_key=None):
    app = Flask(__name__)
    app.secret_key = secret_key or secrets.token_hex(32)
    app.config["MANAGER"] = manager

    @app.before_request
    def check_csrf_or_auth():
        if request.method in ["POST", "PUT", "DELETE"]:
            token = request.headers.get("X-LDM-Token")
            if not token or token != app.secret_key:
                abort(403, description="Forbidden: Invalid or missing API Token")

    app.register_blueprint(bp)
    return app


def start_server(manager, host, port, secret_key=None):
    # Suppress Flask default logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

    app = create_app(manager, secret_key=secret_key)
    app.run(host=host, port=port, debug=False)


def _find_project_path(project_name):
    manager = current_app.config["MANAGER"]
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


@bp.route("/api/projects")
def api_projects():
    manager = current_app.config["MANAGER"]
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

        # Scan for client extensions
        extensions_data = []
        paths = manager.setup_paths(path)
        if paths["cx"].exists():
            from ldm_core.handlers.workspace import WorkspaceService

            handler = WorkspaceService(manager)
            extensions = handler.scan_client_extensions(
                paths["root"], paths["cx"], paths["ce_dir"]
            )
        else:
            extensions = meta.get("extensions", [])
            if isinstance(extensions, str):
                try:
                    import json

                    extensions = json.loads(extensions)
                except Exception:
                    import traceback

                    traceback.print_exc()
                    extensions = []

        is_shared = meta.get("share") or meta.get("share_provider")
        share_subdomain = meta.get("share_subdomain")
        share_domain = meta.get("share_domain", "lfr-demo.online")

        fetched_urls = []
        if is_shared and share_subdomain:
            fetched_urls = manager.share.resolve_public_tunnel_urls(
                share_subdomain, name
            )

        for ext in extensions:
            if isinstance(ext, dict) and ext.get("is_service"):
                ext_id = ext.get("id")
                ext_name = f"{name}-{ext_id}"

                local_url = f"http://{ext_id}.{host_name}:8080"
                public_url = None
                if is_shared and share_subdomain:
                    for u in fetched_urls:
                        if f"-{ext_id}." in u:
                            public_url = u
                            break
                    if not public_url:
                        public_url = (
                            f"https://{share_subdomain}-{ext_id}.{share_domain}"
                        )

                extensions_data.append(
                    {
                        "id": ext_id,
                        "name": ext_name,
                        "local_url": local_url,
                        "public_url": public_url,
                    }
                )

        projects.append(
            {
                "name": name,
                "version": r["version"],
                "status": status,
                "url": url,
                "path": str(path),
                "db_type": meta.get("db_type", "N/A"),
                "archetype": meta.get("archetype", "None"),
                "client_extensions": extensions_data,
            }
        )

    return jsonify(projects)


@bp.route("/api/projects/<project_name>/start", methods=["POST"])
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


@bp.route("/api/projects/<project_name>/stop", methods=["POST"])
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


@bp.route("/api/projects/<project_name>/snapshots")
def api_list_snapshots(project_name):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    path = _find_project_path(project_name)
    if not path:
        return jsonify({"error": "Project not found"}), 404

    manager = current_app.config["MANAGER"]
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
                "includes_database": meta.get("includes_database") in [True, "true"],
                "includes_volume_assets": meta.get("includes_volume_assets")
                in [True, "true"],
                "includes_client_extensions": meta.get("includes_client_extensions")
                in [True, "true"],
                "includes_osgi_modules": meta.get("includes_osgi_modules")
                in [True, "true"],
            }
        )

    return jsonify(result)


@bp.route("/api/projects/<project_name>/snapshot", methods=["POST"])
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


@bp.route("/api/projects/<project_name>/restore/<snapshot_id>", methods=["POST"])
def api_restore_snapshot(project_name, snapshot_id):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400
    if not re.match(r"^[a-zA-Z0-9_.-]+$", snapshot_id):
        return jsonify({"error": "Invalid snapshot ID format"}), 400

    path = _find_project_path(project_name)
    if not path:
        return jsonify({"error": "Project not found"}), 404

    manager = current_app.config["MANAGER"]
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


@bp.route("/api/logs/<project_name>")
def api_logs(project_name):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    manager = current_app.config["MANAGER"]
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


@bp.route("/api/projects/<project_name>/properties")
def api_project_properties(project_name):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    path = _find_project_path(project_name)
    if not path:
        return jsonify({"error": "Project not found"}), 404

    try:
        manager = current_app.config["MANAGER"]
        paths = manager.setup_paths(path)
        project_meta = manager.read_meta(path) or {}
        # Load layers
        seed_ext = (
            Path(__file__).parent.parent
            / "resources"
            / "common_baseline"
            / "portal-ext.properties"
        )
        seed_props, seed_imp = {}, set()
        if seed_ext.exists():
            seed_props, seed_imp = manager.config._get_properties_with_metadata(
                seed_ext.read_text()
            )

        ldmp_ext = paths["root"] / ".liferay-docker" / "ldmp-portal-ext.properties"
        ldmp_props, ldmp_imp = {}, set()
        if ldmp_ext.exists():
            ldmp_props, ldmp_imp = manager.config._get_properties_with_metadata(
                ldmp_ext.read_text()
            )

        global_props, global_imp = {}, set()
        local_props, local_imp = {}, set()
        for cd in paths.get("common_dirs", []):
            cd_ext = cd / "portal-ext.properties"
            if cd_ext.exists():
                c_props, c_imp = manager.config._get_properties_with_metadata(
                    cd_ext.read_text()
                )
                if (
                    ".ldm" in str(cd.resolve())
                    or "home" in str(cd.resolve())
                    or "global" in str(cd.resolve()).lower()
                ):
                    global_props, global_imp = c_props, c_imp
                else:
                    local_props, local_imp = c_props, c_imp

        target_ext = paths["files"] / "portal-ext.properties"
        project_props, project_imp = {}, set()
        if target_ext.exists():
            project_props, project_imp = manager.config._get_properties_with_metadata(
                target_ext.read_text()
            )

        # Compute Project Baseline = Seed + LDMP
        baseline_props = dict(seed_props)
        baseline_props.update(ldmp_props)
        baseline_imp = set(seed_imp)
        for k in ldmp_props:
            if k in ldmp_imp:
                baseline_imp.add(k)
            else:
                baseline_imp.discard(k)

        # Compute Project Customizations (Layer 5 effective)
        project_custom_props = {}
        project_custom_imp = set()
        for k, v in project_props.items():
            if k not in baseline_props:
                project_custom_props[k] = v
                if k in project_imp:
                    project_custom_imp.add(k)
            else:
                val_differs = v != baseline_props[k]
                imp_differs = (k in project_imp) != (k in baseline_imp)
                if val_differs or imp_differs:
                    project_custom_props[k] = v
                    if k in project_imp:
                        project_custom_imp.add(k)

        # Build system/runtime injections (host_updates)
        host_updates = {}
        no_captcha = str(project_meta.get("no_captcha", "false")).lower() == "true"
        host_updates["captcha.enforce.disabled"] = "true" if no_captcha else "false"
        fast_login = str(project_meta.get("fast_login", "false")).lower() == "true"
        if fast_login:
            host_updates.update(
                {
                    "captcha.check.portal.create_account": "false",
                    "captcha.check.portal.send_password": "false",  # pragma: allowlist secret
                    "company.security.strangers.verify": "false",
                    "enterprise.product.notification.enabled": "false",
                    "live.users.enabled": "true",
                    "passwords.default.policy.change.required": "false",
                    "passwords.passwordpolicytoolkit.generator": "static",
                    "passwords.passwordpolicytoolkit.static": "test",
                    "setup.wizard.enabled": "false",
                    "terms.of.use.required": "false",
                    "users.last.name.required": "false",
                    "users.reminder.queries.custom.question.enabled": "false",
                    "users.reminder.queries.enabled": "false",
                }
            )

        global_config = manager.config.get_global_config()
        global_features = global_config.get("features", "")
        project_features = project_meta.get("features", "")
        all_features = set()
        for f_list in [global_features, project_features]:
            if f_list:
                for f in f_list.split(","):
                    if f.strip():
                        all_features.add(f.strip())
        for f in sorted(all_features):
            if f.lower() in ["dev", "beta", "release"]:
                host_updates[f"feature.flag.ui.visible[{f.lower()}]"] = "true"
            else:
                host_updates[f"feature.flag.{f}"] = "true"

        admin_mappings = {
            "admin_password": "default.admin.password",  # pragma: allowlist secret
            "admin_screen_name": "default.admin.screen.name",
            "admin_email_prefix": "default.admin.email.address.prefix",
            "admin_first_name": "default.admin.first.name",
            "admin_middle_name": "default.admin.middle.name",
            "admin_last_name": "default.admin.last.name",
        }
        for config_key, portal_key in admin_mappings.items():
            val = global_config.get(config_key)
            if val is not None:
                host_updates[portal_key] = val

        # Merge all keys
        all_keys = (
            set(seed_props.keys())
            | set(ldmp_props.keys())
            | set(global_props.keys())
            | set(local_props.keys())
            | set(project_custom_props.keys())
            | set(host_updates.keys())
        )

        result = []
        for k in sorted(all_keys):
            entries = []
            if k in seed_props:
                entries.append((1, "seed", seed_props[k], k in seed_imp))
            if k in ldmp_props:
                entries.append((2, "ldmp", ldmp_props[k], k in ldmp_imp))
            if k in global_props:
                entries.append((3, "global", global_props[k], k in global_imp))
            if k in local_props:
                entries.append((4, "local", local_props[k], k in local_imp))
            if k in project_custom_props:
                entries.append(
                    (5, "project", project_custom_props[k], k in project_custom_imp)
                )
            if k in host_updates:
                entries.append((6, "system", host_updates[k], False))

            important_entries = [e for e in entries if e[3]]
            if important_entries:
                winner = max(important_entries, key=lambda x: x[0])
            else:
                winner = max(entries, key=lambda x: x[0])

            result.append(
                {
                    "key": k,
                    "value": winner[2],
                    "important": winner[3],
                    "source": winner[1],
                    "history": {
                        "seed": {
                            "val": seed_props.get(k),
                            "imp": k in seed_imp,
                            "defined": k in seed_props,
                        },
                        "ldmp": {
                            "val": ldmp_props.get(k),
                            "imp": k in ldmp_imp,
                            "defined": k in ldmp_props,
                        },
                        "global": {
                            "val": global_props.get(k),
                            "imp": k in global_imp,
                            "defined": k in global_props,
                        },
                        "local": {
                            "val": local_props.get(k),
                            "imp": k in local_imp,
                            "defined": k in local_props,
                        },
                        "project": {
                            "val": project_custom_props.get(k),
                            "imp": k in project_custom_imp,
                            "defined": k in project_custom_props,
                        },
                        "system": {
                            "val": host_updates.get(k),
                            "imp": False,
                            "defined": k in host_updates,
                        },
                    },
                }
            )
        return jsonify(result)
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify(
            {"error": f"Failed to retrieve properties: {e.__class__.__name__}"}
        ), 500


@bp.route("/api/projects/<project_name>/properties", methods=["PUT"])
def api_update_project_property(project_name):
    import contextlib
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    path = _find_project_path(project_name)
    if not path:
        return jsonify({"error": "Project not found"}), 404

    data = request.get_json() or {}
    key = data.get("key")
    value = data.get("value")
    important = bool(data.get("important", False))

    if not key or not isinstance(key, str):
        return jsonify({"error": "Property key is required and must be a string"}), 400
    if not re.match(r"^[a-zA-Z0-9_.-]+$", key):
        return jsonify({"error": "Invalid property key format"}), 400
    if value is None:
        return jsonify({"error": "Property value is required"}), 400

    try:
        manager = current_app.config["MANAGER"]
        paths = manager.setup_paths(path)
        # Update the properties file
        updates = {key: str(value)}
        important_keys = {key} if important else None

        # Ensure 'files' directory exists
        with contextlib.suppress(PermissionError, OSError):
            paths["files"].mkdir(exist_ok=True)

        manager.config.update_portal_ext(paths, updates, important_keys=important_keys)

        # Rebuild/sync properties
        manager.config.cmd_rebuild_properties(project_name)

        # Return updated properties list
        return api_project_properties(project_name)
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify(
            {"error": f"Failed to update property: {e.__class__.__name__}"}
        ), 500


@bp.route("/api/projects/<project_name>/properties/<key>", methods=["DELETE"])
def api_delete_project_property(project_name, key):
    import re

    if not re.match(r"^[a-zA-Z0-9_-]+$", project_name):
        return jsonify({"error": "Invalid project name format"}), 400

    path = _find_project_path(project_name)
    if not path:
        return jsonify({"error": "Project not found"}), 404

    if not key or not re.match(r"^[a-zA-Z0-9_.-]+$", key):
        return jsonify({"error": "Invalid property key format"}), 400

    try:
        manager = current_app.config["MANAGER"]
        paths = manager.setup_paths(path)
        # Surgically remove property override from files/portal-ext.properties
        manager.config.remove_portal_ext(paths, {key})

        # Rebuild/sync properties
        manager.config.cmd_rebuild_properties(project_name)

        # Return updated properties list
        return api_project_properties(project_name)
    except Exception as e:
        import traceback

        traceback.print_exc()
        return jsonify(
            {"error": f"Failed to delete property: {e.__class__.__name__}"}
        ), 500


@bp.route("/")
def index():
    from ldm_core.constants import SCRIPT_DIR

    html_path = SCRIPT_DIR / "ldm_core" / "resources" / "dashboard" / "index.html"
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        secret_key = current_app.secret_key
        if isinstance(secret_key, bytes):
            secret_key_str = secret_key.decode("utf-8")
        else:
            secret_key_str = str(secret_key or "")

        return html.replace(
            "<!-- LDM_TOKEN_PLACEHOLDER -->",
            f'<meta name="ldm-token" content="{secret_key_str}">',
        )
    return "Dashboard UI not found.", 404
