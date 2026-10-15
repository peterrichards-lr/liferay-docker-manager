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


if __name__ == "__main__":
    unittest.main()
