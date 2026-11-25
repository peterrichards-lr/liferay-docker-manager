"""`ldm share status --json` (LDM-#2525).

Kept apart from `ShareService.cmd_status`, which renders three providers'
answers as formatted text: this module reads the same sources and reports one
stable object, so an agent never has to scrape the tunnel's address out of
coloured output. Schema in docs/reference/cli/core.md.

Read-only. It never installs a client, never prompts, and never starts or
stops anything -- a status query that could download a binary is not one an
agent can run safely.
"""

import contextlib
import json
import os
import subprocess
import sys

SHARE_STATUS_JSON_SCHEMA_VERSION = 1


def _first_list(*candidates):
    for value in candidates:
        if isinstance(value, list) and value:
            return [str(v) for v in value]
    return []


def _from_live_api(share, port=4040):
    """The running client's own API -- the most current answer there is."""
    api = share._get_tunnel_api_state(port)
    info = api.get("info") or {}
    state = api.get("state") or {}
    if not (info or state):
        return None
    public_urls = _first_list(state.get("public_urls"), info.get("public_urls"))
    if not public_urls and state.get("public_url"):
        public_urls = [str(state["public_url"])]
    connected = bool(state.get("connected"))
    return {
        "source": "live-api",
        "subdomain": state.get("subdomain")
        or (info.get("subdomain") or {}).get("name"),
        "status": info.get("status") or ("healthy" if connected else "disconnected"),
        "connection_state": state.get("connection_state")
        or (info.get("connection") or {}).get("state")
        or "disconnected",
        "public_urls": public_urls,
        "inspector_url": f"http://127.0.0.1:{port}",
    }


def _from_status_json(argv, source):
    """A client's `-status-json`, run with a short timeout."""
    try:
        res = subprocess.run(
            argv, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (res.stdout or "").strip()
    if not (out.startswith("{") and out.endswith("}")):
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    inspector_port = data.get("inspector_port")
    return {
        "source": source,
        "status": data.get("status") or "unknown",
        "connection_state": data.get("connection_state") or "disconnected",
        "public_urls": _first_list(data.get("public_urls")),
        "inspector_url": (
            f"http://localhost:{inspector_port}" if inspector_port else None
        ),
    }


def build_share_status_payload(share, project_id=None):
    """The `share status --json` object for ``project_id`` (or the cwd project)."""
    from ldm_core.docker_service import DockerService

    manager = share.manager
    root = manager.detect_project_path(project_id)
    meta = manager.read_meta(root) if root else {}
    provider = meta.get("share_provider") or None
    subdomain = (
        meta.get("share_subdomain") or project_id or (root.name if root else None)
    )

    found = None
    if not getattr(manager, "dry_run", False):
        found = _from_live_api(share)
        if found is None and provider == "lfr-tunnel-docker" and root and subdomain:
            container = meta.get("tunnel_container_name") or f"{root.name}-lfr-tunnel"
            prefix = DockerService.get_docker_cmd_prefix(meta.get("target"))
            found = _from_status_json(
                [
                    *prefix,
                    "exec",
                    container,
                    "./lfr-tunnel",
                    "-status-json",
                    "-subdomain",
                    subdomain,
                ],
                "docker-sidecar",
            )
        if found is None and provider in (None, "lfr-tunnel") and subdomain:
            # _get_binary_path only locates a client; it never installs one.
            bin_path = share._get_binary_path()
            if bin_path and os.path.exists(str(bin_path)):
                found = _from_status_json(
                    [str(bin_path), "-status-json", "-subdomain", subdomain],
                    "native-client",
                )

    found = found or {}
    public_urls = found.get("public_urls") or []
    status = found.get("status") or "inactive"
    return {
        "schema_version": SHARE_STATUS_JSON_SCHEMA_VERSION,
        "project": root.name if root else None,
        "provider": provider,
        # A client answered. Its own `status` says whether it is connected.
        "active": bool(found),
        "source": found.get("source"),
        "subdomain": found.get("subdomain") or subdomain,
        "status": status,
        "connection_state": found.get("connection_state"),
        "public_url": public_urls[0] if public_urls else None,
        "public_urls": public_urls,
        "inspector_url": found.get("inspector_url"),
    }


def print_share_status_json(share, project_id=None):
    """Print the object; anything a helper prints goes to stderr instead."""
    with contextlib.redirect_stdout(sys.stderr):
        payload = build_share_status_payload(share, project_id)
    print(json.dumps(payload, indent=2))
