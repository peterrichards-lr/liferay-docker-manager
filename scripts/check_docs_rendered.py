#!/usr/bin/env python3
"""Assert that the BUILT documentation site gives readers what the source asks for.

SHARED FILE. The canonical copy is scripts/check_docs_rendered.py on the main branch of
peterrichards-lr/ai-agent-template, which seeds it into every project bootstrapped with
--docs-site. Never edit a copy: change the canonical file in the template, through a PR there,
then copy it into each project unchanged. Per-site values belong in mkdocs.yml, under
`extra.docs_standard`, never in this file. scripts/check_template_drift.py compares the copies.
The rule is in the template's CONTRIBUTING.md, "Shared docs files", and
.agents/skills/docs-site-standard/SKILL.md.

Usage: check_docs_rendered.py [SITE_DIR] [--config mkdocs.yml]

Run it on the build output BEFORE the Pages upload, never after: a check that runs on the
deployed site reports a broken page that readers already have.

Why the output and not the source: on the three Liferay tools sites this was standardised on,
every failure that mattered passed a source-side check. Reading mkdocs.yml said "no markdown
extensions"; only the built HTML showed 33 GitHub alert markers printed to readers as literal
text and every diagram rendered as code. `mkdocs build --strict` passes a footer link that
404s on every nested page. So each assertion reads the HTML a reader gets.

Always checked:
  LEAKED-ALERT        no `[!NOTE]`-style marker is printed as text (the alerts hook ran)
  UNRENDERED-DIAGRAM  no mermaid block is rendered as code
  UNDATED-PAGE        every page shows its Last Updated / Last Reviewed footer
  ABOUT-LINK          every page's footer "About these docs" link resolves to a built page
  STYLE-TITLE         no alert is titled with Material's style name ("Info", "Danger")
  NAV                 the top-level nav is an ordered subset of STANDARD_NAV, with "About
                      these docs" present and last, and every nav label, top level and below,
                      is in sentence case (names in extra.docs_standard.proper_nouns allowed)
Checked when mkdocs.yml configures them:
  MISSING-VERIFICATION  extra.google_site_verification / extra.bing_site_verification:
                        the meta tag is on every page
  MISSING-SOCIAL        extra.social_image: every page carries an absolute og:image that
                        exists in the build
  NO-NOTICE           extra.docs_standard.notice: the home page shows it as an `info`
                      callout above its first section
  HOME-LAYOUT         extra.docs_standard.home_layout: the shared home-page layout

Exit codes: 0 every assertion passed; 1 an assertion failed, or nothing was scanned.
"""

import argparse
import re
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml

# Too few pages to be a real build; a near-empty directory would pass every assertion.
MIN_PAGES = 3

# The standard top-level navigation, in order (ai-agent-template#135). A site omits a section
# only when it has no pages, and never renames or reorders one. Only the labels are fixed: which
# page goes where is the Diátaxis decision in .agents/skills/documentation/SKILL.md.
STANDARD_NAV = (
    "Home",
    "Tutorials",
    "How-to guides",
    "Reference",
    "Explanation",
    "Troubleshooting",
    "Showcase",
    "Project and contributing",
    "About these docs",
)
ABOUT_LABEL = "About these docs"

ALERT_MARKER = re.compile(r"\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]")
FOOTER = re.compile(r"<p><em>Last Updated: ?\d{4}-\d{2}-\d{2}</em> \| "
                    r"<em>Last Reviewed: ?\d{4}-\d{2}-\d{2}</em></p>")
# The markup the alerts hook produces when a title is the STYLE name rather than GitHub's label.
STYLE_TITLES = ('<div class="admonition info"><p class="admonition-title">Info</p>',
                '<div class="admonition danger"><p class="admonition-title">Danger</p>')

# A label's words, for the sentence-case rule ("How-To Guides" and "About These Docs" fail).
_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")

