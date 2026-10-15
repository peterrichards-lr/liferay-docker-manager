#!/usr/bin/env python3
"""Assert the docs home renders the shared community-project notice.

LDM-#2215: the three Liferay tools docs sites must render this notice
identically, and for a while they did not -- LDM emitted `admonition note`
titled "Note" with the disclaimer buried in the body, while the other two
emitted `admonition info` titled "Community project". Nothing noticed,
because every one of them was a valid build of a valid page.

The claim is about RENDERED OUTPUT, not source. The source is `> [!NOTE]` in
all three repos; `mkdocs_hooks/github_alerts.py` lifts the title and switches
the type. Any of three separate things silently reverts it -- the hook being
dropped from `hooks:`, the title lead losing its emoji or its em dash, or the
notice being reworded -- and each leaves the build green. So this reads the
built HTML, exactly as check_site_verification.py does and for the same
reason.

Usage:
    check_community_notice.py <site_dir>

Exit codes:
    0  the home page renders the shared notice
    1  it does not -- the message shows what was rendered instead
    2  bad usage
    3  no home page found; the CALLER must treat this as a FAILURE.
"""

import re
import sys
from pathlib import Path

EXPECTED_CLASS = "info"
EXPECTED_TITLE = "Community project"
EXPECTED_BODY = "Not an official Liferay product and not covered by Liferay Support."

_ADMONITION = re.compile(
    r'<div class="admonition (?P<cls>[\w-]+)">\s*'
    r'<p class="admonition-title">(?P<title>[^<]*)</p>\s*'
    r"<p>(?P<body>.*?)</p>",
    re.S,
)


def judge(site_dir: Path):
    home = site_dir / "index.html"
    if not home.is_file():
        return 3, [f"ERROR: no index.html under {site_dir} -- the site was not built."]

    html = home.read_text(encoding="utf-8", errors="replace")
    found = list(_ADMONITION.finditer(html))
    if not found:
        return 1, [
            "ERROR: the home page renders no admonition at all.",
            "  The community-project notice is the first thing a reader should",
            "  see. Check `hooks:` still lists mkdocs_hooks/github_alerts.py --",
            "  without it the source stays a plain blockquote and the literal",
            "  `[!NOTE]` marker is shown to the reader.",
        ]

    first = found[0]
    body = " ".join(re.sub(r"<[^>]+>", " ", first.group("body")).split())
    problems = []
    if first.group("cls") != EXPECTED_CLASS:
        problems.append(
            f"  class:  {first.group('cls')!r}  (expected {EXPECTED_CLASS!r})"
        )
    if first.group("title") != EXPECTED_TITLE:
        problems.append(
            f"  title:  {first.group('title')!r}  (expected {EXPECTED_TITLE!r})"
        )
    if not body.startswith(EXPECTED_BODY):
        problems.append(
            f"  body:   {body[:90]!r}\n          (expected it to start {EXPECTED_BODY!r})"
        )

    if problems:
        return 1, [
            "ERROR: the home page's notice does not match the shared family form.",
            "The three Liferay tools docs sites must render this identically:",
            *problems,
        ]
    return 0, [
        f'OK: home page renders `admonition {EXPECTED_CLASS}` titled "{EXPECTED_TITLE}".'
    ]


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    code, lines = judge(Path(args[0]))
    print("\n".join(lines), file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
