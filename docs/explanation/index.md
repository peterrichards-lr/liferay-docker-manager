# Explanation

**You want to understand why.** These pages explain LDM's design and the
reasoning behind its defaults. Nothing here is a set of steps to follow — for
that, see the [How-To guides](../how-to/index.md) or
[Reference](../reference/index.md).

## How LDM is put together

- **[Architecture](architecture.md)** — the container topology, volume strategy
  and how the pieces fit.
- **[Remote Node Architecture](remote-node-architecture.md)** — how a command
  finds the right machine, and how target resolution works.

## What LDM gives you out of the box

- **[Key Features](conventions.md)** — the capabilities LDM provides without
  being asked: session isolation, zero-config SSL, snapshots, fail-fast checks.

## The two cascades — which one do you want?

LDM layers two different things, and both are commonly called a "hierarchy".
They are unrelated, and picking the wrong one wastes a reader's time:

- **[Conventions & Configuration](conventions_and_config.md)** — the default
  stack (which database, which search mode, which ports) and how an LDM
  *setting* is resolved, from a command-line flag down to the built-in
  convention. Pick this if your question is about an **LDM flag or default**,
  such as `--db` or `database_mode`.
- **[Properties Cascade & Override Hierarchy](properties.md)** — how
  `portal-ext.properties` files are merged, from the pre-warmed seed up to your
  project's own customisations. Pick this if your question is about a
  **Liferay property**.

## Everything else

- **[Internationalization & Transcoding](i18n.md)** — how non-ASCII project
  names become safe container, volume and host identifiers.
- **[FAQ & Walkthroughs](faq.md)** — common questions, with worked answers.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-09-19* | *Last Reviewed: 2026-09-19*
