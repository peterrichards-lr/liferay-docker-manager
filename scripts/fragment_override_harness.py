#!/usr/bin/env python3
"""Live end-to-end harness for the fragment-override chain (LDM-#1745).

WHAT IT PROVES
    That a configured override **reaches the rendered page**. That claim had
    never been demonstrated: `verify_e2e_refactor.sh` carries no fragment
    assertion, the unit suite mocks Docker and Liferay, and LDM-#1618
    established only what the code *does*, not what a user sees.

    The harness boots a real Liferay, lets a real Site Initializer build a
    site with the fragment on a page, and then records four observations in
    order:

        1. `fragmententrylink.editablevalues` carries DEFAULT_VALUE
        2. the rendered page carries DEFAULT_VALUE
        3. after LDM applies the override, `editablevalues` carries
           OVERRIDE_VALUE
        4. after a restart, the rendered page carries OVERRIDE_VALUE and no
           longer carries DEFAULT_VALUE

    (4) is the one that matters. (1) and (2) exist so that (3) and (4) cannot
    pass by accident -- a fixture that never had the default value in it would
    make the whole run vacuous.

WHY A SITE INITIALIZER, AND NOT THE HEADLESS API
    The harness used to try to create the page over Headless. It cannot be
    done. Measured on DXP 2026.q1.7-lts (LDM-#1729):

        * POST /o/headless-delivery/v1.0/sites/{id}/site-pages accepts nested
          `pageElements`, returns 2xx, and discards them -- confirmed in the
          database, zero `fragmententrylink` rows for the new plid, with both
          the bare and the collection-qualified fragment key
        * PUT on a delivery site-page is 405
        * the admin-site element PUT targets an element that must already exist

    There is no create-a-page-element endpoint. LDM-#883 records the same wall
    from the other side. A page carrying a fragment has to come from a site
    initializer or the UI -- and a published site-initializer page is
    *precisely* the case LDM's override feature exists for, because that is
    the case where Headless refuses the update (upstream LPD-99955) and LDM
    falls through to its database fallback.

    So the fixture is a site-initializer client extension. Its shape is taken
    from a real, working one -- `ecopulse-site-initializer` in the
    `ldm-cx-samples` workspace -- not invented. Guessing the schema is what
    produced the 400 in LDM-#1729.

IT NEEDS A LICENSED DXP
    An unlicensed portal serves the DXP Activation page in place of the site,
    so observations (2) and (4) cannot be made at all. Pass `--activation-key`
    (or set `LDM_ACTIVATION_KEY`) and the harness drops the XML into the
    project's `deploy/` before the boot. Developer activation keys carry no
    machine binding, which is what makes this runnable on a CI runner.

    **Never commit an activation key.** `.github/workflows/fragment-override.yml`
    reads one from the `LIFERAY_ACTIVATION_KEY_XML` repository secret and
    writes it to a file at run time.

WHY IT IS NOT IN verify_e2e_refactor.sh
    Fragment override is platform-independent -- it is LDM talking to Liferay's
    API and database, and nothing in it varies by host OS. The verification
    scripts answer "does LDM work on this machine", so running this across
    macOS, Windows and Linux would cost three Liferay boots to yield one bit of
    information. It also depends on a Liferay boot and on Site Initializer
    population, which are durations the suite does not own -- the principle
    LDM-#1383 set out, LDM-#1444 applied, and LDM-#1728 was a reminder of.

    It runs in CI instead, on `ubuntu-latest`, from
    `.github/workflows/fragment-override.yml`.

USAGE
    python3 scripts/fragment_override_harness.py --project fragverify \\
        --activation-key /path/to/activation-key.xml

    python3 scripts/fragment_override_harness.py --project fragverify --keep
    python3 scripts/fragment_override_harness.py --project fragverify \\
        --require-module            # additionally assert the module rung

    LDM_BIN=./ldm python3 scripts/fragment_override_harness.py ...
        # run the source tree rather than an installed `ldm`

The fixture builders below are importable and have unit tests
(`ldm_core/tests/test_fragment_override_harness.py`) so their shape can be
checked without Docker.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shutil
import ssl
import subprocess  # nosec B404 - drives the ldm CLI deliberately
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from base64 import b64encode
from pathlib import Path

# The fixture the harness builds. `FRAGMENT_KEY` is what LDM matches against
# the keys in fragment-overrides.json, and `FIELD_NAME`/`DEFAULT_VALUE` are the
# configuration it then overrides -- so the assertion is "the field changed
# from DEFAULT_VALUE to OVERRIDE_VALUE", which cannot pass by accident.
CX_NAME = "ldm-verify-site-initializer"
SITE_ERC = "ldm-verify-site"
SITE_NAME = "LDM Verify"
PAGE_ERC = "ldm-verify-home"
PAGE_FRIENDLY_URL = "/home"

COLLECTION_KEY = "ldm-verify-collection"
COLLECTION_NAME = "LDM Verify Collection"

# LDM-#1729: `fragmentEntryKey` in fragment.json was "ldmVerifyFragment" and
# Liferay ignored it -- what landed in `fragmententry.fragmententrykey` was
# derived from `name`. The site-initializer form keys on
# `externalReferenceCode` instead (verified against ecopulse: `eco-hero`,
# `grid-monitor`). Keeping `name` and `externalReferenceCode` equal, and equal
# to what the page references, removes the discrepancy rather than encoding it.
FRAGMENT_KEY = "ldm-verify-fragment"
FRAGMENT_NAME = FRAGMENT_KEY

# Deliberately distinctive. `docs/how-to/runtime_overrides.md` warns that the
# database fallback's WHERE clause matches on the key name alone, so a generic
# key ("url", "endpoint") is rewritten in every fragment carrying it across the
# whole instance.
FIELD_NAME = "ldmVerifyEndpoint"
DEFAULT_VALUE = "https://default.invalid/original"
OVERRIDE_VALUE = "https://overridden.invalid/patched"

# Rendered into index.html so the HTML assertion has something unambiguous to
# find, rather than matching a bare URL that could appear anywhere on a page.
RENDER_MARKER = "ldm-verify-endpoint"


def build_site_initializer(dest: Path) -> Path:
    """Write the site-initializer *content* tree under `dest`.

        site-initializer/
          site-initializer.json
          fragments/group/<collection>/collection.json
          fragments/group/<collection>/<fragment>/fragment.json
          fragments/group/<collection>/<fragment>/configuration.json
          fragments/group/<collection>/<fragment>/index.html
          fragments/group/<collection>/<fragment>/index.css
          layouts/1_home/page.json
          layouts/1_home/page-definition.json

    The starting point was a real, deployed site initializer --
    `ldm-cx-samples/client-extensions/ecopulse-site-initializer` -- but three
    details of it do not do what they look like they do, and each was measured
    on a live DXP 2026.q3.0 rather than reasoned about (LDM-#1745):

    **The page content lives in `page-definition.json`, not in `page.json`.**
    ecopulse carries an inline `pageDefinition` inside `page.json`, spelled with
    lowercase types and `fragmentCollectionKey`/`fragmentKey`. That key is never
    read: `BundleSiteInitializer._addOrUpdateLayoutContent` opens the sibling
    file `page-definition.json` and hands it to `LayoutsImporterImpl`, which
    deserialises the headless `PageElement` DTO -- so the types are `"Root"` and
    `"Fragment"`, capitalised, and the fragment is addressed as
    `{"fragment": {"key": ...}}`. Observed with the inline form only: the site,
    the collection, the fragment entry and the layout are all created,
    `addOrUpdateLayoutsContent` reports `0 ms`, and `fragmententrylink` has no
    row. Nothing logs a warning.

    **`fragment.json` must name its `configurationPath`.** Without it the
    `fragmententry.configuration` column is empty, so the fragment has no
    configuration fields, so `editablevalues` is written as `{}` -- and LDM's
    database fallback (`WHERE editablevalues LIKE '%"<field>":%'`) then matches
    zero rows. Observed, twice, before the property was added. ecopulse omits
    it too, which is why copying it was not enough.

    **`${configuration.x}` cannot be used bare.** The importer renders the
    fragment HTML once while computing default editable values, and at that
    moment `configuration` is not yet bound, so FreeMarker aborts the whole
    import with `FragmentEntryContentException: FreeMarker syntax is invalid`.
    The `[configuration.x]` form ecopulse uses survives the import -- but it is
    never substituted at render time either; it reaches the browser as the
    literal text `[configuration.ldmVerifyEndpoint]`. The form that does both is
    FreeMarker's default operator, `${(configuration.x)!'unset'}`: it imports
    cleanly and renders the value.
    """
    root = dest / "site-initializer"
    fragment = root / "fragments" / "group" / COLLECTION_KEY / FRAGMENT_NAME
    fragment.mkdir(parents=True, exist_ok=True)
    (root / "layouts" / "1_home").mkdir(parents=True, exist_ok=True)

    _write_json(
        root / "site-initializer.json",
        {
            "description": "Built by fragment_override_harness.py (LDM-#1745).",
            "name": SITE_NAME,
            "siteExternalReferenceCode": SITE_ERC,
            "siteName": SITE_NAME,
        },
    )

    _write_json(
        root / "fragments" / "group" / COLLECTION_KEY / "collection.json",
        {
            "description": "Fragments for the LDM fragment-override verification.",
            "externalReferenceCode": COLLECTION_KEY,
            "name": COLLECTION_NAME,
        },
    )

    _write_json(
        fragment / "fragment.json",
        {
            # Without this the configuration is never loaded and every
            # `editablevalues` is `{}` -- see the docstring above.
            "configurationPath": "configuration.json",
            "cssPath": "index.css",
            "externalReferenceCode": FRAGMENT_KEY,
            "htmlPath": "index.html",
            "name": FRAGMENT_NAME,
            "type": "component",
        },
    )

    # One text field with a known default. The override changes exactly this.
    _write_json(
        fragment / "configuration.json",
        {
            "fieldSets": [
                {
                    "fields": [
                        {
                            "dataType": "string",
                            "defaultValue": DEFAULT_VALUE,
                            "label": "LDM Verify Endpoint",
                            "name": FIELD_NAME,
                            "type": "text",
                        }
                    ]
                }
            ]
        },
    )

    # `!'unset'` is load-bearing, not defensive: a bare
    # `${configuration.<field>}` aborts the Site Initializer import outright.
    # The value is rendered twice, once in an attribute and once as visible
    # text, so a change in how Liferay escapes attributes cannot make the
    # assertion silently unfindable.
    token = f"${{(configuration.{FIELD_NAME})!'unset'}}"
    (fragment / "index.html").write_text(
        f'<div class="ldm-verify-fragment" data-{RENDER_MARKER}="{token}">\n'
        f"\t<p>{RENDER_MARKER}: {token}</p>\n"
        f"</div>\n",
        encoding="utf-8",
    )
    (fragment / "index.css").write_text(
        ".ldm-verify-fragment { display: block; }\n", encoding="utf-8"
    )

    # page.json carries the layout's METADATA only.
    _write_json(
        root / "layouts" / "1_home" / "page.json",
        {
            "externalReferenceCode": PAGE_ERC,
            "friendlyURL": PAGE_FRIENDLY_URL,
            "name": "Home",
            "name_i18n": {"en_US": "Home"},
            "parentLayoutExternalReferenceCode": "",
            "type": "content",
        },
    )

    # page-definition.json carries the CONTENT, in the headless PageElement
    # schema -- capitalised types, and the fragment addressed by key.
    _write_json(
        root / "layouts" / "1_home" / "page-definition.json",
        {
            "pageElement": {
                "pageElements": [
                    {
                        "definition": {"fragment": {"key": FRAGMENT_KEY}},
                        "type": "Fragment",
                    }
                ],
                "type": "Root",
            }
        },
    )

    return root


def package_site_initializer(content_root: Path, target: Path) -> Path:
    """Package the content tree as a deployable client-extension artifact.

    The layout is taken from the built `ecopulse-site-initializer.zip`, whose
    deployed form was read back out of a running bundle's
    `tomcat/temp/clientextension*` directory:

        WEB-INF/liferay-plugin-package.properties
        site-initializer/site-initializer.json
        site-initializer/site-initializer.zip   <- the content, nested

    `Liferay-Client-Extension-Site-Initializer` names the *directory*, and the
    portal extracts `site-initializer.zip` from inside it. The content zip's
    own entries are rooted at `site-initializer/`.
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    inner = target.parent / "site-initializer.zip"
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(content_root.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(content_root.parent)))

    symbolic_name = CX_NAME.replace("-", "")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "WEB-INF/liferay-plugin-package.properties",
            f"Bundle-SymbolicName={symbolic_name}\n"
            "Liferay-Client-Extension-Site-Initializer=site-initializer/\n"
            "module-group-id=liferay\n"
            f"name={CX_NAME}\n",
        )
        archive.writestr(
            "site-initializer/site-initializer.json",
            json.dumps(
                {"externalReferenceCode": SITE_ERC, "name": SITE_NAME}, indent=2
            ),
        )
        archive.write(inner, arcname="site-initializer/site-initializer.zip")

    inner.unlink()
    return target


