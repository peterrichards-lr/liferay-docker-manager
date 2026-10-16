"""Convert GitHub alert blockquotes into Material admonitions at build time.

SHARED FILE. The canonical copy is scripts/mkdocs_github_alerts.py on the main branch of
peterrichards-lr/ai-agent-template, which seeds it into every project bootstrapped with
--docs-site. Never edit a copy: change the canonical file in the template, through a PR there,
then copy it into each project unchanged. A project may keep it at another path (LDM keeps it
at mkdocs_hooks/github_alerts.py) by recording the mapping in .agents/TEMPLATE_REF.md, and
scripts/check_template_drift.py compares the copies. The rule is in the template's
CONTRIBUTING.md, "Shared docs files", and .agents/skills/docs-site-standard/SKILL.md.

The docs are read on two surfaces. In the repository and on github.com, `> [!WARNING]` is
native syntax and renders as a callout. In the built site it is not: Python-Markdown sees an
ordinary blockquote, so the marker is printed to the reader as literal text and the callout
loses the styling that made it a callout. Measured before this hook existed: 33 markers in
the lfr-tunnel site's published HTML (14 NOTE, 9 IMPORTANT, 5 WARNING, 3 CAUTION, 2 TIP; its
AWS guide alone leaked 12) and 57 in LDM's.

Converting the SOURCE to Material's `!!! warning` syntax would fix the site and break GitHub,
which is where contributors read these files. So the source stays GitHub-native and the site
build translates: when two renderers disagree, the fix is the build, not picking a side.

A hook rather than a plugin, deliberately: `hooks:` is core MkDocs, so this adds no
third-party dependency, and MkDocs 2.0 removes the plugin system altogether (LDM-#1814).

NOT converted, on purpose:

- Anything inside a fenced code block. A doc that *explains* this syntax must be able to show
  it, so writing one cannot silently mangle the example.
- A second marker part-way through a blockquote. GitHub honours only the marker on the
  blockquote's first line, so a later `> [!TIP]` in the same quote renders literally on
  GitHub too. Reproducing that keeps the two surfaces honest: the fix is to break the
  blockquote in the source, which fixes both at once.

One known divergence the other way: GitHub recognises an alert only when the marker sits
alone on its line, but _MARKER below also accepts text after it (`> [!NOTE] text`), which
this hook converts and GitHub renders as a plain blockquote. No page on the three sites uses
that shape; write the marker alone on its line.
"""

import re
import unicodedata

# GitHub's five types, mapped to the Material admonition that carries the same weight.
# IMPORTANT has no exact Material twin; `info` is the closest in both colour and intent.
# CAUTION maps to `danger`: Material has no `caution`, and `danger` is its strongest, which
# matches GitHub's intent for the label.
_TYPES = {
    "NOTE": "note",
    "TIP": "tip",
    "IMPORTANT": "info",
    "WARNING": "warning",
    "CAUTION": "danger",
}

# The title is GitHub's own label; only the style is borrowed from Material (the family
# decision on lfr-tunnel #2391). A bare `!!! info` is titled by Material with the STYLE name,
# so an IMPORTANT box read "Info" and a CAUTION box "Danger" -- the reader lost the word the
# author chose. Emitting the label explicitly keeps it. No custom `important` style either:
# the label matters more than the colour, and fewer shared rules means less to keep in sync.
_LABELS = {
    "NOTE": "Note",
    "TIP": "Tip",
    "IMPORTANT": "Important",
    "WARNING": "Warning",
    "CAUTION": "Caution",
}

_MARKER = re.compile(r"^>\s*\[!(" + "|".join(_TYPES) + r")\]\s*(.*)$")

# A GitHub alert cannot carry a title -- the marker must sit alone on its line or github.com
# degrades it to a plain blockquote -- so a source that wants one writes it as the first line's
# lead: an emoji, a bold phrase, an em dash. `> ℹ️ **Community project** — not an official
# Liferay product…` reads as a title on GitHub and becomes one here (lfr-tunnel #2391). The
# emoji is dropped, because the admonition draws its own icon.
#
# THE EMOJI IS THE OPT-IN, NOT DECORATION, and the strictness is the whole design. Measured
# across LDM's 70 alerts: requiring emoji + bold + em dash lifts exactly one title; accepting a
# bold lead alone would have lifted 29, turning sentences into headings (`**The bundle does not
# carry a DXP activation key, and cannot** — …`). The lfr-tunnel aws_setup_guide.md has the
# same shape: a NOTE whose bold lead runs on past the dash. Measure before ever relaxing this;
# the failure is silent, because every retitled callout still renders as a valid admonition.
_TITLE_LEAD = re.compile(r"^(\S+)\s+\*\*([^*]+)\*\*\s+—\s+(.*)$")


