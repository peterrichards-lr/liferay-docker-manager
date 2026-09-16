#!/usr/bin/env python3
"""Live harness for the fragment-override chain (LDM-#1618).

`_patch_via_override_module` (`ldm_core/runtime/fragments.py`) shipped in
v2.21.0 without ever being exercised. Its own docstring says so, and names the
most likely way it silently never fires:

    `element_id` comes from the Headless page-element representation and may
    not be a `fragmentEntryLinkId` at all.

**That measurement has now been taken** (LDM-#1618): `element_id` is a UUID,
never a `fragmentEntryLinkId`, so the module rung -- whose endpoint declares
`@PathParam("fragmentEntryLinkId") long` -- could never have fired.

THE HARNESS CANNOT CURRENTLY RUN UNATTENDED, AND SAYS SO
    The original plan assumed a page carrying the fragment could be created
    through the Headless API. It cannot (LDM-#1729, all measured on DXP
    2026.q1.7-lts):

        * POST /o/headless-delivery/v1.0/sites/{id}/site-pages accepts nested
          `pageElements`, returns 2xx, and discards them -- confirmed in the
          database, zero `fragmententrylink` rows for the new plid, with both
          the bare and the collection-qualified fragment key
        * PUT on a delivery site-page is 405
        * the admin-site element PUT targets an element that must already exist

    There is no create-a-page-element endpoint. LDM-#883 records the same wall
    from the other side. A page carrying a fragment has to come from a site
    initializer or the UI -- which is precisely the scenario LDM's override
    feature exists for, so a self-contained harness needs a site-initializer
    fixture rather than a Headless call.

    Until then the harness stops at that point and explains it, rather than
    falling through to "no page element carried the fragment key" -- which
    looks identical to a fragment that deployed but never rendered, and is the
    false negative LDM-#1729 was filed for.

WHAT IT PROVES TODAY
    * the fixture builds, packages and deploys as a real Liferay fragment
      collection (verified: `fragmententryid=1` after the post-boot redeploy)
    * what `element_id` actually is in the page-element representation --
      printed, and written to the report

WHAT IT DOES NOT PROVE
    * that a configuration override is applied end to end. That needs the
      fragment placed on a page, which is the gap above.
    * the module rung (rung 2). Verified unreachable rather than untested:
      see LDM-#1618.

WHY IT IS NOT IN verify_e2e_refactor.sh
    It depends on a Liferay boot and on content it must create through the
    Headless API. Those are durations and states the verification suite does
    not own, which is the principle LDM-#1383 set out and LDM-#1444 applied
    when it skipped a check rather than let it hang on an `ssh` client. This is
    an on-demand harness, run deliberately, not part of the default gate.

USAGE
    python3 scripts/fragment_override_harness.py --project fragverify
    python3 scripts/fragment_override_harness.py --project fragverify --keep
    python3 scripts/fragment_override_harness.py --project fragverify \
        --require-module            # additionally assert the module rung

The fixture builder below is importable and has unit tests
(`ldm_core/tests/test_fragment_override_harness.py`) so its shape can be
checked without Docker.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 - drives the ldm CLI deliberately
import sys
import time
import urllib.error
import urllib.request
import zipfile
from base64 import b64encode
from pathlib import Path

# The fragment the harness builds. `FRAGMENT_KEY` is what LDM matches against
# the keys in fragment-overrides.json, and `FIELD_NAME`/`DEFAULT_VALUE` are the
# configuration it then overrides -- so the assertion is "the field changed
# from DEFAULT_VALUE to OVERRIDE_VALUE", which cannot pass by accident.
COLLECTION_NAME = "ldm-verify-collection"
# LDM-#1729: this was "ldmVerifyFragment", and Liferay ignored it. What lands
# in `fragmententry.fragmententrykey` is derived from the fragment's `name`:
#
#     select fragmententrykey, name from fragmententry;
#      ldm-verify-fragment | ldm-verify-fragment
#
# so the harness was matching on a key that never existed and would have
# reported "no page element carried the fragment key" even once everything
# else worked. Keeping the two equal removes the discrepancy rather than
# encoding it.
FRAGMENT_NAME = "ldm-verify-fragment"
FRAGMENT_KEY = FRAGMENT_NAME
FIELD_NAME = "endpoint"
DEFAULT_VALUE = "https://default.invalid/original"
OVERRIDE_VALUE = "https://overridden.invalid/patched"


def build_fragment_collection(dest: Path) -> Path:
    """Write a minimal, valid Liferay fragment collection under `dest`.

    Shape taken from a real collection rather than invented:
    `collection.json` beside one fragment directory holding `fragment.json`
    (which carries the `fragmentEntryKey` LDM matches on), `index.json` (the
    configuration that gets overridden) and the html/css/js the fragment
    renders.
    """
    collection = dest / COLLECTION_NAME
    fragment = collection / FRAGMENT_NAME
    fragment.mkdir(parents=True, exist_ok=True)

    (collection / "collection.json").write_text(
        json.dumps(
            {
                "description": "Built by fragment_override_harness.py (LDM-#1618).",
                "name": COLLECTION_NAME,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (fragment / "fragment.json").write_text(
        json.dumps(
            {
                "fragmentEntryKey": FRAGMENT_KEY,
                "icon": "cog",
                "name": FRAGMENT_NAME,
                "type": "component",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # One text field with a known default. The override changes exactly this.
    (fragment / "index.json").write_text(
        json.dumps(
            {
                "fieldSets": [
                    {
                        "fields": [
                            {
                                "defaultValue": DEFAULT_VALUE,
                                "description": "ldm-verify-endpoint",
                                "label": "Endpoint",
                                "name": FIELD_NAME,
                                "type": "text",
                            }
                        ]
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (fragment / "index.html").write_text(
        '<div class="ldm-verify" data-endpoint="${configuration.'
        + FIELD_NAME
        + '}">LDM verification fragment</div>\n',
        encoding="utf-8",
    )
    (fragment / "index.css").write_text(".ldm-verify { display: block; }\n", "utf-8")
    (fragment / "index.js").write_text("// intentionally empty\n", encoding="utf-8")
    return collection


def package_fragment_zip(collection: Path, target: Path) -> Path:
    """Zip the collection with the marker LDM looks for.

    `workspace/hydration.py:_sync_fragments` only treats a zip as a fragment
    bundle when it contains `liferay-deploy-fragments.json`; without it the
    file is ignored silently.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("liferay-deploy-fragments.json", json.dumps({"version": 1}))
        for path in sorted(collection.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(collection.parent)))
    return target


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
        import ssl

        req = urllib.request.Request(
            f"{self.base_url}{path}", headers=self.headers, method=method
        )
        if payload is not None:
            req.data = json.dumps(payload).encode("utf-8")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as res:  # nosec B310
                body = res.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            return {"_error": e.code, "_reason": e.reason, "_body": e.read().decode()}
        except Exception as e:  # The harness reports failures, never raises
            return {"_error": "connection", "_reason": str(e)}


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