# Emoji, for the no-emoji-in-headings rule: symbols (So) other than the typographic marks a
# heading may legitimately carry, plus the pieces emoji are built from that are not So -- the
# variation selector, the keycap mark, and the few emoji Unicode files under other categories
# (ℹ is a letter, ‼ and ⁉ are punctuation).
NOT_EMOJI = {"©", "®", "™"}                         # © ® ™
EMOJI_EXTRA = {"️", "⃣", "ℹ", "‼", "⁉"}


def has_emoji(text):
    return any((unicodedata.category(c) == "So" and c not in NOT_EMOJI) or c in EMOJI_EXTRA
               for c in text)


def slugify(text):
    """Python-Markdown's toc slug, which is how MkDocs derives every heading's id."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def is_title_case(label, names=()):
    """A word after the first is capitalised like an ordinary word (a capital, then lower case)
    and is not one of `names`. Acronyms (SSO) and inner-capital names (InfoSec, macOS) pass by
    their shape; other proper nouns go in extra.docs_standard.proper_nouns."""
    return any(w[0].isupper() and w[1:].islower() and w not in names
               for w in _WORD.findall(label)[1:])


def flatten(html):
    """One line, no whitespace between tags: what a reader sees, matched regardless of wrapping."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    return re.sub(r">\s+<", "><", " ".join(html.split()))


def load_config(path):
    """mkdocs.yml, tolerating MkDocs' `!!python/name:` tags."""

    class Tolerant(yaml.SafeLoader):
        pass

    Tolerant.add_multi_constructor("tag:yaml.org,2002:python/name:", lambda *_: None)
    Tolerant.add_multi_constructor("!", lambda *_: None)
    return yaml.load(Path(path).read_text(encoding="utf-8"), Loader=Tolerant) or {}


# --------------------------------------------------------------------------------------------
# HTML readers
# --------------------------------------------------------------------------------------------

class PrimaryNav(HTMLParser):
    """Material's primary navigation: the top-level labels in order, and every label in it.

    The page's own table of contents is embedded in the primary nav (nav.md-nav--secondary) and
    is skipped: those are the page's headings, not navigation labels.
    """

    def __init__(self):
        super().__init__()
        self.labels, self.all_labels = [], []
        self.nav_depth, self.ul_depth, self.toc_depth = 0, 0, 0
        self.in_item, self.in_label, self.top, self.buffer = False, False, False, ""

    def handle_starttag(self, tag, attrs):
        cls = (dict(attrs).get("class") or "").split()
        if tag == "nav":
            if self.nav_depth or "md-nav--primary" in cls:
                self.nav_depth += 1
                if self.toc_depth or "md-nav--secondary" in cls:
                    self.toc_depth += 1
            return
        if not self.nav_depth or self.toc_depth:
            return
        if tag == "ul":
            self.ul_depth += 1
        elif tag == "li" and self.ul_depth == 1:
            self.in_item, self.buffer = True, None
        elif tag == "span" and "md-ellipsis" in cls and not self.in_label:
            # The first label inside a top-level item is that item's own; any later one is a
            # page or group below it.
            self.top = self.in_item and self.ul_depth == 1 and self.buffer is None
            self.in_label, self.buffer = True, ""

    def handle_endtag(self, tag):
        if not self.nav_depth:
            return
        if tag == "nav":
            self.nav_depth -= 1
            self.toc_depth = max(0, self.toc_depth - 1)
            return
        if self.toc_depth:
            return
        if tag == "ul":
            self.ul_depth -= 1
        elif tag == "span" and self.in_label:
            self.in_label = False
            label = " ".join(self.buffer.split())
            self.all_labels.append(label)
            if self.top:
                self.labels.append(label)
        elif tag == "li" and self.ul_depth == 1:
            self.in_item = False

    def handle_data(self, data):
        if self.in_label:
            self.buffer += data