def build_workspace(scratch: Path, tag: str) -> tuple[Path, Path]:
    """Assemble the fixture. Returns (workspace to import, artifact to deploy).

    **The client extension is deliberately NOT placed in the workspace's
    `client-extensions/` directory**, even though `_sync_client_extensions`
    (`workspace/hydration.py:17`) would happily move it into the project's
    `osgi/client-extensions/` for us. Doing so puts it there *before the first
    boot*, and on a first boot that fails -- measured on DXP 2026.q3.0:

        SiteInitializerClientExtension.addingBundle(...) :
        java.lang.NullPointerException: Cannot invoke
        "com.liferay.portal.kernel.model.Layout.getGroupId()"
        because "layout" is null
            at PortalImpl.getCanonicalURL(PortalImpl.java:1556)
            at ServiceContextFactory._getInstance(...)
            at SiteResourceImpl._addGroup(SiteResourceImpl.java:482)

    The extender's bundle tracker opens during portal startup, before the
    built-in `welcome` and `cms` site initializers have created the Guest
    site's layouts, and `ServiceContextFactory` needs one. The bundle logs
    `STARTED`, no site is created, and the only sign is a stack trace among
    thousands of startup lines. Dropping the same artifact into the running
    portal afterwards initialises the site in ~100 ms.

    So the harness deploys it itself, once the portal is answering. LDM-#1729
    recorded the same shape of fault for a fragment collection deployed during
    import; this is that lesson applied rather than rediscovered.
    """
    source = scratch / "workspace"
    source.mkdir(parents=True, exist_ok=True)

    content = build_site_initializer(scratch / "fixture")
    artifact = package_site_initializer(
        content, scratch / "artifacts" / f"{CX_NAME}.zip"
    )

    # Keep the workspace's product pin and the tag we boot in step: LDM warns
    # (and offers the pinned tag) when they disagree, which would stall an
    # unattended run.
    (source / "gradle.properties").write_text(
        f"liferay.workspace.product=dxp-{tag}\n", encoding="utf-8"
    )
    return source, artifact