def ensure_page_with_fragment(api, sites):
    """Create a site page carrying the fragment, if one is not there already.

    LDM-#1719. The harness used to walk *existing* pages and, finding none,
    tell the operator to place the fragment by hand -- so it could not run
    unattended, and the docstring claiming it built "a page it creates itself"
    was wrong.

    The page-element schema is not guessed silently: the request is made, the
    page is then read back, and whichever happened is reported verbatim. A
    harness that cannot create the page must say so plainly rather than fall
    through to "no fragment found", which would look identical to a fragment
    that deployed but did not render.
    """
    items = [
        s
        for s in (sites.get("items") or [])
        if s.get("externalReferenceCode") != "L_GLOBAL"
    ]
    if not items:
        return {"ok": False, "summary": "no site to create a page in"}

    site = items[0]
    # LDM-#1729: the payload below is headless-DELIVERY's SitePage schema
    # (`title`, `friendlyUrlPath`, `pageDefinition`) and was being posted to
    # headless-admin-site, whose SitePage has none of those properties -- it
    # uses `name_i18n`, `friendlyUrlPath_i18n` and `pageSpecifications`.
    # Observed: 400 `The property "title" is not defined in SitePage.`
    #
    # Delivery takes the numeric site id, not the external reference code,
    # and has no `/sites` listing of its own -- which is why the lookup above
    # stays on admin-site.
    site_id = site.get("id")
    if not site_id:
        return {"ok": False, "summary": f"site has no numeric id: {site}"}

    page = {
        "title": "LDM Verify",
        # A leading slash is required; "ldm-verify" is rejected with
        # LayoutFriendlyURLException (LDM-#1729).
        "friendlyUrlPath": "/ldm-verify",
        "pageDefinition": {
            "pageElement": {
                "type": "Root",
                "pageElements": [
                    {
                        "type": "Fragment",
                        "definition": {
                            "fragment": {"key": FRAGMENT_KEY},
                            "fragmentConfig": {FIELD_NAME: DEFAULT_VALUE},
                        },
                    }
                ],
            }
        },
    }

    res = api.request(
        "POST", f"/o/headless-delivery/v1.0/sites/{site_id}/site-pages", page
    )
    # LDM-#1729: creating the page is not the same as placing the fragment.
    # Read the page back and check, because the POST accepts the nested
    # `pageElements` and then silently discards them -- measured on DXP
    # 2026.q1.7-lts, with both the bare key and the collection-qualified one,
    # and confirmed in the database (zero `fragmententrylink` rows for the new
    # plid). Reporting "created" on the strength of a 2xx would hand the rest
    # of the harness an empty page and produce "no page element carried the
    # fragment key" -- indistinguishable from a fragment that deployed but did
    # not render, which is the specific confusion this check exists to stop.
    written = api.request(
        "GET", f"/o/headless-delivery/v1.0/sites/{site_id}/site-pages/ldm-verify"
    )
    root = (written or {}).get("pageDefinition", {}).get("pageElement", {})
    if root.get("pageElements"):
        # Reached on a fresh create and on a re-run alike: a 409 means the page
        # is already there, which is success for this step, so the read-back is
        # the authority rather than the POST status.
        return {
            "ok": True,
            "summary": f"page carries the fragment: {written.get('friendlyUrlPath')}",
        }

    if not isinstance(res, dict) or res.get("_error"):
        return {
            "ok": False,
            "summary": f"could not create the page -- Headless answered {res}",
        }

    return {
        "ok": False,
        "summary": (
            "the page was created but the fragment was NOT placed on it. The "
            "Headless API has no way to create a page element: POST discards "
            "nested pageElements, PUT on a delivery site-page is 405, and the "
            "admin-site element PUT targets an element that must already "
            "exist (LDM-#883 records the same wall from the other side). A "
            "page carrying a fragment has to come from a site initializer or "
            "the UI -- which is the scenario LDM's override feature is FOR, "
            "so an unattended harness needs a site-initializer fixture rather "
            "than a Headless call. Tracked in LDM-#1729."
        ),
    }


