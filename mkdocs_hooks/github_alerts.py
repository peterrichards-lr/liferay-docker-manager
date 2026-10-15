"""Render GitHub-style alerts as MkDocs admonitions at build time (LDM-#1820).

The docs use GitHub's alert syntax throughout:

    > [!NOTE]
    > Something worth knowing.

GitHub renders that as a styled callout. MkDocs has no idea what it is and
emits a plain blockquote with the marker still in it, so 57 places showed a
literal "[!NOTE]" to the reader -- disproportionately the warnings, i.e. the
most load-bearing sentences in the docs.

Converting the sources to `!!! note` would fix the site and break GitHub, where
these files are also read. So the conversion happens here, at build time, and
the sources keep the syntax that works in both places.

A hook rather than a plugin deliberately: `hooks:` is core MkDocs, so this adds
no third-party dependency -- see the toolchain note in LDM-#1814 about MkDocs
2.0 removing the plugin system.

NOT converted, on purpose:

- Anything inside a fenced code block. A doc that *explains* this syntax must be
  able to show it. No such doc exists today; the guard is here so writing one
  cannot silently mangle it.
- A second marker part-way through a blockquote. GitHub honours only the marker
  on the blockquote's first line, so `> [!NOTE] ... > [!TIP] ...` as one quote
  renders the second literally *on GitHub too*. Reproducing that faithfully
  keeps the two surfaces honest: the fix is to break the blockquote in the
  source, which fixes both at once.
"""

import re
import unicodedata

# CAUTION maps to `danger`: Material has no `caution` admonition, and `danger`
# is its strongest, which matches GitHub's intent for the label.
_KINDS = {
    "NOTE": "note",
    "TIP": "tip",
    "IMPORTANT": "important",
    "WARNING": "warning",
    "CAUTION": "danger",
}

_MARKER = re.compile(r"^(?P<indent>[ \t]*)> ?\[!(?P<kind>[A-Z]+)\][ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")

# LDM-#2215. A GitHub alert cannot carry a title -- the marker must sit alone on
# its line or github.com degrades it to a plain blockquote -- so a source that
# wants one writes it as the first line's lead: an emoji, a bold phrase, an em
# dash. `> ℹ️ **Community project** — not an official Liferay product…` reads as
# a title on GitHub and becomes one here. The emoji is dropped, because the
# admonition draws its own icon.
#
# THE EMOJI IS THE OPT-IN, NOT DECORATION, and the strictness is the whole
# design. Measured across this repository's 70 alerts: requiring emoji + bold +
# em dash lifts exactly ONE title. Accepting a bold lead alone would have lifted
# 29, cutting sentences in half -- `**The bundle does not carry a DXP activation
# key, and cannot** — …` would have become a heading.
_TITLE_LEAD = re.compile(r"^(\S+)\s+\*\*([^*]+)\*\*\s+—\s+(.*)$")

# "Option B", the family decision across the three Liferay tools docs sites: an
# alert whose title was LIFTED renders as `info`, whatever its own type. The
# community-project notice is written `> [!NOTE]` in every repo, for one source
# form, and must render as the same shared `info` callout titled "Community
# project" on all three. Alerts without a lifted title keep the mapping above.
_TITLED_KIND = "info"


def _is_emoji(token: str) -> bool:
    """True when every character is a symbol, or a joiner an emoji is built from.

    Not "non-ASCII": that admitted `→` and would have dropped a word such as
    `Важно` from the text. Not "not a word character" either -- Python counts
    ℹ (U+2139) as a letter, so that codepoint is allowed explicitly.
    """
    return all(
        unicodedata.category(ch) in ("So", "Sk", "Mn", "Cf") or ch == "\u2139"
        for ch in token
    )


def on_page_markdown(markdown: str, **_kwargs: object) -> str:
    lines = markdown.split("\n")
    out: list[str] = []
    i = 0
    in_fence = False

    while i < len(lines):
        line = lines[i]

        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        match = None if in_fence else _MARKER.match(line)
        if match is None or match.group("kind") not in _KINDS:
            out.append(line)
            i += 1
            continue

        indent = match.group("indent")
        kind = _KINDS[match.group("kind")]
        i += 1

        # The body is collected BEFORE the admonition header is emitted, because
        # a lifted title comes out of the body's first line and changes both the
        # header text and the alert's type.
        body: list[str] = []
        while i < len(lines) and lines[i][len(indent) :].startswith(">"):
            text = lines[i][len(indent) + 1 :]
            if text.startswith(" "):
                text = text[1:]
            body.append(text)
            i += 1

        title = _TITLE_LEAD.match(body[0]) if body else None
        if title is not None and not _is_emoji(title.group(1)):
            title = None

        if title is not None:
            rest = title.group(3)
            # The remainder now opens a paragraph under its own heading, so it
            # takes a capital -- but only an ordinary lower-case word.
            # `lfr-tunnel`, `macOS` and `--no-wait` are names whose case is the
            # point and are left exactly as written.
            first = rest.split(" ", 1)[0]
            if re.fullmatch(r"[a-z]+[.,;:]?", first):
                rest = rest[:1].upper() + rest[1:]
            body[0] = rest
            out.append(f'{indent}!!! {_TITLED_KIND} "{title.group(2).strip()}"')
        else:
            out.append(f"{indent}!!! {kind}")

        # A bare ">" is a paragraph break and must stay blank -- four spaces of
        # nothing would close the admonition early.
        out.extend(f"{indent}    {b}" if b.strip() else "" for b in body)
        out.append("")

    return "\n".join(out)