def build_overrides(path: Path) -> Path:
    """The fragment-overrides.json LDM reads.

    Schema per `_validate_fragment_overrides`: a top-level object keyed by
    fragment key, each value the configuration payload.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({FRAGMENT_KEY: {FIELD_NAME: OVERRIDE_VALUE}}, indent=2),
        encoding="utf-8",
    )
    return path


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _insecure_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class Headless:
    """The few Headless calls the harness needs, with LDM's own auth convention."""

    def __init__(self, base_url: str, email: str, password: str):
        self.base_url = base_url.rstrip("/")
        token = b64encode(f"{email}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, payload=None):
        req = urllib.request.Request(
            f"{self.base_url}{path}", headers=self.headers, method=method
        )
        if payload is not None:
            req.data = json.dumps(payload).encode("utf-8")
        try:
            with urllib.request.urlopen(  # nosec B310
                req, context=_insecure_context(), timeout=60
            ) as res:
                body = res.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            return {"_error": e.code, "_reason": e.reason, "_body": e.read().decode()}
        except Exception as e:  # The harness reports failures, never raises
            return {"_error": "connection", "_reason": str(e)}


def fetch_page(base_url: str, path: str, email: str, password: str) -> str:
    """Return the rendered HTML of a portal page.

    Anonymous first, because a site initializer's public pages are guest
    viewable and that is the cheapest, most representative read. If the marker
    is absent we log in through `/c/portal/login` with a cookie jar and try
    again -- Basic auth is not accepted on portal pages (it is a Headless
    convention), so an authenticated read has to be a real session.
    """
    url = f"{base_url.rstrip('/')}{path}"

    def _get(opener) -> str:
        req = urllib.request.Request(url, headers={"Accept": "text/html"})
        with opener.open(req, timeout=120) as res:  # nosec B310
            return res.read().decode("utf-8", errors="replace")

    anonymous = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_insecure_context())
    )
    try:
        body = _get(anonymous)
    except Exception as e:
        body = f"<!-- anonymous fetch failed: {e} -->"

    if RENDER_MARKER in body:
        return body

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=_insecure_context()),
    )
    try:
        # Liferay refuses the login unless it can see that cookies work.
        opener.open(f"{base_url.rstrip('/')}/c/portal/login", timeout=60).read()  # nosec B310
        data = urllib.parse.urlencode(
            {"login": email, "password": password, "rememberMe": "false"}
        ).encode()
        opener.open(  # nosec B310
            urllib.request.Request(f"{base_url.rstrip('/')}/c/portal/login", data=data),
            timeout=60,
        ).read()
        return _get(opener)
    except Exception as e:
        return f"{body}\n<!-- authenticated fetch failed: {e} -->"