# A lifted title renders as `info`, whatever the alert's type: option B, the family decision on
# lfr-tunnel #2391. GitHub has five alert types and none of them is `info`, so a notice that has
# to render as the `info` callout cannot say so in GitHub-native source. The community-project
# notice is written as `[!NOTE]` on all three sites and renders as the shared `info` callout
# titled "Community project". It is the lift that decides, not the emoji; the strict opt-in
# above is what keeps this from reaching any other alert. Every other alert keeps its _TYPES
# style and its _LABELS title.
_LIFTED_TITLE_TYPE = "info"


def _is_emoji(token: str) -> bool:
    """True when every character is a symbol, or one of the joiners and variation selectors an
    emoji is built from. Not "non-ASCII": that let `→`'s neighbours in, and a word such as
    `Важно` would have been dropped from the text. Not "not a word character" either: Python
    counts ℹ (U+2139) as a letter, so the category is checked for it explicitly."""
    return all(unicodedata.category(ch) in ("So", "Sk", "Mn", "Cf") or ch == "\u2139"
               for ch in token)

# CommonMark fences, and the rule that matters here: a fence is CLOSED only by a line of the
# same character, at least as long, carrying no info string. `` ```bash `` therefore cannot
# close a block -- it is content inside one.
#
# Tracking fences as a naive toggle instead got this wrong on the largest page of the
# lfr-tunnel site: its docs/server/setup_guide.md has a ```bash line sitting inside another bash
# block; the toggle desynced there and stayed desynced, so every alert in the remaining 1200
# lines was treated as being inside code and left unconverted. Three survived the first build
# because of it.
_FENCE_OPEN = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")


def _convert(markdown: str) -> str:
    lines = markdown.split("\n")
    out: list[str] = []
    i = 0
    in_fence = False
    fence_char, fence_len = "", 0

    while i < len(lines):
        line = lines[i]

        # Never rewrite inside a fenced block: a doc that *documents* this syntax would
        # otherwise have its example silently converted.
        fence = _FENCE_OPEN.match(line)
        if fence is not None:
            marker = fence.group(2)
            info = fence.group(3).strip()
            if in_fence:
                # Closes only if it is the same character, no shorter, and bare.
                if marker[0] == fence_char and len(marker) >= fence_len and not info:
                    in_fence = False
            else:
                in_fence, fence_char, fence_len = True, marker[0], len(marker)
            out.append(line)
            i += 1
            continue

        match = None if in_fence else _MARKER.match(line)
        if match is None:
            out.append(line)
            i += 1
            continue

        kind = _TYPES[match.group(1)]
        label = _LABELS[match.group(1)]
        trailing = match.group(2).strip()

        # The body is collected BEFORE the admonition header is emitted, because a lifted title
        # comes out of the body's first line and changes both the header text and its type.
        i += 1
        body = [trailing] if trailing else []
        while i < len(lines) and lines[i].startswith(">"):
            body.append(re.sub(r"^>\s?", "", lines[i]))
            i += 1

        title = _TITLE_LEAD.match(body[0]) if body else None
        if title is not None and not _is_emoji(title.group(1)):
            title = None
        if title is not None:
            # The remainder now opens a paragraph under its own heading, so it takes a capital --
            # but only an ordinary lower-case word. `lfr-tunnel`, `macOS` and `-ports` are names
            # whose case is the point, and are left exactly as written.
            rest = title.group(3)
            first = rest.split(" ", 1)[0]
            if re.fullmatch(r"[a-z]+[.,;:]?", first):
                rest = rest[:1].upper() + rest[1:]
            body[0] = rest
            kind = _LIFTED_TITLE_TYPE
            out.append(f'!!! {kind} "{title.group(2).strip()}"')
        else:
            out.append(f'!!! {kind} "{label}"')
        out.append("")
        # Blank lines stay blank: indenting them would end the admonition early on some
        # Python-Markdown versions and is invisible either way.
        out.extend("    " + b if b.strip() else "" for b in body)
        out.append("")

    return "\n".join(out)


def on_page_markdown(markdown, page=None, config=None, files=None):  # noqa: ARG001
    return _convert(markdown)
