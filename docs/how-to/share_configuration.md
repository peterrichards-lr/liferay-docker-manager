# Sharing Configuration Across Projects

**You want the same setting in every project** -- an API key in an OSGi
configuration, a System Setting, a portal property -- without re-entering it
in each one. Put the file in `~/.ldm/common/`. LDM copies it into every project
it runs.

This page is the task. The full rules -- which folders are read, the complete
file table, and how a project overrides a shared file -- are in
[Sharing Configuration Across Projects](../reference/configuration.md#sharing-configuration-across-projects-common)
in the configuration reference.

## Before you start: know what cannot be shared

Only settings that live in a **file** can be shared this way:

- **System Settings** (OSGi Configuration Admin) -- yes, as a `.config` file.
- **Portal properties** -- yes, through `portal-ext.properties`.
- **Instance Settings**, and **Control Panel screens with no OSGi
  configuration behind them** -- **no**. They are stored in the database, so
  there is no file to place, and they must be entered in each project by hand.

Check which kind your setting is before you rely on `common/` for it. See
[What can and cannot be shared](../reference/configuration.md#what-can-and-cannot-be-shared).

## 1. Create the folder

```bash
ldm init-common
```

This creates `~/.ldm/common/` and fills in LDM's baseline files (developer
`portal-ext.properties`, `env-blacklist.txt`, and OSGi configurations for
session timeout and search). It never overwrites a file that is already there,
so it is safe to run again to restore a deleted baseline file.

It always writes to `~/.ldm/common/`, wherever you run it. If another `common/`
folder is active there -- one named by `LDM_COMMON_DIR`, or a `common/` folder
in the current directory -- it names that folder and leaves it unchanged.

## 2. Add the file

| To share | put in `~/.ldm/common/` | it lands in each project's |
|---|---|---|
| A System Setting | `<pid>.config` or `<pid>.cfg` | `osgi/configs` |
| A licence or other XML | `*.xml` | `deploy` |
| A Marketplace app | `*.lpkg` | `deploy` |
| Portal properties | `portal-ext.properties` | merged into `files/portal-ext.properties` |

Some names are handled specially rather than copied as-is:

- `portal-log4j-ext.xml` is never copied.
- Files with `elasticsearch` in the name follow the project's search mode. They
  are copied only when the shared search container (`liferay-search-global`)
  exists -- and, if it runs Elasticsearch 7, only the `elasticsearch7` files.
  Otherwise, and in a project that uses its own search sidecar, LDM removes
  the project's copy instead.
- A `-REMOTE` suffix is dropped from the copied file's name.

`portal-ext.properties` is not copied but merged: `~/.ldm/common/` is one layer
of the [properties cascade](../explanation/properties.md), below a workspace
`common/` folder (or `LDM_COMMON_DIR`) and below the project's own edits.

> [!WARNING]
> A credential in a shared `.config` file is stored in plaintext in
> `~/.ldm/common/` **and** in every project's `osgi/configs`. See
> [Credentials are stored in plaintext](../reference/configuration.md#credentials-are-stored-in-plaintext).

## 3. Apply it

The copy is not continuous. It happens when LDM synchronises a project, which
is:

- every `ldm run` -- including the first, which creates the project;
- `ldm config rebuild-properties` and `ldm config reset-properties`;
- `ldm infra migrate-search`.

A project that is already running does not see a new or changed shared file
until one of these runs for it.

```bash
ldm run my-project
ls my-project/osgi/configs
```

## 4. Giving one project a different value

A file LDM did not copy is left alone (other than the Elasticsearch files
above), so a project-local file wins **if it was
there before LDM first copied the shared one**. Once LDM has copied a shared
`.config` or `.cfg`, it overwrites it on every later sync, and an edit made in
the project is lost. Follow
[Overriding a shared file in one project](../reference/configuration.md#overriding-a-shared-file-in-one-project)
to break that link.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-11-19* | *Last Reviewed: 2026-11-19*