def docker_prefix(node: str | None) -> list[str]:
    """`docker` plus whatever routes it at an LDM compute node, if any."""
    if not node:
        return ["docker"]
    try:
        from ldm_core.docker_service import DockerService

        return list(DockerService.get_docker_cmd_prefix(node))
    except Exception:
        return ["docker", "--context", node]


def db_query(project: str, sql: str, node: str | None = None) -> str:
    """Run one read-only statement against the project's PostgreSQL container.

    The database is the only place the override's effect is unambiguous:
    `fragmententrylink.editablevalues` is the column LDM's fallback rewrites,
    and it is what the rendered page is built from.
    """
    cmd = [
        *docker_prefix(node),
        "exec",
        f"{project}-db",
        "psql",
        "-U",
        "lportal",
        "-d",
        "lportal",
        "-t",
        "-A",
        "-c",
        sql,
    ]
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        cmd, capture_output=True, text=True, timeout=120, check=False
    )
    if proc.returncode != 0:
        return f"<query failed: {proc.stderr.strip()}>"
    return proc.stdout.strip()


def fragment_entry_links(project: str, node: str | None = None) -> list[str]:
    """Every `editablevalues` document that mentions the fixture's field."""
    raw = db_query(
        project,
        "SELECT fragmententrylinkid || '|' || editablevalues FROM fragmententrylink "
        f"WHERE editablevalues LIKE '%{FIELD_NAME}%';",
        node=node,
    )
    return [line for line in raw.splitlines() if line.strip()]


MODULE_REPO = "peterrichards-lr/liferay-custom-osgi-modules"
MODULE_PREFIX = "com.liferay.custom.fragment.override-"


