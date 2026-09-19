"""A heading that something links to must slugify the same on GitHub and MkDocs.

LDM-#1832.

## The asymmetry that makes this worth a test

These docs are read on two renderers, and their slugifiers disagree whenever a
heading contains an emoji. For

    ## 🌱 4. Site Initializers Are Deployed After the First Boot

MkDocs drops the emoji and the space with it; GitHub leaves a leading hyphen:

    MkDocs   #4-site-initializers-are-deployed-after-the-first-boot
    GitHub   #-4-site-initializers-are-deployed-after-the-first-boot

A relative `file.md#anchor` link renders on *both*, so it can only be correct on
one at a time. Picking a side is not a fix; removing the emoji is, because then
both agree.

**Only one direction is noisy.** If the link carries the GitHub form, `mkdocs
build` reports the anchor as missing and someone notices -- that is how LDM-#1820
found it, and that fix quietly traded GitHub for the site. If the link carries
the MkDocs form, the build is silent and the GitHub link is broken with nothing
to say so. This test covers the silent direction.

It is deliberately narrow: emoji in headings are used widely here and are
harmless on any heading nothing links to. The rule is only that a heading which
is a **link target** must not contain one.
"""

import pathlib
import re
import unittest

DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs"

_LINK = re.compile(r"\(([A-Za-z0-9_./-]+\.md)#([A-Za-z0-9_-]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(?P<text>.+?)\s*$")

# Ranges covering the emoji actually used in these docs (pictographs, symbols,
# dingbats, transport/misc) plus variation selectors.
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿️‍]")


def _anchored_links():
    for md in DOCS.rglob("*.md"):
        text = md.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.split("\n"), 1):
            for m in _LINK.finditer(line):
                yield md, n, (md.parent / m.group(1)).resolve(), m.group(2)


def _headings(path):
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        m = _HEADING.match(line)
        if m:
            out.append(m.group("text"))
    return out


def _mkdocs_slug(text):
    """Close enough to MkDocs' toc slugifier for matching purposes."""
    s = _EMOJI.sub("", text).strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"[\s_]+", "-", s).strip("-")


class LinkedHeadingsSlugifyTheSameEverywhere(unittest.TestCase):
    def test_no_linked_heading_contains_an_emoji(self):
        offenders = []
        for src, line, target, anchor in _anchored_links():
            for heading in _headings(target):
                if _mkdocs_slug(heading) == anchor and _EMOJI.search(heading):
                    rel = target.relative_to(DOCS.parent)
                    offenders.append(
                        f"{src.relative_to(DOCS.parent)}:{line} -> {rel} '{heading}'"
                    )

        self.assertEqual(
            offenders,
            [],
            "these headings are link targets and contain an emoji, so their "
            "anchor differs between GitHub and MkDocs:\n  " + "\n  ".join(offenders),
        )

    def test_the_scan_finds_the_links_it_is_meant_to_check(self):
        """A guard that matches nothing passes forever. If the link syntax or
        the docs layout changes, fail here rather than go quietly green."""
        found = list(_anchored_links())

        self.assertGreater(len(found), 0, "no anchored relative links found at all")
        self.assertTrue(
            any(t.is_file() for _s, _l, t, _a in found),
            "no anchored link resolved to a real file -- has docs/ moved?",
        )


if __name__ == "__main__":
    unittest.main()
