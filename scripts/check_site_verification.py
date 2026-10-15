#!/usr/bin/env python3
"""Assert the built site carries its search-console verification tag.

LDM-#2210: the tag is injected from `overrides/main.html`, guarded by
`if config.extra.google_site_verification`. Remove that key -- or mistype it,
or let a Material upgrade change the block this overrides -- and the build
stays **green** while every page quietly loses the tag. Google then
unverifies the property, and nothing says so until someone opens Search
Console weeks later.

So the claim worth checking is not "the key is in mkdocs.yml" but "the tag is
in the HTML that shipped". This reads the built site.

Usage:
    check_site_verification.py <site_dir> [--expect-token TOKEN]

Exit codes:
    0  every built page carries the tag
    1  at least one page does not -- the message names how many and lists some
    2  bad usage
    3  nothing to evaluate (no HTML found); the CALLER must treat this as a
       FAILURE, never a pass -- an empty site directory means the build did
       not happen, which is not the same as a site that verified.
"""

import sys
from pathlib import Path

TAG = 'name="google-site-verification"'


def pages(site_dir: Path):
    return sorted(p for p in site_dir.rglob("*.html"))


def judge(site_dir: Path, expect_token: str | None):
    html_files = pages(site_dir)
    if not html_files:
        return 3, [f"ERROR: no .html under {site_dir} -- the site was not built."]

    missing = []
    wrong_token = []
    for page in html_files:
        text = page.read_text(encoding="utf-8", errors="replace")
        if TAG not in text:
            missing.append(page)
        elif expect_token and f'content="{expect_token}"' not in text:
            wrong_token.append(page)

    lines = [f"   {len(html_files)} built page(s) checked."]

    if missing:
        shown = ", ".join(str(p.relative_to(site_dir)) for p in missing[:5])
        more = "" if len(missing) <= 5 else f" (+{len(missing) - 5} more)"
        return 1, [
            *lines,
            f"ERROR: {len(missing)} page(s) carry no {TAG} (LDM-#2210). The "
            f"build succeeded, so nothing else would have told you: the tag "
            f"is emitted only when extra.google_site_verification is set, and "
            f"an absent key silently unverifies the property.",
            f"     missing from: {shown}{more}",
        ]

    if wrong_token:
        return 1, [
            *lines,
            f"ERROR: {len(wrong_token)} page(s) carry the tag with a token "
            f"that is not the expected one (LDM-#2210).",
        ]

    return 0, [
        *lines,
        f"ASSERTED: every built page carries {TAG}, so the property stays "
        f"verified for as long as this build is what deploys.",
    ]


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2

    site_dir = Path(argv[0])
    expect_token = None
    if "--expect-token" in argv:
        i = argv.index("--expect-token")
        if i + 1 >= len(argv):
            print("ERROR: --expect-token needs a value")
            return 2
        expect_token = argv[i + 1]

    if not site_dir.is_dir():
        print(f"ERROR: {site_dir} is not a directory")
        return 2

    code, lines = judge(site_dir, expect_token)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