class FooterAbout(HTMLParser):
    """The href of the "About these docs" link inside the footer's .md-copyright block."""

    def __init__(self):
        super().__init__()
        self.depth, self.href, self.text, self.found = 0, None, "", []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and "md-copyright" in (a.get("class") or "").split():
            self.depth = 1
        elif tag == "div" and self.depth:
            self.depth += 1
        elif tag == "a" and self.depth:
            self.href, self.text = a.get("href", ""), ""

    def handle_endtag(self, tag):
        if tag == "div" and self.depth:
            self.depth -= 1
        elif tag == "a" and self.href is not None:
            if " ".join(self.text.split()) == ABOUT_LABEL:
                self.found.append(self.href)
            self.href = None

    def handle_data(self, data):
        if self.href is not None:
            self.text += data


class Article(HTMLParser):
    """The article's headings, paragraphs and tables, in document order."""

    def __init__(self):
        super().__init__()
        self.items, self.depth, self.cur, self.cell, self.skip = [], 0, None, None, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "article":
            self.depth += 1
        if not self.depth:
            return
        if tag == "a" and "headerlink" in (a.get("class") or ""):
            self.skip += 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.cur = {"kind": tag, "id": a.get("id", ""), "text": ""}
        elif tag == "p" and self.cur is None and self.cell is None:
            self.cur = {"kind": "p", "text": ""}
        elif tag == "table":
            self.items.append({"kind": "table", "head": [], "rows": []})
        elif tag == "tr" and self.items and self.items[-1]["kind"] == "table":
            self.items[-1]["rows"].append([])
        elif tag in ("th", "td"):
            self.cell = {"tag": tag, "text": "", "href": None}
        elif tag == "a" and self.cell is not None and self.cell["href"] is None:
            self.cell["href"] = a.get("href", "")

    def handle_endtag(self, tag):
        if tag == "article" and self.depth:
            self.depth -= 1
        if not self.depth:
            return
        if tag == "a" and self.skip:
            self.skip -= 1
        if self.cur is not None and tag == self.cur["kind"]:
            self.cur["text"] = " ".join(self.cur["text"].split())
            self.items.append(self.cur)
            self.cur = None
        elif tag in ("th", "td") and self.cell is not None:
            t = self.items[-1]
            text = " ".join(self.cell["text"].split())
            if self.cell["tag"] == "th":
                t["head"].append(text)
            else:
                t["rows"][-1].append((text, self.cell["href"]))
            self.cell = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.cell is not None:
            self.cell["text"] += data
        elif self.cur is not None:
            self.cur["text"] += data


# --------------------------------------------------------------------------------------------
# Checks. Each returns a list of problem lines; empty means it passed.
# --------------------------------------------------------------------------------------------

def check_leaked_alerts(pages):
    return [f"{rel}: {len(ALERT_MARKER.findall(html))} literal alert marker(s)"
            for rel, html in pages.items() if ALERT_MARKER.search(html)]


def check_diagrams(pages):
    return [f"{rel}: a mermaid block rendered as code"
            for rel, html in pages.items() if 'class="language-mermaid"' in html]


def check_footers(pages):
    return [f"{rel}: no Last Updated / Last Reviewed footer"
            for rel, html in pages.items() if rel != "404.html" and not FOOTER.search(html)]


def check_about_links(site, pages):
    """Resolved per page, because the failure is per depth: a relative "about/" works on the
    home page and 404s on every nested one, and `mkdocs build --strict` does not see it."""
    problems = []
    for rel, html in pages.items():
        if rel == "404.html":      # served at every depth, so it cannot carry a relative link
            continue
        parser = FooterAbout()
        parser.feed(html)
        if not parser.found:
            problems.append(f"{rel}: no \"{ABOUT_LABEL}\" link in the footer")
            continue
        target = urlparse(urljoin("http://site/" + rel, parser.found[0])).path.lstrip("/")
        if not target or target.endswith("/"):
            target += "index.html"
        if not (site / target).is_file():
            problems.append(f"{rel}: footer link {parser.found[0]!r} resolves to /{target}, "
                            "which is not in the build")
    return problems


def check_style_titles(pages):
    return [f"{rel}: an alert titled \"Info\" or \"Danger\""
            for rel, html in pages.items() if any(t in flatten(html) for t in STYLE_TITLES)]


