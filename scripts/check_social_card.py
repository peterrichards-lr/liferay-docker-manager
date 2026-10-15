#!/usr/bin/env python3
"""Assert the built site's social card and favicon point at files that exist.

LDM-#2215: `og:image` is an ABSOLUTE url assembled in `overrides/main.html`
from `site_url` + `extra.social_image`. Nothing resolves it at build time --
it is read by a scraper, days later, when someone pastes a link into Slack.
So a typo in `social_image`, a missing trailing slash on `site_url`, or the
image simply not being committed all produce the same thing: a **green build**
and a blank card. The favicon fails the same way, silently, in a browser tab.

The check is deliberately narrow. It does not fetch anything and it does not
judge the image: it asserts that each referenced path resolves to a file
inside the site that was just built, which is the part that breaks.

Usage:
    check_social_card.py <site_dir> [--site-url URL]

Exit codes:
    0  every referenced asset exists in the built site
    1  at least one does not -- the message names it
    2  bad usage
    3  nothing to evaluate (no HTML found); the CALLER must treat this as a
       FAILURE, never a pass -- the same contract as check_site_verification.py.
"""

import re
import sys
from pathlib import Path

# The tags whose href/content must resolve to a real file in the built site.
_REFS = (
    ("og:image", re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"')),
    ("twitter:image", re.compile(r'<meta\s+name="twitter:image"\s+content="([^"]+)"')),
    (
        "svg favicon",
        re.compile(r'<link\s+rel="icon"\s+type="image/svg\+xml"\s+href="([^"]+)"'),
    ),
    (
        "apple-touch-icon",
        re.compile(r'<link\s+rel="apple-touch-icon"\s+href="([^"]+)"'),
    ),
)


def _resolve(ref: str, page: Path, site_dir: Path, site_url: str | None) -> Path | None:
    """Map a reference as written in the HTML back to a path in the site."""
    if ref.startswith(("http://", "https://")):
        if not site_url or not ref.startswith(site_url):
            # An absolute url pointing somewhere else entirely is out of scope:
            # this check owns the built site, not the whole internet.
            return None
        return site_dir / ref[len(site_url) :].lstrip("/")

    if ref.startswith("/"):
        # Root-relative. Material writes these on 404.html deliberately: that
        # page is served for a request at ANY depth, so a page-relative href
        # would resolve differently every time it was shown. The leading
        # segment is the site's base path -- `/liferay-docker-manager/` here --
        # which is part of the published URL, not of the build directory.
        base = ""
        if site_url:
            base = "/" + site_url.split("//", 1)[-1].split("/", 1)[-1]
        if base and base != "/" and ref.startswith(base):
            ref = ref[len(base) :]
        return site_dir / ref.lstrip("/")

    # Relative references are resolved against the PAGE, exactly as a browser
    # would -- which is the whole reason `url` filters exist in the templates.
    return (page.parent / ref).resolve()


def judge(site_dir: Path, site_url: str | None):
    html_files = sorted(site_dir.rglob("*.html"))
    if not html_files:
        return 3, [f"ERROR: no .html under {site_dir} -- the site was not built."]

    missing: list[str] = []
    seen: set[tuple[str, str]] = set()
    for page in html_files:
        text = page.read_text(encoding="utf-8", errors="replace")
        for label, pattern in _REFS:
            for ref in pattern.findall(text):
                target = _resolve(ref, page, site_dir, site_url)
                if target is None or target.is_file():
                    continue
                key = (label, ref)
                if key in seen:
                    continue
                seen.add(key)
                rel = page.relative_to(site_dir)
                missing.append(f"  {label}: {ref}  (referenced by {rel})")

    if missing:
        return 1, [
            f"ERROR: {len(missing)} referenced asset(s) are not in the built site.",
            "A scraper or a browser resolves these AFTER the build, so this is",
            "the only place it can be caught:",
            *missing,
        ]
    return 0, [
        f"OK: social card and icon references all resolve ({len(html_files)} pages)."
    ]


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    site_url = None
    for a in argv[1:]:
        if a.startswith("--site-url="):
            site_url = a.split("=", 1)[1]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    code, lines = judge(Path(args[0]), site_url)
    print("\n".join(lines), file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