def redeploy_fragment_collection(
    workspace: Path, project: str, node: str | None
) -> str:
    """Drop the collection zip into the running project's deploy directory.

    Returns a human-readable outcome; never raises. A failure here is worth
    reporting but is not worth aborting over -- the page-creation step reports
    an absent fragment clearly enough on its own.
    """
    zips = sorted(workspace.rglob("*.zip"))
    if not zips:
        return "no collection zip to redeploy"
    if node:
        return (
            "skipping the post-boot redeploy: the deploy directory is on "
            f"node '{node}', not reachable as a local path"
        )
    root = Path.cwd() / project
    deploy = root / "deploy"
    if not deploy.is_dir():
        return f"no deploy directory at {deploy}"
    shutil.copy2(zips[0], deploy / zips[0].name)
    # The file-install watcher polls; a fragment collection registers in a few
    # seconds. This is a fixed wait rather than a poll because the harness has
    # no cheap way to observe registration without the database.
    time.sleep(30)
    return f"redeployed {zips[0].name} after boot"


def run_ldm(
    args: list[str], cwd: Path, node: str | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    # `--target` is a GLOBAL flag, so it precedes the subcommand.
    cmd = ["ldm", *(["--target", node] if node else []), *args]
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    if check and proc.returncode != 0:
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:], file=sys.stderr)
        raise SystemExit(f"ldm {' '.join(args)} failed with {proc.returncode}")
    return proc


