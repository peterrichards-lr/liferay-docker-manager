# About these docs

Liferay Docker Manager is a community project maintained by Peter Richards. It
is not an official Liferay product and is not covered by Liferay Support.

This documentation is written with the help of AI coding agents, with the
maintainer accountable for what ships. Each page shows when it was last
updated and when it was last reviewed (reviews are carried out by the agents
as part of keeping the docs current). Changes reach this site through pull
requests the maintainer merges.

Found something wrong or out of date?
[Open an issue](https://github.com/peterrichards-lr/liferay-docker-manager/issues/new).

## What those statements are worth

A disclosure is only useful if you can check it. Here is what enforces each
claim above, and — just as importantly — what does not.

| The claim | What enforces it |
|---|---|
| Every page carries a last-updated and last-reviewed date | `check_docs_review.py`, run on every pull request. A page with **no** footer fails the build, not just a stale one. |
| Those dates are never more than **180 days** old | The same check, with `--max-review-days 180` and `--max-gap-days 180`. |
| A date is never moved by automation | The timestamp hook is **append-only**: it adds a footer where none exists and never rewrites an existing date. Bumping it on a real edit is the editing agent's job. |
| Changes ship only through a reviewed pull request | A branch ruleset on `master` requiring a pull request, passing status checks and linear history. Pushing straight to `master` is not possible, for anyone. |

Two statements on this page are **not** enforced by anything, and should be
read as disclosures rather than guarantees:

- **that the documentation is written with AI agents.** There is no check for
  this, and deliberately so — the only way to enforce it would be to fail the
  build when a human writes a page by hand, which would mandate AI involvement
  rather than disclose it.
- **who performs the reviews.** The build can tell that a page was reviewed
  recently. It cannot tell who did the reviewing. That reviews are carried out
  by the agents is a statement of practice, recorded in the project's
  `AGENTS.md`, not something a machine verifies.

Both are claims made *against* the project's interest rather than in its
favour, which is why they are stated plainly and left unenforced. The claims
you might actually rely on are the ones in the table.

## Why the dates matter more than they look

Twice this project has removed a date that implied a review nobody had done.
An earlier version of the timestamp hook rewrote "last updated" on every full
run, so a commit touching one file stamped *reviewed today* across dozens of
untouched pages and hid real staleness. Separately, the man page's version
stamp was advertised as freshness when a release bump had moved it.

Both were corrected, and the rule that came out of them governs this page too:
a date here means someone looked, or it means nothing at all.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-10-15* | *Last Reviewed: 2026-10-15*
