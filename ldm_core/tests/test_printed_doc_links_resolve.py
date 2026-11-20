"""Every repo-file URL LDM prints must point at a file that exists.

LDM-#1825.

## What went wrong

Three places printed a link to `docs/INSTALLATION.md`:

    ldm_core/cli.py                   sudo/root failure
    ldm_core/diagnostics/doctor.py    x2, Docker resource alignment

That file was renamed to `docs/tutorials/quick_start.md` (git records the rename
at 69% similarity) and every one of those links has been a 404 since. Two of
them fire from `ldm doctor`, and the third from a permissions failure -- so a
user already having a bad time was handed a dead link.

Nothing caught it. `check-cli-drift` compares the parser against the CLI
reference, and `check-docs-review` checks markdown timestamps; neither looks at
URLs embedded in strings. A rename in `docs/` cannot see the Python files that
point into it.

## It happened again, through the constant (LDM-#2476)

This test only matched literal `https://github.com/...` URLs. Most of
`ldm doctor`'s links are built as `f"{GITHUB_DOCS_URL}/INSTALLATION.md#..."`,
which the pattern never saw, so some thirty of them -- every `INSTALLATION.md`,
`SECURITY.md` and `docs/README.md#<command>` tip -- stayed 404s, and several more
pointed at anchors that no longer existed. Links built from `GITHUB_DOCS_URL`
and `GITHUB_REPO_URL` are now resolved too, and anchors are checked.

## Anchors are checked against GitHub's slugifier, not MkDocs'

These are `github.com/.../blob/` URLs, so their fragments are produced by
*GitHub's* slugifier, which differs from MkDocs' for any heading containing an
emoji or an ampersand: GitHub keeps the hyphen an emoji's trailing space
becomes (`## 💾 Disk Space` is `#-disk-space`) and does not collapse the double
hyphen ` & ` leaves (`dns--subdomain`). Asserting against the built MkDocs ids
would demand "fixes" that break the links on GitHub, which is where they
actually point -- so `_github_slugs` below follows GitHub's rules.
"""

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

# github.com/<owner>/<repo>/blob/<ref>/<path>[#anchor]
_BLOB_URL = re.compile(
    r"https://github\.com/[^/\s]+/[^/\s]+/blob/[^/\s]+/"
    r"(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]+)"
)

# f"{GITHUB_DOCS_URL}/<path>[#anchor]" and f"{GITHUB_REPO_URL}/blob/master/<path>"
_CONST_URL = re.compile(
    r"\{(?:(?P<const>GITHUB_DOCS_URL)|GITHUB_REPO_URL\}/blob/[^/\s]+)\}?/"
    r"(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*)"
    r"(?:#(?P<anchor>[A-Za-z0-9_-]+))?"
)
_BLOB_ANCHOR = re.compile(_BLOB_URL.pattern + r"(?:#(?P<anchor>[A-Za-z0-9_-]+))?")

_SEARCH_ROOTS = ["ldm_core"]
_SKIP_DIRS = {"tests", "__pycache__"}


def _printed_links():
    """Yields (source_file, lineno, repo_path) for every blob URL in the code."""
    for root in _SEARCH_ROOTS:
        for py in (REPO / root).rglob("*.py"):
            if _SKIP_DIRS & set(py.relative_to(REPO).parts):
                continue
            for n, line in enumerate(
                py.read_text(encoding="utf-8", errors="replace").split("\n"), 1
            ):
                for m in _BLOB_URL.finditer(line):
                    yield py.relative_to(REPO), n, m.group("path")


def _constant_links():
    """Yields (source_file, lineno, repo_path, anchor) for every link built from
    GITHUB_DOCS_URL / GITHUB_REPO_URL, plus every literal blob URL's anchor."""
    for root in _SEARCH_ROOTS:
        for py in (REPO / root).rglob("*.py"):
            if _SKIP_DIRS & set(py.relative_to(REPO).parts):
                continue
            if py.name == "constants.py":  # where the constants are defined
                continue
            for n, line in enumerate(
                py.read_text(encoding="utf-8", errors="replace").split("\n"), 1
            ):
                for m in _CONST_URL.finditer(line):
                    base = "docs/" if m.group("const") == "GITHUB_DOCS_URL" else ""
                    yield py.relative_to(REPO), n, base + m.group("path"), m["anchor"]
                for m in _BLOB_ANCHOR.finditer(line):
                    yield py.relative_to(REPO), n, m.group("path"), m["anchor"]


def _github_slugs(path):
    """Heading anchors as GitHub renders them for a markdown file."""
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    fenced = False
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        m = None if fenced else re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(1))  # [text](url)
        slug = re.sub(r"[^\w\s-]", "", text.strip().lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        slugs.add(slug if count == 0 else f"{slug}-{count}")
    return slugs


class EveryPrintedRepoLinkResolves(unittest.TestCase):
    def test_no_printed_link_points_at_a_missing_file(self):
        broken = [
            f"{src}:{line} -> {path}"
            for src, line, path in _printed_links()
            if not (REPO / path).is_file()
        ]

        self.assertEqual(
            broken,
            [],
            "these URLs are printed to users but the file no longer exists:\n  "
            + "\n  ".join(broken),
        )

    def test_every_constant_built_link_and_anchor_resolves(self):
        broken = []
        for src, line, path, anchor in _constant_links():
            target = REPO / path
            if not target.is_file():
                broken.append(f"{src}:{line} -> {path} (no such file)")
            elif anchor and anchor not in _github_slugs(target):
                broken.append(f"{src}:{line} -> {path}#{anchor} (no such heading)")

        self.assertEqual(
            broken,
            [],
            "these links are printed to users but do not resolve on GitHub:\n  "
            + "\n  ".join(broken),
        )

    def test_the_constant_scan_finds_doctors_links(self):
        found = [link for link in _constant_links() if link[0].name == "doctor.py"]

        self.assertGreater(len(found), 30, found)
        self.assertTrue(any(anchor for *_, anchor in found), found)

    def test_github_slugs_follow_githubs_rules(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            md = pathlib.Path(tmp) / "x.md"
            md.write_text(
                "# Top\n"
                "## 💾 Disk Space Issues (Elasticsearch Flood Stage)\n"
                "## 🌐 DNS & Subdomain Configuration\n"
                "## `run` (alias: `up`)\n"
                '### **Windows/WSL: mkcert SSL "Not Secure" in Chrome**\n'
                "```bash\n# not a heading\n```\n"
                "## Top\n"
            )
            self.assertEqual(
                _github_slugs(md),
                {
                    "top",
                    "-disk-space-issues-elasticsearch-flood-stage",
                    "-dns--subdomain-configuration",
                    "run-alias-up",
                    "windowswsl-mkcert-ssl-not-secure-in-chrome",
                    "top-1",
                },
            )

    def test_the_scan_actually_finds_links(self):
        """A guard that silently matches nothing would pass forever. If the URL
        shape changes, this fails rather than the suite going quietly green."""
        found = list(_printed_links())

        self.assertGreater(
            len(found),
            0,
            "the blob-URL pattern matched nothing -- has the URL shape changed?",
        )


if __name__ == "__main__":
    unittest.main()
