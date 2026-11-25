"""Refuse to share an instance that still accepts the default admin login (LDM-#2526).

`ldm share` puts a local instance on a public URL. A seeded instance signs in
with LDM's well-known default administrator (``DEFAULT_ADMIN_EMAIL`` /
``DEFAULT_ADMIN_PASSWORD`` in ``ldm_core/constants.py``) until someone changes
it, and anyone who has the URL knows those credentials too.

So before a tunnel starts, LDM asks the instance whether the default pair still
signs in. If it does, sharing needs an explicit yes: ``--allow-default-credentials``,
or an interactive confirmation. Non-interactively it refuses.

The probe is injectable (``probe=``) so tests never reach a portal.
"""

import base64
import json
import ssl
import urllib.error
import urllib.request

from ldm_core.ui import UI

# Any authenticated Headless call would do; this one answers with the account
# the credentials resolved to, so a guest response cannot be mistaken for a
# successful sign-in.
PROBE_PATH = "/o/headless-admin-user/v1.0/my-user-account"

ALLOW_FLAG = "--allow-default-credentials"

# The compose service `composer._build_lfr_tunnel_service` emits for the
# `lfr-tunnel-docker` provider. It is started only by `ShareService.cmd_start`
# (`compose up -d lfr-tunnel`), after the check below.
SIDECAR_SERVICE = "lfr-tunnel"


def services_holding_back_sidecar(compose_file):
    """The services a bring-up should name so the share sidecar is left out.

    A bare `compose up`/`create`/`start` starts every service in the file,
    including the tunnel sidecar -- which would put the instance on its public
    URL before `assert_default_credentials_acceptable` has run. Naming every
    other service starts exactly what the bare form would, minus the sidecar;
    `ShareService.cmd_start` brings it up once the check has passed.

    Returns ``[]`` when the file defines no sidecar, meaning "no change: start
    everything", so a project that is not shared through Docker is unaffected.
    """
    from pathlib import Path

    from ldm_core.utils import yaml_to_dict

    try:
        text = Path(compose_file).read_text(errors="replace")
    except (OSError, TypeError):
        return []
    services = (yaml_to_dict(text) or {}).get("services") or {}
    if not isinstance(services, dict) or SIDECAR_SERVICE not in services:
        return []
    return [name for name in services if name != SIDECAR_SERVICE]


def default_credentials_work(base_url, email, password, timeout=5.0):
    """True if ``email``/``password`` sign in at ``base_url``; False if refused.

    None when it cannot be told -- unreachable, timed out, or an answer that is
    neither a sign-in nor a refusal. Callers must not read None as "safe".
    """
    token = base64.b64encode(f"{email}:{password}".encode()).decode()
    req = urllib.request.Request(
        base_url.rstrip("/") + PROBE_PATH,
        headers={
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "User-Agent": "LDM-ShareGuard/1.0",
        },
    )
    # The local instance commonly uses an mkcert or self-signed certificate;
    # what is being tested is the credential, not the certificate.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310
            if resp.status != 200:
                return None
            body = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as e:
        return False if e.code in (401, 403) else None
    except Exception:
        return None
    account = str(body.get("emailAddress") or "").strip().lower()
    return account == email.strip().lower() if account else None


def _probe_url(manager, project_meta):
    from ldm_core.handlers.composer import resolve_access_url

    host_name = project_meta.get("host_name", "localhost")
    ssl_active = manager.composer._is_ssl_active(host_name, project_meta)
    return resolve_access_url(host_name, project_meta, ssl_active)


def assert_default_credentials_acceptable(manager, project_meta, probe=None):
    """Stop a share of an instance whose default admin login still works.

    Reads ``--allow-default-credentials`` from the parsed arguments, so the
    check covers every way a share starts (`share start`, `run --share`,
    `start --share`, the post-boot auto-share) without each caller passing it.
    """
    from ldm_core.constants import DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD

    if getattr(manager, "dry_run", False):
        return
    args = getattr(manager, "args", None)
    allowed = getattr(args, "allow_default_credentials", False) is True

    try:
        base_url = _probe_url(manager, project_meta)
    except Exception:
        base_url = None
    if not base_url:
        UI.warning(
            "Could not work out this project's URL, so LDM cannot check whether "
            f"the default admin login ({DEFAULT_ADMIN_EMAIL}) still works."
        )
        return

    works = (probe or default_credentials_work)(
        base_url, DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_PASSWORD
    )
    if works is None:
        UI.warning(
            f"Could not check whether the default admin login ({DEFAULT_ADMIN_EMAIL}) "
            f"still works at {base_url}. Change it before sharing if it does."
        )
        return
    if works is False:
        return

    warning = (
        f"This instance still accepts the default admin login "
        f"({DEFAULT_ADMIN_EMAIL} with the default password). Anyone with the "
        "public URL can sign in as an administrator."
    )
    if allowed:
        UI.warning(f"{warning} Sharing anyway: {ALLOW_FLAG} was passed.")
        return
    UI.warning(warning)
    if getattr(manager, "non_interactive", False):
        UI.die(
            "Refusing to share an instance that still accepts the default admin login.",
            tip=(
                "Change the administrator's password in the portal, or pass "
                f"{ALLOW_FLAG} to share it anyway."
            ),
        )
    if not UI.confirm("Share it anyway?", "N"):
        UI.die(
            "Not shared. Change the administrator's password, then share again.",
            tip=f"Or pass {ALLOW_FLAG} to skip this question.",
        )
