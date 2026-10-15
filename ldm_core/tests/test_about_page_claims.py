"""The public About page may not out-run the checks that back it.

`docs/about.md` tells readers, in the project's own name, that every page
carries a last-updated and last-reviewed date and that neither is ever more
than 180 days old. That is the one claim on the page a reader might actually
rely on, and it is a number written in prose beside a number written in
`.pre-commit-config.yaml`. Nothing but this file stops the two diverging.

The failure mode is specific and silent: someone relaxes `--max-review-days`
to quieten a noisy gate, the page goes on advertising 180, and the docs now
promise a freshness the build no longer enforces. That is the same defect
LDM-#986 and LDM-#1833 each removed -- a date advertising a review nobody
performed -- re-created one level up, in the disclosure rather than the
footer.
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
ABOUT = ROOT / "docs" / "about.md"
README = ROOT / "README.md"
DOCS_HOME = ROOT / "docs" / "README.md"
COPYRIGHT_PARTIAL = ROOT / "overrides" / "partials" / "copyright.html"
PRECOMMIT = ROOT / ".pre-commit-config.yaml"
MKDOCS = ROOT / "mkdocs.yml"


def _review_days_from_hook() -> str:
    """The `--max-review-days` value the docs-review gate actually enforces."""
    config = yaml.safe_load(PRECOMMIT.read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") == "check-docs-review":
                match = re.search(r"--max-review-days[= ](\d+)", hook["entry"])
                assert match, f"no --max-review-days in {hook['entry']!r}"
                return match.group(1)
    raise AssertionError("the check-docs-review hook is gone")


class TheAboutPagePromisesWhatCiEnforces(unittest.TestCase):
    def test_the_review_window_matches_the_hook(self):
        days = _review_days_from_hook()
        self.assertIn(
            days,
            ABOUT.read_text(encoding="utf-8"),
            f"docs/about.md does not mention {days}, but that is what "
            f"--max-review-days enforces. The page states a review window to "
            f"readers; if the gate's value changed, the page has to change "
            f"with it (see the note beside the Documentation Maintenance Rule "
            f"in AGENTS.md).",
        )

    def test_the_page_the_footer_links_to_exists(self):
        self.assertTrue(
            ABOUT.is_file(),
            "mkdocs.yml links every page's footer at /about/, so docs/about.md "
            "must exist or the site-wide disclosure link 404s",
        )


class TheFooterLinkSurvivesADomainChange(unittest.TestCase):
    """The About link is reachable from the footer of every page, at any depth.

    LDM-#2215 changed the MECHANISM, not the claim. It used to be an absolute
    URL hardcoded into `copyright:`, because that string is raw HTML and is
    NOT Jinja-processed -- it could not interpolate `site_url`, and this is a
    PROJECT site served under `/liferay-docker-manager/`, so the
    obvious-looking root-relative `/about/` resolves to `github.io/about/` and
    404s on every page.

    It now lives in `overrides/partials/copyright.html`, where Jinja's `url`
    filter resolves it against the CURRENT page. Measured on a real build:
    `about/` at the site root, `../../../about/` at depth 3, and `./` on the
    About page itself. That cannot drift from `site_url`, because it is
    derived from it rather than repeated alongside it.

    This asserts the partial, not the rendered HTML, because a unit test must
    not need a site build. The two things that can silently break it are the
    filter being dropped and `custom_dir` being removed, so both are checked.
    """

    def test_the_footer_partial_links_to_the_about_page(self):
        self.assertTrue(
            COPYRIGHT_PARTIAL.exists(),
            "overrides/partials/copyright.html is gone -- every page's footer "
            "has lost the site-wide About disclosure",
        )
        self.assertIn(
            "about/",
            COPYRIGHT_PARTIAL.read_text(encoding="utf-8"),
            "the footer partial no longer points at the About page",
        )

    def test_the_footer_link_is_resolved_by_the_url_filter(self):
        """A bare href here is the exact defect the absolute URL worked around.

        `href="/about/"` 404s under the project subpath; `href="about/"` is
        correct at the site root and wrong on every nested page. Only the
        filter is right everywhere.
        """
        text = COPYRIGHT_PARTIAL.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"""\{\{\s*['"]about/['"]\s*\|\s*url\s*\}\}""",
            "the About href is not resolved through Jinja's `url` filter, so "
            "it is correct at only one nesting depth",
        )

    def test_the_overrides_directory_is_still_wired_up(self):
        """The partial is inert unless `custom_dir` points at its parent.

        Removing that line keeps `mkdocs build` green and silently reverts the
        footer to Material's stock partial, with no About link at all -- the
        same shape of silent failure as LDM-#2210's verification tag, which is
        why that one is checked against the BUILT html.
        """
        self.assertRegex(
            MKDOCS.read_text(encoding="utf-8"),
            # re.M, because assertRegex does not apply it and `custom_dir` is
            # one line inside a long file, not the whole of it.
            re.compile(r"^\s*custom_dir:\s*overrides\s*$", re.M),
            "theme.custom_dir is not `overrides`, so the footer partial and "
            "the search-console tags are both ignored",
        )


# The notice is matched on its distinctive TEXT, not on the whole line. The two
# surfaces deliberately differ in form -- the README uses a plain blockquote,
# the docs home a `> [!NOTE]` GitHub alert that mkdocs_hooks/github_alerts.py
# turns into a Material admonition -- and both are correct. Matching the line
# verbatim would force one form on both and break the rendered callout.
_NOTICE = (
    "**Community project** \u2014 not an official Liferay product and not "
    "covered by Liferay Support."
)


def _first_section_heading_index(text: str) -> int:
    """Where the first `##` heading starts, or -1 if the page has none.

    Fenced code is skipped: a `##` inside a block is not a section heading, and
    both of these files open shell examples that contain comments.
    """
    index, fenced = 0, False
    for line in text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            return index
        index += len(line) + 1
    return -1


class TheCommunityNoticeIsAboveTheFold(unittest.TestCase):
    """LDM-#2205: a reader decides whether to adopt LDM on the first screen.

    The Liferay name, the logo and the phrasing can all make this look like an
    official, supported Liferay product, so "not official, not supported" has
    to arrive before that decision rather than in a closing paragraph nobody
    scrolls to.

    Presence alone is not the claim being made here -- the full disclaimer at
    the foot of the README has always been present, and was still invisible to
    anyone deciding in the first ten seconds. Position is the point, which is
    why these tests assert the notice precedes the first section heading.
    """

    def test_the_readme_carries_the_notice(self):
        self.assertIn(_NOTICE, README.read_text(encoding="utf-8"))

    def test_the_docs_home_carries_the_notice(self):
        self.assertIn(_NOTICE, DOCS_HOME.read_text(encoding="utf-8"))

    def test_the_notice_precedes_the_first_section_heading(self):
        for name, path in (("README.md", README), ("docs/README.md", DOCS_HOME)):
            with self.subTest(page=name):
                text = path.read_text(encoding="utf-8")
                at = text.find(_NOTICE)
                self.assertNotEqual(at, -1, f"{name} has no community notice")

                heading = _first_section_heading_index(text)
                self.assertNotEqual(heading, -1, f"{name} has no '##' heading")
                self.assertLess(
                    at,
                    heading,
                    f"the community notice in {name} sits BELOW the first "
                    f"'##' heading, so a reader deciding whether to adopt LDM "
                    f"meets the Liferay branding before the disclaimer. It "
                    f"belongs directly after the opening description.",
                )

    def test_the_notice_links_somewhere_about(self):
        """Each surface links to About, but not to the same URL: the README is
        read on GitHub and needs the absolute docs-site address, while the docs
        home is inside the site and uses a relative path."""
        self.assertIn(
            "https://peterrichards-lr.github.io/liferay-docker-manager/about/",
            README.read_text(encoding="utf-8"),
        )
        self.assertIn("(about.md)", DOCS_HOME.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