def check_nav(home_html, names=()):
    parser = PrimaryNav()
    parser.feed(home_html)
    labels = parser.labels
    problems = []
    if not labels:
        return ["no top-level navigation found on the home page"], labels
    unknown = [lab for lab in labels if lab not in STANDARD_NAV]
    if unknown:
        problems.append(f"labels not in the standard list: {unknown!r}")
    known = [lab for lab in labels if lab in STANDARD_NAV]
    if known != sorted(known, key=STANDARD_NAV.index):
        problems.append(f"out of order: {known!r}")
    if labels[-1:] != [ABOUT_LABEL]:
        problems.append(f"\"{ABOUT_LABEL}\" must be present and last; the last label is "
                        f"{labels[-1]!r}")
    # Every label, top level and below; the page's own table of contents is not nav and is
    # skipped by the parser.
    titled = [lab for lab in dict.fromkeys(parser.all_labels) if is_title_case(lab, names)]
    if titled:
        problems.append(f"title case (use sentence case): {titled!r}")
    if problems:
        problems.append(f"top-level labels are {labels!r}; the standard order is "
                        f"{list(STANDARD_NAV)!r}")
    return problems, labels


def check_verification(pages, extra):
    problems = []
    for key, name in (("google_site_verification", "google-site-verification"),
                      ("bing_site_verification", "msvalidate.01")):
        if not extra.get(key):
            continue
        missing = [rel for rel, html in pages.items() if f'name="{name}"' not in html]
        problems += [f"{rel}: no {name} meta tag (extra.{key} is set)" for rel in missing]
    return problems


def check_social(site, pages, config):
    image = (config.get("extra") or {}).get("social_image")
    if not image:
        return []
    site_url = config.get("site_url") or ""
    if site_url and not site_url.endswith("/"):
        site_url += "/"          # as MkDocs normalises it before the template sees it
    if not site_url.startswith(("http://", "https://")):
        return ["extra.social_image is set but site_url is not an absolute URL, so og:image "
                "cannot be absolute: a scraper has no page to resolve a relative one against"]
    want = site_url + image
    problems = [] if (site / image).is_file() else [f"{image} is not in the build"]
    for rel, html in pages.items():
        if f'<meta property="og:image" content="{want}">' not in html:
            problems.append(f"{rel}: no og:image {want!r}")
    return problems


def check_notice(home_html, notice):
    """The notice is an `info` callout ABOVE the first section heading, with its title, its
    text and its link. Inside the callout, not beside one: an unrelated admonition above a
    plain-paragraph notice satisfies separate searches."""
    flat = flatten(home_html)
    above = flat.split("<h2", 1)[0] if "<h2" in flat else ""
    marker = 'class="admonition info"'
    if marker not in above:
        return ["no `info` callout above the home page's first section heading"]
    # Every `info` callout above the heading is a candidate, not just the first: [!IMPORTANT]
    # renders as `info` too, and one placed before the notice must not hide it.
    title = notice.get("title", "")
    link = notice.get("link_text")
    best = None
    for callout in above.split(marker)[1:]:
        callout = callout.split("</div>", 1)[0]
        problems = []
        if f'<p class="admonition-title">{title}</p>' not in callout:
            problems.append(f"the callout is not titled {title!r}")
        if notice.get("text") and notice["text"] not in callout:
            problems.append(f"the callout does not say {notice['text']!r}")
        if link and not re.search(r'<a href="about/">' + re.escape(link) + "</a>", callout):
            problems.append(f"the callout has no {link!r} link to about/")
        if not problems:
            return []
        if best is None or len(problems) < len(best):
            best = problems
    return best