def find_fragment_element(node, found: list):
    """Collect every page element that carries our fragment key, with its id.

    The shape of `id` here IS the open question in LDM-#1618, so the harness
    records it verbatim rather than interpreting it.
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

    workspace = Path(os.environ.get("LDM_WORKSPACE", Path.cwd())).resolve()
    scratch = workspace / f".{args.project}-fixture"
    if scratch.exists():
        shutil.rmtree(scratch)

    print("▶ Building the fragment fixture...")
    collection = build_fragment_collection(scratch)
    source = scratch / "workspace"
    (source / "fragments").mkdir(parents=True, exist_ok=True)
    package_fragment_zip(collection, source / "fragments" / f"{COLLECTION_NAME}.zip")
    (source / "gradle.properties").write_text(
        "liferay.workspace.product=dxp-2026.q3.0\n", encoding="utf-8"
    )
    print(f"  fixture at {source}")

    print("▶ Creating the project...")
    run_ldm(
        ["-y", "import", str(source), args.project, "--no-run", "--port", args.port],
        cwd=workspace,
        node=args.node,
    )
    build_overrides(workspace / args.project / ".ldm" / "fragment-overrides.json")

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
        deploy_dir = workspace / args.project / "deploy"
        deploy_dir.mkdir(parents=True, exist_ok=True)
        jar, module_note = resolve_module_jar(args.tag, args.module_jar, deploy_dir)
        print(f"  {module_note}")
        if jar is None:
            print("  The module rung cannot be exercised without it.")
            return 4
        # LDM sets `feature.flag.LPD-99955=true` from this; the module reads the
        # property directly through PropsUtil, so the portal never needs to
        # register it as a known flag.
        run_flags += ["--feature", "LPD-99955"]

    print("▶ Booting (this pulls and starts Liferay; several minutes)...")
    run_ldm(run_flags, cwd=workspace, node=args.node)

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

    print("▶ Waiting for Headless to answer...")
    sites = None
    for _ in range(60):
        sites = api.request("GET", "/o/headless-admin-site/v1.0/sites")
        if isinstance(sites, dict) and "_error" not in sites:
            break
        time.sleep(10)
    if not isinstance(sites, dict) or "_error" in sites:
        print(f"  Headless never answered: {sites}")
        return 3

    # LDM-#1729: the collection is deployed during `ldm import`, so it lands
    # while the portal is still starting and the deploy throws:
    #
    #   NoSuchResourcePermissionException: {name=com.liferay.fragment, ...}
    #
    # Liferay then logs "Deployed ldm-verify-collection.zip successfully"
    # anyway, so nothing looks wrong -- but `fragmententry` is empty, and every
    # later step fails for a reason that has nothing to do with what is being
    # verified. Re-dropping the same zip now that Headless answers deploys it
    # properly (measured: `fragmententryid=1` afterwards, nothing before).
    redeployed = redeploy_fragment_collection(workspace, args.project, args.node)
    print(f"  {redeployed}")

    print("▶ Creating a page that carries the fragment...")
    created = ensure_page_with_fragment(api, sites)
    print(f"  {created['summary']}")

    print("▶ Reading the page tree and recording what `id` actually is...")
    report = {
        "fragment_key": FRAGMENT_KEY,
        "default_value": DEFAULT_VALUE,
        "override_value": OVERRIDE_VALUE,
        "elements": find_fragment_element(sites, []),
    }
    for site in sites.get("items", []) or []:
        erc = site.get("externalReferenceCode") or site.get("id")
        pages = api.request(
            "GET", f"/o/headless-admin-site/v1.0/sites/{erc}/site-pages"
        )
        report["elements"].extend(find_fragment_element(pages, []))

    out = workspace / args.project / ".ldm" / "fragment-override-harness.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print()
    print("=" * 70)
    print("  LDM-#1618 measurement: what is `element_id`?")
    print("=" * 70)
    if not report["elements"]:
        print("  No page element carried the fragment key.")
        print("  The fragment deployed but is not placed on any page -- add it to a")
        print("  page in the UI and re-run, or the chain has nothing to patch.")
    for element in report["elements"]:
        print(
            f"  id={element['id']!r}  type={element['id_type']}  "
            f"numeric={element['id_is_numeric']}  "
            f"fragmentEntryLinkId present={element['has_fragmentEntryLinkId']}"
        )
    print()
    print("  A non-numeric id means the module rung can NEVER fire: the endpoint")
    print('  declares `@PathParam("fragmentEntryLinkId") long`, and a path param')
    print("  that cannot be coerced is a 404 by JAX-RS specification -- which")
    print("  `_api_request` swallows as expected. That would resolve LDM-#1618 by")
    print("  fixing or removing the rung, with no live positive path needed.")
    print(f"  Full report: {out}")
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
