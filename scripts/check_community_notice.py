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

It also asserts, across the WHOLE site, that no admonition is titled with
the Material STYLE name where that name is not a label any author chose.

Material titles an untitled `!!! info` "Info" and an untitled `!!! danger`
"Danger". GitHub has no alert called either: its five labels are NOTE, TIP,
IMPORTANT, WARNING and CAUTION, and the hook maps IMPORTANT onto the `info`
style and CAUTION onto `danger`. So a rendered box reading "Info" or "Danger"
is not a style choice -- it is the author's word having been dropped on the
way through, and the reader sees the name of a CSS class instead of the name
of the thing. `note`/`tip`/`warning` are deliberately NOT checked this way:
there the style name and GitHub's label are the same word, so "Note" on a
`note` box is correct.

Usage:
    check_community_notice.py <site_dir>

Exit codes:
    0  the home page renders the shared notice, and no label was lost
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

# (class, title) pairs that mean a label was lost rather than chosen. A tuple
# of tuples, not a set literal at module scope, to keep RUF012 happy about
# mutable module state.
LOST_LABEL_PAIRS = (("info", "Info"), ("danger", "Danger"))

_ADMONITION = re.compile(
    r'<div class="admonition (?P<cls>[\w-]+)">\s*'
    r'<p class="admonition-title">(?P<title>[^<]*)</p>\s*'
    r"<p>(?P<body>.*?)</p>",
    re.S,
)


def _scan_for_lost_labels(site_dir: Path) -> list[str]:
    """Every page, not just the home page -- the hook applies site-wide."""
    hits: list[tuple[str, str, str]] = []
    for page in sorted(site_dir.rglob("*.html")):
        html = page.read_text(encoding="utf-8", errors="replace")
        for match in _ADMONITION.finditer(html):
            pair = (match.group("cls"), match.group("title"))
            if pair in LOST_LABEL_PAIRS:
                hits.append((str(page.relative_to(site_dir)), *pair))
    if not hits:
        return []
    lines = [
        "ERROR: an admonition is titled with its Material style name, which",
        "means the author's label was dropped rather than rendered:",
    ]
    lines += [
        f"  {page}: `admonition {cls}` titled {title!r}" for page, cls, title in hits
    ]
    lines += [
        "GitHub has no alert called 'Info' or 'Danger'. IMPORTANT renders as",
        "the `info` style and CAUTION as `danger`, and the hook is what keeps",
        "the LABEL. A box reading 'Info' means it stopped doing so.",
    ]
    return lines


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
    lost = _scan_for_lost_labels(site_dir)
    if lost:
        return 1, lost
    return 0, [
        f'OK: home page renders `admonition {EXPECTED_CLASS}` titled "{EXPECTED_TITLE}".',
        "OK: no admonition is titled with its Material style name.",
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