def check_home_layout(home_html, standard, site_name):
    """The shared home-page layout (lfr-tunnel #2401, generalised). Returns (problems, passes)."""
    p = Article()
    p.feed(home_html)
    items = p.items
    for t in items:
        if t["kind"] == "table":
            t["rows"] = [r for r in t["rows"] if r]     # the header row holds <th> only
    heads = [i for i in items if i["kind"] in ("h1", "h2", "h3", "h4", "h5", "h6")]
    h2 = [h for h in heads if h["kind"] == "h2"]
    ok, bad = [], []

    def check(cond, good, problem):
        (ok if cond else bad).append(good if cond else problem)

    def following(anchor, n=1):
        """The n items directly after the h2 with this id -- not merely somewhere under it."""
        for i, it in enumerate(items):
            if it["kind"] == "h2" and it["id"] == anchor:
                return items[i + 1:i + 1 + n]
        return []

    project = standard.get("project_name") or site_name
    want_h1 = f"{project} documentation"
    h1 = [h["text"] for h in heads if h["kind"] == "h1"]
    check(h1 == [want_h1], f'H1 is "{want_h1}"', f'H1 is {h1!r}, expected ["{want_h1}"]')

    related = standard.get("related") or {}
    heading = related.get("heading", "")
    first = [("i-want-to", "I want to…"), ("by-subject", "By subject")]
    last = [(slugify(heading), heading)] if heading else []
    got = [(h["id"], h["text"]) for h in h2]
    check(got[:2] == first and (not last or got[-1:] == last),
          "H2s: \"I want to…\" first, \"By subject\" second"
          + (f", \"{heading}\" last" if heading else ""),
          f"H2s (id, text) are {got!r}; the first two must be {first!r}"
          + (f" and the last {last[0]!r}" if last else "")
          + " (project-specific sections go between By subject and the related section)")

    nxt = following("i-want-to")
    want = nxt[0] if nxt and nxt[0]["kind"] == "table" else None
    check(want is not None and want["head"] == ["I want to…", "Go to"],
          'the table directly under #i-want-to is headed "I want to…" | "Go to"',
          "directly under #i-want-to: "
          + (f'a table headed {want["head"]!r}' if want
             else f'{nxt[0]["kind"] if nxt else "nothing"}, not the table'))
    rows = [r[0][0] for r in (want or {}).get("rows", []) if r]
    odd = [r for r in rows if not r[:1].isupper() or r.endswith(".")]
    check(bool(rows) and not odd,
          f"{len(rows)} task rows start with a capital letter and have no trailing full stop",
          f"task rows that start lower-case or with a '…', or end with a full stop: "
          f"{odd or 'no rows'!r}")

    subs, inside = [], False
    for h in heads:
        if h["kind"] == "h2":
            inside = h["id"] == "by-subject"
        elif h["kind"] == "h3" and inside:
            subs.append(h["text"])
    numbered = [s for s in subs if s[:1].isdigit()]
    check(bool(subs) and not numbered, f"#by-subject has {len(subs)} unnumbered ### subsections",
          f"#by-subject subsections: {subs!r} (need at least one, none numbered)")

    if heading:
        anchor = slugify(heading)
        word = heading.split()[0]
        named = [h["text"] for h in heads if re.search(rf"\b{re.escape(word)}\b", h["text"], re.I)]
        check(named == [heading], f'exactly one "{word}" heading',
              f'headings with the word "{word}": {named!r}')

        nxt = following(anchor, 2)
        intro = nxt[0] if nxt and nxt[0]["kind"] == "p" else None
        check(intro is not None and intro["text"] == related.get("intro", ""),
              f"the line directly under #{anchor} is the shared intro, word for word",
              f"directly under #{anchor}: "
              + (f'{intro["text"]!r}' if intro
                 else f'{nxt[0]["kind"] if nxt else "nothing"}')
              + f', not the shared intro {related.get("intro", "")!r}')
        rel = nxt[1] if len(nxt) > 1 and nxt[1]["kind"] == "table" else None
        check(rel is not None and rel["head"] == ["Tool", "What it does"],
              'the table directly after the intro is headed "Tool" | "What it does"',
              "after the related intro: "
              + (f'a table headed {rel["head"]!r}' if rel else "no table directly after it"))

        expected = [(r["name"], None, "You are here. " + r["description"])
                    if r["name"] == project else (r["name"], r["url"], r["description"])
                    for r in related.get("rows", [])]
        actual = [(r[0][0], r[0][1], r[1][0] if len(r) > 1 else None)
                  for r in (rel or {}).get("rows", [])]
        check(actual == expected,
              f"related rows: {len(expected)} shared rows word for word with their links, in "
              f'order, {project}\'s unlinked and starting "You are here."',
              f"related rows (name, link, description) are {actual!r}, expected {expected!r}")

    emoji = [h["text"] for h in heads if has_emoji(h["text"])]
    check(not emoji, "no emoji in any heading", f"headings with emoji: {emoji!r}")
    return bad, ok