def resolve_module_jar(tag: str, explicit: Path | None, dest: Path):
    """Put the fragment-override jar where LDM will deploy it (LDM-#1719).

    The module lives in another repository and is published **per DXP line** --
    `com.liferay.custom.fragment.override-3.3.0-dxp-2026.q3.0.jar`. Its
    `bnd.bnd` hardcodes `Import-Package` ranges, so a jar built for one line
    does not resolve on another; deployed onto the wrong tag it fails silently
    as an unresolved bundle in the OSGi log, nowhere near this script.

    Fetched at run time rather than vendored into LDM's own release: vendoring
    would pin a third-party binary to one DXP line inside our artifact, where it
    would go stale independently of us and be wrong for anyone on another line.
    `--module-jar` covers the offline case -- point it at a local copy and
    nothing is downloaded.

    Returns (path, note). A None path means the rung cannot be exercised, and
    the note says why rather than leaving the caller to guess.
    """
    if explicit is not None:
        if not explicit.is_file():
            return None, f"--module-jar {explicit} does not exist"
        target = dest / explicit.name
        shutil.copy2(explicit, target)
        return target, f"using local jar {explicit.name}"

    # The tag names the line the jar must match, e.g. 2026.q3.0 -> dxp-2026.q3.0.
    line = f"dxp-{tag}" if not tag.startswith("dxp-") else tag
    api = f"https://api.github.com/repos/{MODULE_REPO}/releases/latest"
    try:
        with urllib.request.urlopen(api, timeout=30) as res:  # nosec B310
            release = json.loads(res.read().decode())
    except Exception as e:
        return None, f"could not reach {MODULE_REPO}: {e}"

    wanted = [
        a
        for a in release.get("assets", [])
        if a.get("name", "").startswith(MODULE_PREFIX)
        and a.get("name", "").endswith(f"-{line}.jar")
    ]
    if not wanted:
        available = sorted(
            a.get("name", "")
            for a in release.get("assets", [])
            if a.get("name", "").startswith(MODULE_PREFIX)
        )
        return None, (
            f"{release.get('tag_name')} publishes no fragment-override jar for "
            f"{line}. Available: {available or 'none'}. Re-run with --tag set to "
            "a line the module is built for, or pass --module-jar."
        )

    asset = wanted[0]
    target = dest / asset["name"]
    try:
        with urllib.request.urlopen(asset["browser_download_url"], timeout=120) as res:  # nosec B310
            target.write_bytes(res.read())
    except Exception as e:
        return None, f"could not download {asset['name']}: {e}"

    return target, f"fetched {asset['name']} from {release.get('tag_name')}"


