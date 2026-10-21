# Permissions, Bind Mounts and the Reclaim Helper

LDM occasionally starts a short-lived Alpine container that does nothing but
change the ownership and mode of one of your project's directories. If you have
seen those appear and disappear during `ldm run`, or hit a "Permission denied"
on a project directory, this page explains what they are, why they exist, and
which directories they touch.

## The problem they solve

Most of a project's directories are **bind mounts** — a folder on your machine,
presented directly inside the container. On native Linux a bind mount performs
no UID translation: a file owned by uid 1001 on the host is owned by uid 1001
inside the container, whatever user that container runs as.

The Liferay image's Tomcat/Equinox process runs as **uid 1000**, and so does
the `elasticsearch:8.x` image behind `liferay-search-global`. Your host account
is almost never uid 1000 — it is typically 1001 on Linux CI and 501 on macOS.

So the two sides of the mount are different users, and neither is in the
other's group.

## Why the mode is `777`

The helper sets ownership to the **host** user and then applies a mode. If that
mode were `750`, uid 1000 would land in the "other" class and have no access at
all to a tree it must both read and write. `770` and `775` do not help either,
because the container's uid is not in the host user's group — the "other" class
is the only one both parties land in.

This is not hypothetical. LDM-#599 changed the default from `777` to `750` and
broke native Linux; LDM-#645 ("restore native linux cross-container mapping")
re-widened the affected directories one release later.

On macOS and Windows the point is largely moot, because Docker Desktop's
virtiofs and gRPC-FUSE mounts present files as the host user regardless of mode
— which is exactly why that regression was not noticed before it shipped.

**What this means for you:** the config trees LDM reclaims are world-readable
on your host. On a shared machine, treat a project directory as readable by any
local account.

## What the helper actually does

```bash
docker run --rm \
  --label com.liferay.ldm.managed=true \
  --label com.liferay.ldm.helper=chown \
  -v <directory>:/workspace alpine \
  sh -c "chown -R <host-uid>:<host-gid> /workspace; chmod -R 777 /workspace; stat -c '%a' /workspace"
```

Three things worth noting:

- It is **recursive**. Everything beneath the directory is re-owned and
  re-moded, not just the directory itself.
- It runs **as root inside the container**, which is the whole point: it can
  correct a directory your own account does not own. Nothing running as your
  user can — not `rsync -p`, not `chmod` over SSH.
- It ends with `stat`, because a `chmod` on exFAT, FAT32 or NTFS exits 0 and
  silently changes nothing. Verifying the mode is the difference between "the
  command ran" and "the mode changed" (LDM-#1946).

You can spot strays with:

```bash
docker ps -a --filter label=com.liferay.ldm.helper=chown
```

## Which directories, on which paths

There is no single reclaim. Different commands reclaim different sets, and they
are not the same set.

| path key | pre-boot (`handlers/base.py`) | run pipeline (`pipelines/run.py`) | repair (`ldm rescue`) |
|---|---|---|---|
| `data` | yes | — | — |
| `state` | yes | — | — |
| `logs` | yes | yes | yes |
| `deploy` | yes | yes | yes |
| `files` | yes | yes | yes |
| `configs` | yes | — | — |
| `log4j` | yes | — | — |
| `portal_log4j` | yes | — | — |
| `backups` | yes | — | — |
| `cx` | yes | — | — |
| `routes` | yes | yes | yes |
| `osgi` | — | yes | yes |

`osgi` is the parent of `configs`, `state`, `modules` and `marketplace`.
Reclaiming it recursively covers those children, which is why the run pipeline
names the parent where the pre-boot check names the children.

**Two gates decide whether the run pipeline reclaims at all:**

- against a **remote target**, it always runs, through that node's Docker
  context;
- **locally**, only on Linux, or when you pass `--fix-permissions`. On macOS
  without that flag it does not run, because Docker Desktop's mounts make it
  unnecessary.

### On a remote node, the reclaim also runs before the sync

The run pipeline's reclaim happens **after** the project is synced to the node,
and a container keeps writing once it is up. Anything it creates after that,
such as `routes/default/<project>`, belongs to uid 1000. The SSH user cannot
change or delete it, so on the next run the sync failed. rsync then skips its
`--delete` step for the whole transfer, which leaves files you deleted locally
on the node, where they are mounted back in (LDM-#2266).

So against a remote target LDM now also reclaims, through the node's Docker
daemon:

- **before every sync** (`ldm run`, `ldm start`, and moving a project to
  another target): it reclaims the run pipeline's own list of trees, but only
  those that already exist on the node, and gives them to the **node's** SSH
  user rather than to your local uid;
- **before `ldm rm --delete` removes the node's copy**: it reclaims the whole
  project directory, which is about to be deleted anyway.

If the removal still fails, the warning now says whether the node answered.
"May be unreachable" is reserved for a node that really did not answer.

## The `routes` tree is a special case

`routes/default/<project>` is written by **Liferay**, at runtime, after the
container is healthy — not by LDM before it starts. Liferay creates those
directories under its own umask, and its
`RoutesPortalK8sConfigMapModifier._deleteRoutes` deletes and recreates the
per-project child on each publish.

That has two consequences:

1. A reclaim that runs before boot cannot set the mode of a directory Liferay
   has not created yet.
2. Setting ownership once on an existing directory does not hold, because the
   child is replaced.

LDM sets `UMASK=0022` in the Liferay container (LDM-#1944) so that what Liferay
creates is readable — that is the primary lever, and the reclaim is the backstop.

**If you have a project from before LDM v2.26.0**, its `routes/default` may
still carry the old `0027`-derived mode of `750`. Nothing recreates that
directory, so the umask fix cannot reach it. Symptoms are a remote sync failing
with `Permission denied` on `routes/default`, or a client extension unable to
read its own config. `ldm rescue <project>` repairs it (LDM-#2144).

## Related

- `docs/how-to/multi_node_orchestration.md` — how a project is synced to a
  compute node, and what happens when that sync fails.
- `docs/TROUBLESHOOTING.md` — the *Permission & Mount Issues* section, for
  symptoms rather than mechanism.

---
*Last Updated: 2026-10-20* | *Last Reviewed: 2026-10-20*