# --------------------------------------------------------------------------------------------

def run(site, config):
    """Run every check; print the report; return the exit code."""
    site = Path(site)
    if not site.is_dir():
        print(f"[NO-SCAN] {site} is not a directory -- nothing was checked.\n"
              f"  Build the site first: mkdocs build --site-dir {site}")
        return 1
    pages = {p.relative_to(site).as_posix(): p.read_text(encoding="utf-8", errors="replace")
             for p in sorted(site.rglob("*.html"))}
    if len(pages) < MIN_PAGES:
        print(f"[NO-SCAN] only {len(pages)} HTML page(s) under {site}. That is too few to be a "
              "real build, and a near-empty directory would pass every assertion.")
        return 1
    home = pages.get("index.html", "")
    extra = config.get("extra") or {}
    standard = extra.get("docs_standard") or {}

    failures, passes = {}, []
    always = {
        "LEAKED-ALERT": check_leaked_alerts(pages),
        "UNRENDERED-DIAGRAM": check_diagrams(pages),
        "UNDATED-PAGE": check_footers(pages),
        "ABOUT-LINK": check_about_links(site, pages),
        "STYLE-TITLE": check_style_titles(pages),
        "MISSING-VERIFICATION": check_verification(pages, extra),
        "MISSING-SOCIAL": check_social(site, pages, config),
    }
    nav_problems, labels = check_nav(
        home, set(standard.get("proper_nouns") or ())) if home else (["no home page (index.html)"], [])
    always["NAV"] = nav_problems
    if standard.get("notice"):
        always["NO-NOTICE"] = check_notice(home, standard["notice"])
    for name, problems in always.items():
        if problems:
            failures[name] = problems
    # Name only the checks that ran: an unconfigured one checked nothing and did not "pass".
    ran = [name for name in always if name not in ("MISSING-VERIFICATION", "MISSING-SOCIAL")]
    if extra.get("google_site_verification") or extra.get("bing_site_verification"):
        ran.append("MISSING-VERIFICATION")
    if extra.get("social_image"):
        ran.append("MISSING-SOCIAL")
    if standard.get("home_layout"):
        bad, ok = check_home_layout(home, standard, config.get("site_name", ""))
        if bad:
            failures["HOME-LAYOUT"] = bad
        passes += [f"home: {line}" for line in ok]

    if failures:
        for name, problems in failures.items():
            print(f"[{name}]")
            for line in problems[:40]:
                print(f"  {line}")
            if len(problems) > 40:
                print(f"  ... and {len(problems) - 40} more")
        print("\n❌ The built site does not give readers what the source asks for. "
              "See .agents/skills/docs-site-standard/SKILL.md.")
        return 1

    for line in passes:
        print(f"  ✓ {line}")
    print(f"  ✓ nav: {' › '.join(labels)}")
    checked = ", ".join(sorted(ran) + (["HOME-LAYOUT"] if standard.get("home_layout") else []))
    print(f"✅ {len(pages)} page(s) pass: {checked}.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("site", nargs="?", default="_site", help="built site directory")
    parser.add_argument("--config", default="mkdocs.yml", help="the MkDocs config the site was "
                        "built from (default: mkdocs.yml)")
    args = parser.parse_args(argv)
    return run(args.site, load_config(args.config))


if __name__ == "__main__":
    sys.exit(main())
