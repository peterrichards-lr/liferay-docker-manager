"""LDM-#2215: the docs build and its skip job must cover every pull request.

`docs.yml` runs on a paths filter so an unrelated change does not rebuild the
site. That makes it unusable as a required check on its own: GitHub leaves a
check that never ran PENDING, and the pull request cannot merge. `docs-skip.yml`
reports the SAME job name on the inverse path set so the check is always
present and always truthful.

The arrangement only holds while the two lists are exact complements. Add a
path to one and forget the other and you get one of two silent failures:

* **present in both** -- two workflows claim the same check on the same PR,
  and GitHub keeps whichever finished last. A real docs failure can be
  overwritten by the skip job reporting success.
* **present in neither** -- a documentation change that never builds, which is
  the state this issue found the repository in.

Both are invisible in review: the diff looks like a list of strings.
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
BUILD = ROOT / ".github" / "workflows" / "docs.yml"
SKIP = ROOT / ".github" / "workflows" / "docs-skip.yml"


def _on(path: Path) -> dict:
    # PyYAML resolves a bare `on:` key to the boolean True, because YAML 1.1
    # says so. Every GitHub workflow is affected; this is not a typo.
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded.get(True) or loaded["on"]


class TheDocsCheckCoversEveryPullRequest(unittest.TestCase):
    def test_the_two_path_lists_are_exact_complements(self):
        build = set(_on(BUILD)["pull_request"]["paths"])
        skip = set(_on(SKIP)["pull_request"]["paths-ignore"])
        self.assertEqual(
            build,
            skip,
            "docs.yml `paths` and docs-skip.yml `paths-ignore` have drifted. "
            f"only in docs.yml: {sorted(build - skip)}; "
            f"only in docs-skip.yml: {sorted(skip - build)}",
        )

    def test_both_workflows_report_the_same_check_name(self):
        """The check is identified by workflow name + job name, so BOTH match."""
        bw = yaml.safe_load(BUILD.read_text(encoding="utf-8"))
        sw = yaml.safe_load(SKIP.read_text(encoding="utf-8"))
        self.assertEqual(bw["name"], sw["name"], "workflow names differ")
        self.assertIn("build", bw["jobs"])
        self.assertIn("build", sw["jobs"])

    def test_a_push_rebuilds_for_everything_a_pull_request_builds_for(self):
        """Otherwise a change is verified on the PR and never deployed.

        The asymmetry is the dangerous direction: the PR goes green, the merge
        lands, and the live site keeps serving the previous HTML -- the exact
        shape of LDM-#2210's `overrides/**` omission.
        """
        on = _on(BUILD)
        pr = set(on["pull_request"]["paths"])
        push = set(on["push"]["paths"])
        self.assertEqual(
            set(),
            pr - push,
            f"built on a PR but never deployed on merge: {sorted(pr - push)}",
        )

    def test_the_deploy_job_cannot_run_from_a_pull_request(self):
        """`pull_request` plus an unguarded deploy publishes every PR."""
        deploy = yaml.safe_load(BUILD.read_text(encoding="utf-8"))["jobs"]["deploy"]
        guard = deploy.get("if", "")
        self.assertIn("github.event_name == 'push'", guard)
        self.assertIn("refs/heads/master", guard)

    def test_the_build_is_strict(self):
        """MkDocs warns about a broken link and emits the page anyway."""
        self.assertIn("mkdocs build --strict", BUILD.read_text(encoding="utf-8"))
