# JVM & Database Tuning Reference

How LDM sizes Liferay's JVM and database connections, how that compares with
[Liferay's published guidance][jvm], and **why it differs where it does**.

Written for LDM-#1448 so the differences are recorded decisions rather than
accidents. Where a difference is deliberate the reason is given; where it is a
defect it links to the issue tracking it.

[jvm]: https://learn.liferay.com/w/dxp/self-hosted-installation-and-upgrades/setting-up-liferay/tuning-your-jvm
[liferay]: https://learn.liferay.com/w/dxp/self-hosted-installation-and-upgrades/setting-up-liferay/tuning-liferay

## What LDM actually produces

`ComposerService.get_default_jvm_args()` sizes from the **smaller** of host RAM
and the Docker daemon's reported memory — the daemon that will run the
container, which on a remote target is not the orchestrating machine (LDM-#1133).

Measured on a 32 GB host:

```text
-Xms4096m -Xmx16384m
-XX:MaxMetaspaceSize=1024m -XX:MetaspaceSize=1024m
-XX:NewSize=5406m -XX:MaxNewSize=5406m
-XX:TieredStopAtLevel=1
```

And under `--lean`, which is also applied automatically when
`GITHUB_ACTIONS=true`:

```text
-Xms1536m -Xmx2048m
-XX:MaxMetaspaceSize=512m -XX:MetaspaceSize=512m
-XX:TieredStopAtLevel=1
```

## JVM: LDM versus Liferay

| Setting | Liferay recommends | LDM | Why |
|---|---|---|---|
| `-Xms` / `-Xmx` | **equal**, "to prevent dynamic adjustments" | min ≈25% of RAM, max ≈50% | **Deliberate.** Liferay's advice targets a dedicated server. LDM runs Liferay, a database and often Elasticsearch on one laptop; reserving half the machine at startup would starve the rest. The cost is some heap resizing, which is cheapest exactly where memory is contended. |
| `-XX:NewSize` / `MaxNewSize` | **half** the heap | **33%** of max heap | Undocumented divergence. Liferay's figure assumes a steady production workload; a dev cycle is dominated by redeploys and OSGi churn rather than short-lived request objects. Not measured either way — see "Open questions". |
| `-XX:MetaspaceSize` / `MaxMetaspaceSize` | 768m, both equal | equal, tiered 384m–1024m by RAM | **Matches the shape.** Both set, both equal, scaled to the machine. |
| `-XX:ReservedCodeCacheSize` | **96m** — "too small a code cache (48m is the default) reduces performance" | unset, except 512m during reindex | **Defect.** See below. |
| `-XX:InitialCodeCacheSize` | 64m | unset | Follows from the above. |
| `-XX:SurvivorRatio` / `TargetSurvivorRatio` / `MaxTenuringThreshold` | 16 / 50 / 15 | unset | Not evaluated. Generation-tuning advice written for CMS on Java 8; its applicability under G1 on Java 21 is unestablished. |
| Garbage collector | G1 on Java 11+; CMS/ParNew deprecated | unset → G1 | **Correct.** Java 21 defaults to G1; setting it explicitly would add nothing. |
| Maximum heap | "avoid more than 32g" | capped at 32g | **Matches.** |
| `--add-opens=jdk.zipfs/...` | required for fragment `.zip` export | present | **Matches** (`composer.py:581`). |

### The code cache is the one that matters

The JVM's default `ReservedCodeCacheSize` is **not constant** — it depends on a
flag LDM sets itself. Measured on `eclipse-temurin:21`:

```text
java                          ->  240 MB  (ergonomic)
java -XX:TieredStopAtLevel=1  ->   48 MB  (default)
```

LDM adds `-XX:TieredStopAtLevel=1` on **darwin and windows**, and `--lean` sets
it unconditionally. So on LDM's primary developer platforms the code cache is
**48 MB** — precisely the figure Liferay's guidance singles out as harmful.

| Platform | `TieredStopAtLevel=1` | Effective code cache |
|---|---|---|
| macOS / Windows | yes, by default | **48 MB** |
| Linux | no | 240 MB |
| `--lean`, any platform | yes | **48 MB** |

This also explains LDM-422/423: the reindex path removes
`TieredStopAtLevel=1` *and* sets `ReservedCodeCacheSize=512m` to prevent
`VirtualMachineError`. That treated the symptom on one code path; the cause is
that the flag silently drops the cache everywhere it is applied.

Any explicit default should be justified against the **effective** value on the
platform in question, not against Liferay's "48m", which assumes a non-tiered
JVM and is only coincidentally the same number.

## Database connection pool

Liferay uses **HikariCP**. Verified by extracting `portal.properties` from
`liferay/dxp:2026.q1.7-lts`:

```properties
jdbc.default.connectionTimeout=30000
jdbc.default.idleTimeout=600000
jdbc.default.maximumPoolSize=180
jdbc.default.maxLifetime=0
jdbc.default.minimumIdle=10
jdbc.default.registerMbeans=true
```

LDM writes `jdbc.default.maxActive`, `minIdle` and `maxIdle` — **DBCP names**,
which appear nowhere in those 12,085 lines and are therefore ignored. The
`db_max_active` / `db_min_idle` / `db_max_idle` keys are settable and have no
effect; every project runs on Liferay's built-in defaults.

Tracked and being fixed in **LDM-#1454**. Note the fix is not a rename: giving
those values effect for the first time changes the pool from Liferay's 180 to
LDM's 15, so the numbers are being chosen deliberately rather than inherited.

`maxIdle` has no HikariCP equivalent — Hikari has one pool size and governs idle
connections through `idleTimeout`.

## Not exposed at all

From the [Liferay tuning page][liferay], for completeness rather than as a
commitment:

- **Tomcat thread pool** — `maxThreads`, `minSpareThreads` (50, up to 250),
  `maxConnections=16384`, `connectionTimeout`, `URIEncoding=UTF-8`. LDM has no
  `server.xml` handling.
- **JSP engine** — `development=false`, `mappedFile=false` for non-dev use.

## Open questions

1. **`NewSize` at 33% rather than 50%.** Neither figure has been measured
   against an LDM workload. Worth benchmarking before changing.
2. **Survivor-space tuning.** Liferay's values are Java 8 / CMS advice; whether
   they help under G1 on Java 21 is unknown.
3. **Whether `-Xms`/`-Xmx` should converge** for users who *do* run LDM on a
   dedicated machine. That is a good argument for the profiles in LDM-#1449.

## Overriding any of this

Today: `--lean`, or `--jvm-args` — which **replaces** LDM's defaults entirely
rather than adding to them, so changing one value discards the adaptive sizing.
See [Advanced CLI](advanced_cli.md).

Per-setting overrides through the `ldm config` cascade are tracked in
**LDM-#1449**, with the rule that an unset key keeps the adaptive calculation.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-28* | *Last Reviewed: 2026-08-28*
