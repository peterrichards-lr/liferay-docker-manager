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
    """`copyright` is raw HTML and is not Jinja-processed, so it cannot
    interpolate `site_url`. The link is therefore hardcoded, and this is what
    stops it being orphaned by a move to a custom domain.

    It is not hypothetical arithmetic: this is a PROJECT site served under
    `/liferay-docker-manager/`, so the obvious-looking root-relative
    `/about/` resolves to `github.io/about/` and 404s on every page.
    """

    def test_the_footer_link_sits_under_site_url(self):
        # yaml.safe_load chokes on mkdocs' !!python/name: tags, and the two
        # values wanted here are plain scalars, so read them as text.
        text = MKDOCS.read_text(encoding="utf-8")

        site_url = re.search(r"^site_url:\s*(\S+)", text, re.M)
        assert site_url, "mkdocs.yml has no site_url"
        base = site_url.group(1).rstrip("/")

        href = re.search(r'<a href="([^"]+)">about these docs</a>', text)
        assert href, "the About link is gone from the mkdocs copyright footer"

        self.assertTrue(
            href.group(1).startswith(base),
            f"the footer links to {href.group(1)} but the site is published "
            f"at {base}/ -- the disclosure link is orphaned on every page",
        )

    def test_the_footer_link_resolves_to_the_about_page(self):
        text = MKDOCS.read_text(encoding="utf-8")
        href = re.search(r'<a href="([^"]+)">about these docs</a>', text)
        assert href, "the About link is gone from the mkdocs copyright footer"
        self.assertTrue(
            href.group(1).rstrip("/").endswith("/about"),
            f"the footer link {href.group(1)} does not point at the About page",
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
