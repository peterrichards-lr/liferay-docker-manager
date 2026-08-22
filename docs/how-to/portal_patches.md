# Patching Core Portal JARs

![Added in v2.16.0](https://img.shields.io/badge/Added%20in-v2.16.0-blue)

This guide explains how to apply patched Liferay **core** JARs — the ones in
`/opt/liferay/osgi/portal` — to an LDM project, and how LDM protects you from
the main hazard of doing so: a patch built for one Liferay release silently
masking a core JAR in another.

---

## 1. What this solves

`/opt/liferay/osgi/portal` holds roughly 1,420 core JARs shipped inside the
Liferay image. When Liferay support issues a patched build of one of them, the
usual workaround is to copy it into the running container by hand:

```bash
docker cp com.liferay.account.service.jar myproject:/opt/liferay/osgi/portal/
```

That works, but the patch is:

- **lost on every container recreate** — `ldm run`, `--force-recreate`, an image
  bump, or anything else that replaces the container silently reverts it;
- **invisible** — nothing in the project records that the environment differs
  from stock, so a colleague cloning the workspace gets different behaviour;
- **unversioned** — six months later nobody knows which release it was for.

LDM replaces it with a directory in the project.

## 2. Usage

Drop the patched JAR into `portal-patches/` in your project:

```text
myproject/
├── .ldm.meta
├── deploy/
├── files/
└── portal-patches/
    └── com.liferay.account.service.jar
```

Then start the project as usual:

```bash
ldm run .
```

On the next boot LDM reports:

```text
Applying 1 portal patch(es) from portal-patches/.
✅  Applied 1 portal patch(es) to /opt/liferay/osgi/portal.
```

The patch is re-applied on **every** boot, so it survives container recreates.

> [!NOTE]
> The JAR filename must match the core JAR it replaces. LDM checks the name
> against the image's own `/opt/liferay/osgi/portal/` and refuses to copy a file
> that has no counterpart there, since that usually means a typo or a JAR that
> upstream has renamed or removed.

## 3. The sidecar manifest

The first time LDM sees a patch it writes a manifest beside it:

```json
{
  "jira": "",
  "introduced_in": "2026.q1.12-lts",
  "max_version": null,
  "fail_on_mismatch": false
}
```

> [!IMPORTANT]
> `introduced_in` records the release that was current **when LDM first saw the
> file** — not necessarily the release the JAR was compiled against. If you are
> adding a patch that support built for an earlier release, correct this value
> by hand. Everything in the next section depends on it.

| Field | Purpose |
| --- | --- |
| `jira` | Free-text reference (e.g. `LPD-12345`) so the patch's origin stays discoverable. |
| `introduced_in` | The Liferay release this patch was built against. |
| `max_version` | Optional known-good upper bound. Booting above it always aborts. |
| `fail_on_mismatch` | Escalate this patch's warnings to hard failures. |

Commit both the JAR and its `.json` so the whole team gets the same environment.

> [!NOTE]
> Removing a JAR leaves its manifest behind on purpose. Pulling a patch out
> temporarily to check whether a bug still reproduces is routine, and if the
> manifest went with it, re-adding the JAR would reset `introduced_in` to
> whatever release happened to be current — quietly disarming the check below
> and losing the JIRA reference.

## 4. What happens when the Liferay version changes

This is the point of the manifest. A core JAR built against one release can
break OSGi contracts in another, and the failure is quiet — Liferay boots and
reports itself healthy while one bundle fails to resolve.

When you upgrade the project's `tag`, LDM grades each patch:

| Change | Outcome |
| --- | --- |
| Same release (`2026.q1.12-lts` → `2026.q1.12-lts`) | Applied silently. |
| Patch bump (`2026.q1.12-lts` → `2026.q1.13-lts`) | **Warning** + a CTRL+C pause. |
| Quarter or year change (`2026.q1.x` → `2026.q2.x`) | **Abort.** |
| Legacy update bump (`7.4.13-u108` → `u109`) | **Warning** + pause. |
| Legacy minor change (`7.4.x` → `7.5.x`) | **Abort.** |
| Across schemes (`7.4.13-u108` → `2026.q1.12-lts`) | **Abort.** |
| Either side a rolling tag (`nightly`, `latest`) | **Abort.** |
| Above `max_version` | **Abort.** |

Rolling tags abort because there is no way to tell what they point at today, and
a rolling tag is exactly where a stale core JAR is most likely to be wrong.

The pause is interactive only. Under `-y`/`--non-interactive` (and in CI) LDM
does not block — the warnings in the log are the record instead.

To make stale patches a hard failure everywhere, set `fail_on_mismatch` on the
patch, or export `LDM_FAIL_ON_STALE_PATCHES=1`.

### Overriding

When you have checked the patch is still correct:

```bash
ldm run . --force-portal-patches
```

This downgrades every abort above to a warning and applies the patches anyway.
It is also accepted by `ldm start --force-recreate` and
`ldm restart --force-recreate`.

## 5. How it works

LDM splits the usual `docker compose up -d` into three steps:

```text
docker compose create      # container exists, not yet started
docker cp <jar> ...        # patches copied into /opt/liferay/osgi/portal
docker compose start       # Liferay boots, resolving the patched bundle
```

The seam matters. OSGi resolves bundles at startup, so patching an
already-running container would need a second restart and would briefly run the
unpatched JAR.

Patches are **copied**, not mounted. A directory bind-mount onto
`/opt/liferay/osgi/portal` would hide all ~1,420 core JARs and Liferay would not
boot at all; per-file bind-mounts avoid that but reintroduce the container-UID
ownership problems of LDM-#1255 on Linux and WSL. Copying adds files *into* the
directory, which is why the manual `docker cp` workaround worked in the first
place.

> [!NOTE]
> LDM normalises the copied file's permissions to `644`. `docker cp` otherwise
> preserves the host file's mode, so a patch JAR that happens to be mode `600`
> in your workspace lands unreadable by the `liferay` user inside the container —
> and OSGi then fails to resolve that one bundle while the container still starts
> and reports itself healthy. Your own file is left untouched.

## 6. Limitations

- Only `/opt/liferay/osgi/portal` is covered. Your own modules belong in
  `deploy/` or `osgi/modules/`, which are already bind-mounted for hot reload.
- Only `*.jar` files in `portal-patches/` are considered; anything else is
  ignored, so a `README.md` alongside your patches is safe.
- Because patches are copied into the container's writable layer, `ldm start`
  and `ldm restart` without `--force-recreate` keep them without re-copying —
  the layer survives stop/start.

## 7. Related

- [Liferay Version Upgrades](version_upgrades.md) — what else to check when
  changing the image tag.
- [Client Extensions](client_extensions.md) — for your own code, rather than
  patched core JARs.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-22* | *Last Reviewed: 2026-08-22*
