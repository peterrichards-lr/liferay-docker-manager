#!/usr/bin/env python3
"""Assert the docs home page renders the shared Liferay tools layout.

LDM-#2222: the three sites' home pages had drifted in title, in the shape of
the "I want to..." table, in whether the subject index was numbered, and in
where the related-tools section sat. None of that breaks a build, and none of
it is visible in a diff of one repo -- it only shows when the three are opened
side by side, which nobody does routinely.

So the structure is asserted on the BUILT page, where the anchors actually
exist. An `##` that quietly becomes `###`, or an emoji added to a heading,
changes the anchor and silently breaks every inbound link to it.

Usage:
    check_home_layout.py <site_dir>

Exit codes:
    0  the home page matches the shared layout
    1  it does not -- every failing assertion is listed
    2  bad usage
    3  no home page found; the CALLER must treat this as a FAILURE.
"""

import re
import sys
import unicodedata
from pathlib import Path

EXPECTED_H1 = "Liferay Docker Manager documentation"
REQUIRED_H2_ORDER = ["i-want-to", "by-subject", "related-liferay-tools"]
TASK_HEADERS = ["I want to…", "Go to"]
RELATED_HEADERS = ["Tool", "What it does"]
RELATED_ROWS = [
    "Liferay Docker Manager",
    "Liferay AI Commerce Accelerator",
    "Liferay Tunnel",
]
HERE = "You are here."


# Material appends a permalink anchor inside every heading. Its content is the
# pilcrow entity, which survives tag-stripping and would make every heading
# comparison fail on a character no reader sees.
_HEADERLINK = re.compile(r'<a class="headerlink".*?</a>', re.S)


def _text(fragment: str) -> str:
    fragment = _HEADERLINK.sub("", fragment)
    plain = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(plain.replace("\u00b6", " ").replace("&para;", " ").split())


def _has_emoji(s: str) -> bool:
    # So/Sk only. `…` is Po and `—` is Pd, both legitimate in a heading; this
    # must not flag them or the "I want to…" heading fails on its own name.
    return any(unicodedata.category(ch) in ("So", "Sk") for ch in s)


def _table_after(article: str, anchor: str):
    """Headers and first-column cells of the first table after an anchor."""
    at = article.find(f'id="{anchor}"')
    if at == -1:
        return None, []
    seg = article[at:]
    end = seg.find("</table>")
    seg = seg[: end + 8] if end != -1 else seg[:3000]
    headers = [_text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", seg, re.S)]
    rows = [_text(r) for r in re.findall(r"<tr>\s*<td[^>]*>(.*?)</td>", seg, re.S)]
    return headers, rows


def _check_h1(article, ok, bad):
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", article, re.S)
    text = _text(h1.group(1)) if h1 else ""
    (ok if text == EXPECTED_H1 else bad).append(
        f"h1 is {EXPECTED_H1!r}"
        if text == EXPECTED_H1
        else f"  h1 is {text!r}, expected {EXPECTED_H1!r}"
    )


def _check_anchors(article, ok, bad):
    ids = re.findall(r'<h2 id="([^"]+)"', article)
    present = [i for i in ids if i in REQUIRED_H2_ORDER]
    if present == REQUIRED_H2_ORDER:
        ok.append(f"h2 anchors {' -> '.join(REQUIRED_H2_ORDER)}")
    else:
        bad.append(
            f"  h2 anchors are {present}, expected {REQUIRED_H2_ORDER} in that order"
        )


def _check_headers(article, anchor, expected, ok, bad):
    headers, rows = _table_after(article, anchor)
    if headers is not None and headers[:2] == expected:
        ok.append(f"#{anchor} headers {expected}")
    else:
        bad.append(f"  #{anchor} headers are {headers}, expected {expected}")
    return rows


def _check_related(article, rows, ok, bad):
    if rows == RELATED_ROWS:
        ok.append(f"related-tools rows {RELATED_ROWS}")
    else:
        bad.append(f"  related-tools rows are {rows}, expected exactly {RELATED_ROWS}")

    at = article.find('id="related-liferay-tools"')
    here = article[at:].count(HERE) if at != -1 else 0
    if here == 1:
        ok.append(f"exactly one {HERE!r} row")
    else:
        bad.append(f"  {here} rows say {HERE!r}; exactly one must (this tool's)")


def _check_no_emoji(article, ok, bad):
    found = [
        _text(t)
        for t in re.findall(r"<h[123][^>]*>(.*?)</h[123]>", article, re.S)
        if _has_emoji(_text(t))
    ]
    (ok if not found else bad).append(
        "no emoji in h1-h3" if not found else f"  headings contain emoji: {found}"
    )


def judge(site_dir: Path):
    home = site_dir / "index.html"
    if not home.is_file():
        return 3, [f"ERROR: no index.html under {site_dir} -- the site was not built."]

    html = home.read_text(encoding="utf-8", errors="replace")
    article = re.search(r"<article[^>]*>(.*?)</article>", html, re.S)
    if not article:
        return 1, [
            "ERROR: the home page has no <article> -- cannot read its structure."
        ]
    article = article.group(1)

    ok: list[str] = []
    bad: list[str] = []
    _check_h1(article, ok, bad)
    _check_anchors(article, ok, bad)
    _check_headers(article, "i-want-to", TASK_HEADERS, ok, bad)
    rows = _check_headers(article, "related-liferay-tools", RELATED_HEADERS, ok, bad)
    _check_related(article, rows, ok, bad)
    _check_no_emoji(article, ok, bad)

    if bad:
        return 1, [
            "ERROR: the home page does not match the shared layout (LDM-#2222):",
            *bad,
        ]
    return 0, ["OK: home page matches the shared layout."] + [f"  - {p}" for p in ok]


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