def run_ldm(
    args: list[str], cwd: Path, node: str | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    """Invoke the LDM CLI.

    `LDM_BIN` lets a source checkout drive its own wrapper (`./ldm`) rather
    than needing an installed console script -- which is also the reliable
    spelling on a developer machine, where endpoint protection deletes
    `.venv/bin` wrappers by name (see the ldm-developer skill).
    """
    binary = os.environ.get("LDM_BIN", "ldm")
    # `--target` is a GLOBAL flag, so it precedes the subcommand.
    cmd = [binary, *(["--target", node] if node else []), *args]
    print(f"  $ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=5400,
        check=False,
    )
    if check and proc.returncode != 0:
        print(proc.stdout[-8000:])
        print(proc.stderr[-8000:], file=sys.stderr)
        raise SystemExit(f"ldm {' '.join(args)} failed with {proc.returncode}")
    return proc


def find_fragment_element(node, found: list):
    """Collect every page element that carries our fragment key, with its id.

    The shape of `id` here IS the question LDM-#1618 answered -- a UUID, never
    a `fragmentEntryLinkId` -- so the harness records it verbatim rather than
    interpreting it, and the answer stays checkable on every new DXP line.
    """
    if isinstance(node, dict):
        blob = json.dumps(node)
        if FRAGMENT_KEY in blob and "id" in node:
            found.append(
                {
                    "id": node.get("id"),
                    "id_type": type(node.get("id")).__name__,
                    "id_is_numeric": str(node.get("id", "")).isdigit(),
                    "has_fragmentEntryLinkId": "fragmentEntryLinkId" in blob,
                    "keys": sorted(node.keys()),
                }
            )
        for value in node.values():
            find_fragment_element(value, found)
    elif isinstance(node, list):
        for item in node:
            find_fragment_element(item, found)
    return found


def wait_for(description: str, probe, timeout: int, interval: int = 10):
    """Poll `probe` until it returns something truthy, or the budget is gone.

    Returns (value, seconds). A None value means the wait expired -- reported,
    never raised, because a harness that dies mid-run leaves containers behind
    and says less than one that finishes and explains.
    """
    started = time.time()
    deadline = started + timeout
    announced = started
    while time.time() < deadline:
        value = probe()
        if value:
            return value, time.time() - started
        if time.time() - announced >= 60:
            announced = time.time()
            print(
                f"  still waiting for {description} "
                f"({int(deadline - time.time())}s of budget left)",
                flush=True,
            )
        time.sleep(interval)
    print(f"  gave up waiting for {description} after {timeout}s")
    return None, time.time() - started


def resolve_site_page_url(api: Headless) -> str | None:
    """The public URL path of the initializer's page, read from the portal.

    Derived rather than assumed: Liferay builds the site's friendly URL from
    its name, and hardcoding a guess is the kind of thing that fails quietly
    on a DXP line that changes the rule.
    """
    sites = api.request("GET", "/o/headless-admin-site/v1.0/sites")
    if not isinstance(sites, dict):
        return None
    for site in sites.get("items") or []:
        if site.get("externalReferenceCode") != SITE_ERC:
            continue
        friendly = site.get("friendlyUrlPath") or site.get("key")
        if not friendly:
            return None
        return f"/web/{str(friendly).lstrip('/')}{PAGE_FRIENDLY_URL}"
    return None


def headless_answered(api: Headless):
    """The sites listing, or None while the API is not answering yet."""
    res = api.request("GET", "/o/headless-admin-site/v1.0/sites")
    return res if isinstance(res, dict) and "_error" not in res else None


def _report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def main() -> int:  # noqa: C901, PLR0911, PLR0912, PLR0915 - linear by design
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="fragverify")
    parser.add_argument(
        "--tag",
        default="2026.q3.0",
        help=(
            "Liferay tag. Defaults to the line the fragment-override module "
            "publishes for, so --require-module can work without a rebuild."
        ),
    )
    parser.add_argument("--admin-email", default="test@liferay.com")
    parser.add_argument("--admin-password", default="test")  # nosec B107
    parser.add_argument("--port", default="8080")
    parser.add_argument(
        "--activation-key",
        type=Path,
        default=os.environ.get("LDM_ACTIVATION_KEY") or None,
        help=(
            "DXP activation key XML, dropped into the project's deploy/ before "
            "the boot. Without a licence the portal serves the DXP Activation "
            "page instead of the site, so the rendered-page assertions cannot "
            "be made. Also read from LDM_ACTIVATION_KEY."
        ),
    )
    parser.add_argument(
        "--search-mode",
        default="sidecar",
        help=(
            "Elasticsearch topology. Defaults to `sidecar` rather than LDM's "
            "own `shared` default: this is a throwaway verification project, "
            "and a shared Global Search node is infrastructure the harness "
            "should not have to provision -- on a remote target LDM refuses to "
            "provision it for the first time at all."
        ),
    )
    parser.add_argument(
        "--node",
        default=None,
        help=(
            "run against a registered LDM compute node (`ldm target ls`) "
            "instead of local Docker. The Headless base URL follows the node's "
            "host automatically -- pointing it at localhost while the container "
            "runs elsewhere is the obvious way to get this wrong."
        ),
    )
    parser.add_argument(
        "--initializer-timeout",
        type=int,
        default=1200,
        help=(
            "seconds to wait for the Site Initializer to place the fragment. "
            "Bounded deliberately: an unbounded poll is the failure mode "
            "LDM-#1728 was filed for."
        ),
    )
    parser.add_argument(
        "--fragment-patch-timeout",
        type=int,
        default=60,
        help=(
            "seconds LDM may spend waiting for the Headless rungs before it "
            "falls through to the database fallback. Well below LDM's own 300s "
            "default because both Headless rungs are known-dead on a published "
            "site-initializer page, so the full budget is pure wall clock."
        ),
    )
    parser.add_argument("--keep", action="store_true", help="leave the project behind")
    parser.add_argument(
        "--require-module",
        action="store_true",
        help=(
            "additionally assert the fragment-override module rung. The jar is "
            "fetched from the module repository for the DXP line --tag names, "
            "and feature.flag.LPD-99955 is enabled automatically; see LDM-#1618."
        ),
    )
    parser.add_argument(
        "--module-jar",
        type=Path,
        default=None,
        help=(
            "use this local fragment-override jar instead of fetching one. For "
            "an air-gapped run, or to test a build that is not yet released."
        ),
    )
    args = parser.parse_args()

    started = time.time()
    timings: dict[str, float] = {}
    workspace = Path(os.environ.get("LDM_WORKSPACE", Path.cwd())).resolve()
    scratch = workspace / f".{args.project}-fixture"
    if scratch.exists():
        shutil.rmtree(scratch)

    report: dict = {
        "fragment_key": FRAGMENT_KEY,
        "field_name": FIELD_NAME,
        "default_value": DEFAULT_VALUE,
        "override_value": OVERRIDE_VALUE,
        "tag": args.tag,
        "observations": {},
        "timings_seconds": timings,
    }
    # Deliberately outside the project directory: `--keep` is the exception,
    # not the rule, and the cleanup below deletes the project. Evidence that
    # only survives a successful, kept run is not evidence.
    evidence = workspace / f"{args.project}-fragment-override"
    evidence.mkdir(parents=True, exist_ok=True)
    out = evidence / "report.json"

    print("▶ Building the site-initializer fixture...", flush=True)
    source, artifact = build_workspace(scratch, args.tag)
    print(f"  fixture at {source}", flush=True)

    print("▶ Creating the project...", flush=True)
    mark = time.time()
    run_ldm(
        ["-y", "import", str(source), args.project, "--no-run", "--port", args.port],
        cwd=workspace,
        node=args.node,
    )
    timings["import"] = time.time() - mark

    project_root = workspace / args.project
    deploy_dir = project_root / "deploy"
    deploy_dir.mkdir(parents=True, exist_ok=True)

    if args.activation_key:
        key = Path(args.activation_key)
        if not key.is_file():
            print(f"  --activation-key {key} does not exist.")
            return 1
        shutil.copy2(key, deploy_dir / key.name)
        print(f"  staged activation key {key.name} into deploy/", flush=True)
        report["licensed"] = True
    else:
        print(
            "  NO ACTIVATION KEY. An unlicensed portal serves the DXP Activation\n"
            "  page instead of the site, so the rendered-page observations will\n"
            "  not be made. Pass --activation-key to complete the chain.",
            flush=True,
        )
        report["licensed"] = False

    module_note = None
    run_flags = [
        "-y",
        "run",
        args.project,
        "-t",
        args.tag,
        "--search-mode",
        args.search_mode,
    ]
    if args.require_module:
        # Into deploy/ before the boot: OSGi resolves bundles at startup, so a
        # jar dropped afterwards needs a second restart to take effect.
        jar, module_note = resolve_module_jar(args.tag, args.module_jar, deploy_dir)
        print(f"  {module_note}")
        if jar is None:
            print("  The module rung cannot be exercised without it.")
            return 4
        # LDM sets `feature.flag.LPD-99955=true` from this; the module reads the
        # property directly through PropsUtil, so the portal never needs to
        # register it as a known flag.
        run_flags += ["--feature", "LPD-99955"]

    print("▶ Booting (this pulls and starts Liferay; several minutes)...", flush=True)
    mark = time.time()
    run_ldm(run_flags, cwd=workspace, node=args.node)
    timings["boot"] = time.time() - mark

    host = "localhost"
    if args.node:
        # Read the host from LDM's own target registry rather than asking for
        # it twice -- two sources for one fact is how they drift.
        try:
            from ldm_core.config import load_targets

            node = load_targets().get(args.node)
            if node is None:
                print(f"  Unknown node {args.node!r}; see `ldm target ls`.")
                return 2
            host = node.host
        except Exception as e:  # Reported, never raised
            print(f"  Could not resolve node {args.node!r}: {e}")
            return 2
        print(f"▶ Targeting node {args.node} at {host}")

    base_url = f"http://{host}:{args.port}"
    api = Headless(base_url, args.admin_email, args.admin_password)

    print("▶ Waiting for Headless to answer...", flush=True)
    mark = time.time()
    sites, waited = wait_for(
        "the Headless API", lambda: headless_answered(api), timeout=600
    )
    timings["headless_ready"] = waited
    if sites is None:
        print("  Headless never answered.")
        _report(out, report)
        return 3

    # Only now -- see build_workspace() for why not before the boot.
    print("▶ Deploying the site initializer into the running portal...", flush=True)
    cx_dir = project_root / "osgi" / "client-extensions"
    if args.node:
        # The project directory lives on the node, so this copy would write to
        # a path that does not exist here and the run would then fail much
        # later, looking like a Site Initializer problem. Refuse instead.
        print(
            f"  The artifact has to be placed in {cx_dir} ON node "
            f"{args.node!r}, which this script cannot reach as a local path.\n"
            f"  Copy {artifact} there and re-run without --node, or run the "
            "harness on the node itself."
        )
        _report(out, report)
        return 2
    cx_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact, cx_dir / artifact.name)
    print(f"  dropped {artifact.name} into {cx_dir}", flush=True)

    print("▶ Waiting for the Site Initializer to place the fragment...", flush=True)
    rows, waited = wait_for(
        "the fragment on a page",
        lambda: fragment_entry_links(args.project, args.node) or None,
        timeout=args.initializer_timeout,
    )
    timings["site_initializer"] = waited
    report["observations"]["fragmententrylink_before"] = rows or []

    if not rows:
        print()
        print("  The Site Initializer never placed the fragment.")
        print("  `fragmententrylink` has no row mentioning the field, so there is")
        print("  nothing for the override chain to patch. Check the portal log for")
        print("  a client-extension deployment failure before reading anything")
        print("  else into this run.")
        _report(out, report)
        return 3

    print(f"  {len(rows)} fragmententrylink row(s) carry {FIELD_NAME}", flush=True)
    for row in rows:
        print(f"    {row}", flush=True)

    before_has_default = any(DEFAULT_VALUE in row for row in rows)
    report["observations"]["default_in_db_before"] = before_has_default

    page_path = resolve_site_page_url(api)
    report["page_path"] = page_path
    print(f"▶ The initializer's page is at {page_path}", flush=True)

    rendered_before = ""
    if page_path:
        rendered_before = fetch_page(
            base_url, page_path, args.admin_email, args.admin_password
        )
    report["observations"]["default_in_html_before"] = DEFAULT_VALUE in rendered_before
    report["observations"]["marker_in_html_before"] = RENDER_MARKER in rendered_before

    print(
        "▶ Writing fragment-overrides.json and re-running LDM's patcher...", flush=True
    )
    build_overrides(project_root / ".ldm" / "fragment-overrides.json")
    mark = time.time()
    # `ldm wait` runs the same readiness path `ldm run` does, and that path is
    # what calls _patch_fragment_overrides (runtime/readiness.py:684). Doing it
    # as a second step rather than on the initial boot is what makes the
    # before/after pair observable at all.
    #
    # --fragment-patch-timeout is cut from its 300s default on purpose. Both
    # Headless rungs are known-dead on a published site-initializer page
    # (LDM-#883/LPD-99955 for the specification PUT; LDM-#1618 for the module),
    # so the full budget is spent waiting for an answer that will not come --
    # measured at 300s of pure wall clock before the database fallback ran.
    # Shortening it does not weaken the test: the fallback is the rung being
    # verified, and the run still fails if it patches nothing.
    patch_proc = run_ldm(
        [
            "-y",
            "wait",
            args.project,
            "--fragment-patch-timeout",
            str(args.fragment_patch_timeout),
        ],
        cwd=workspace,
        node=args.node,
        check=False,
    )
    timings["patch"] = time.time() - mark
    report["patch_exit_code"] = patch_proc.returncode
    tail = (patch_proc.stdout or "")[-4000:]
    report["patch_output_tail"] = tail
    print(tail, flush=True)

    after_rows = fragment_entry_links(args.project, args.node)
    report["observations"]["fragmententrylink_after"] = after_rows
    after_has_override = any(OVERRIDE_VALUE in row for row in after_rows)
    report["observations"]["override_in_db_after"] = after_has_override

    print(
        "▶ Restarting -- the database fallback is invisible until the portal reloads",
        flush=True,
    )
    mark = time.time()
    run_ldm(["-y", "restart", args.project], cwd=workspace, node=args.node, check=False)
    timings["restart"] = time.time() - mark

    wait_for(
        "the portal after the restart", lambda: headless_answered(api), timeout=900
    )

    def overridden_html():
        html = fetch_page(base_url, page_path, args.admin_email, args.admin_password)
        return html if OVERRIDE_VALUE in html else None

    rendered_after = ""
    if page_path:
        rendered_after, waited = wait_for(
            "the overridden value on the rendered page",
            overridden_html,
            timeout=300,
            interval=15,
        )
        timings["render_after"] = waited
        if rendered_after is None:
            rendered_after = fetch_page(
                base_url, page_path, args.admin_email, args.admin_password
            )

    report["observations"]["override_in_html_after"] = OVERRIDE_VALUE in rendered_after
    report["observations"]["default_in_html_after"] = DEFAULT_VALUE in rendered_after
    report["observations"]["marker_in_html_after"] = RENDER_MARKER in rendered_after

    # The raw HTML is evidence, not output -- kept beside the report rather
    # than printed, because a portal page is tens of thousands of characters.
    (evidence / "rendered-before.html").write_text(rendered_before, encoding="utf-8")
    (evidence / "rendered-after.html").write_text(rendered_after, encoding="utf-8")

    print("▶ Recording what `element_id` actually is (LDM-#1618)...", flush=True)
    elements = find_fragment_element(sites, [])
    for site in sites.get("items", []) or []:
        erc = site.get("externalReferenceCode") or site.get("id")
        pages = api.request(
            "GET", f"/o/headless-admin-site/v1.0/sites/{erc}/site-pages"
        )
        elements.extend(find_fragment_element(pages, []))
    report["elements"] = elements

    timings["total"] = time.time() - started
    _report(out, report)

    obs = report["observations"]
    verified = bool(
        obs.get("default_in_db_before")
        and obs.get("override_in_db_after")
        and obs.get("override_in_html_after")
        and not obs.get("default_in_html_after")
    )
    report["verified_end_to_end"] = verified
    _report(out, report)

    print()
    print("=" * 70)
    print("  LDM-#1745: does a configured override reach the rendered page?")
    print("=" * 70)
    for label, key in (
        ("default value in editablevalues, before ", "default_in_db_before"),
        ("default value in rendered HTML, before  ", "default_in_html_before"),
        ("override value in editablevalues, after ", "override_in_db_after"),
        ("override value in rendered HTML, after  ", "override_in_html_after"),
        ("default value STILL in HTML, after      ", "default_in_html_after"),
    ):
        print(f"  {label} : {obs.get(key)}")
    print()
    for element in elements:
        print(
            f"  id={element['id']!r}  type={element['id_type']}  "
            f"numeric={element['id_is_numeric']}  "
            f"fragmentEntryLinkId present={element['has_fragmentEntryLinkId']}"
        )
    print()
    print(f"  VERIFIED END TO END: {verified}")
    print(f"  Report: {out}")
    for name, seconds in timings.items():
        print(f"  {name:<18} {seconds:7.1f}s")
    print("=" * 70)

    if args.require_module:
        status = api.request("GET", "/o/fragment-override/status")
        print(f"▶ fragment-override module status: {status}")
        if status.get("_error"):
            print(f"  The module did not answer ({module_note}).")
            print("  Either the bundle failed to resolve -- check the OSGi log for")
            print("  an Import-Package mismatch, which means the jar was built for")
            print("  a different DXP line -- or the feature flag did not take.")
            return 4
        if not status.get("enabled"):
            print("  The module is deployed but feature.flag.LPD-99955 is off.")
            return 4

    if not args.keep:
        print("▶ Cleaning up...")
        run_ldm(
            ["-y", "rm", args.project, "--delete"],
            cwd=workspace,
            node=args.node,
            check=False,
        )
        shutil.rmtree(scratch, ignore_errors=True)

    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
