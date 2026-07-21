# PR Merge & Testing Strategy (Target: v2.11.4-pre.3)

To avoid merge conflicts across the CLI routing and ensure safe binary testing without polluting the official GitHub Releases, follow this sequence exactly:

1. **Merge the CI PR first (PR #30):**
   This enables GitHub Actions to compile binaries as artifacts for Pull Requests instead of requiring a `v*` release tag.
2. **Trigger the Binaries:**
   Re-run the pending/failed CI checks on the open PRs (Ngrok, OSGi, Dashboard). Download the `.zip` artifacts from the PR "Checks" tab to distribute to the QA team for testing.
3. **Merge the Minor PRs:**
   Merge the Tag Resolution (#25) and External DB Docs (#26) PRs into `master`.
4. **Merge Ngrok (#27):**
   Test the downloaded Ngrok artifact. If it works, merge into `master`.
5. **Rebase & Merge OSGi (#28):**
   Click "Update branch" on GitHub (or locally merge `master` into the branch) to resolve the `cli.py` and `composer.py` conflicts caused by the Ngrok flag additions. Test the new artifact, then merge.
6. **Rebase & Merge Dashboard (#29):**
   Update this branch from `master`, resolve the final CLI conflicts, test the dashboard artifact, and merge.

Once all features are successfully merged into `master`, you can cut a single, unified `v2.11.4-pre.3` tag that consolidates all of these features for wider community testing.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-07-21* | *Last Reviewed: 2026-07-02*
