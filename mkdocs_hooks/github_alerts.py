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
        out.append(f"{indent}!!! {_KINDS[match.group('kind')]}")
        i += 1

        # Consume the rest of the blockquote as the admonition body, indented
        # four spaces. A bare ">" is a paragraph break and must stay blank --
        # four spaces of nothing would close the admonition early.
        while i < len(lines) and lines[i][len(indent) :].startswith(">"):
            body = lines[i][len(indent) + 1 :]
            if body.startswith(" "):
                body = body[1:]
            out.append(f"{indent}    {body}" if body.strip() else "")
            i += 1

        out.append("")

    return "\n".join(out)
