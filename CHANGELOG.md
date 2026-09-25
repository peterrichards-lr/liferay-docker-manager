# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.26.0-pre.3] - 2026-09-25

### Fixed

- **A client extension can finally read the config tree Liferay writes** (LDM-#1944). This is the release that closes the bug an external team reported, and the cause was not the one it looked like. **A bind mount resolves its source inode once, at container-create time.** LDM mounted each config tree at its own leaf -- `routes/default/<projectName>` -- and Liferay deletes and recreates that directory after the mount exists. The container kept the orphaned inode and read an empty directory for the rest of its life, while the live one was populated beside it. Measured on the reporting deployment: the extension held inode `2013444` while the host directory was `2013445`, holding 38 files Liferay had written without error. The decisive evidence was the control in the same capture -- `routes/default/dxp/`, mounted into the *same container at the same moment*, reading correctly, because Liferay does not recreate that one.
  There is now **one** mount, at `routes/default`, with both trees addressed as subdirectories through the variables the extension already reads: `LIFERAY_ROUTES_DXP=/etc/liferay/lxc/routes/dxp` and `LIFERAY_ROUTES_CLIENT_EXTENSION=/etc/liferay/lxc/routes/<projectName>`. The leaf is then resolved **inside** the container on every read, so it follows whatever the live directory is. This is why the Liferay container never had this bug: it has always mounted above the level that gets replaced.
  **Two consequences worth knowing.** LDM now *overrides* the routes paths an extension declares in its own `LCP.json`, which reverses the behaviour introduced in v2.26.0-pre.1 -- LDM owns the mount layout and the extension cannot know it, so honouring the declaration would point it at a path nothing is mounted at. And every client extension in a project can now read every other extension's OAuth2 credentials, and the DXP tree. That isolation is **deliberately traded**: LDM is for demos, testbeds and experimentation, not production. Narrowing the mount to restore it would reintroduce this bug.
  Verified end to end on native Linux against a running Liferay, which is what LDM-#1944 was held open for.

- **BEHAVIOUR CHANGE: client extensions no longer publish a host port** (LDM-#1973, LDM-#1969). A client extension is reached through Traefik, by subdomain -- `<ext-id>.<host_name>` -- which resolves over the Docker network to the port its `LCP.json` declares. The host port mapping was never on that access path, and SSL projects had never had one at all. It was also actively harmful: the published port was computed by a rewrite (`8080` became `28080`, `80`/`443` gained 10000) applied **downstream** of the per-project uniqueness resolution and never deduplicated itself, so two extensions could be assigned distinct ports and still collide -- `Bind for 0.0.0.0:28080 failed: port is already allocated`. Fixing the allocation in isolation did not help, because the exclusion set is computed before the rewrite.
  Worse, the relocation was silent, and something downstream believed it: the OAuth URL written **into the extension's own configuration, inside the deployed artifact**, used the pre-rewrite number, so it pointed at nothing or at a different extension's container. That URL is now the subdomain with no port, matching what SSL has always emitted. **If you were reaching an extension on a published host port, use its subdomain instead** -- `ldm fix-hosts` adds the entry.

- **`ldm config` no longer prints stored credentials** (LDM-#1970). It printed every stored value verbatim, which put a live API key and an auth token into a user's terminal and therefore into that session's scrollback and any capture of it. Values are now masked by **key name**, because neither redaction point could work: the listing prints `key = value` with spaces while `UI.redact` requires `KEY=value`, and `ldm config <key>` prints the value alone with no key beside it for anything downstream to key on. Asking for a credential by name now **refuses and exits non-zero** rather than printing nothing, so `TOKEN=$(ldm config gemini_api_key)` fails at that line instead of binding an empty string. `--reveal` is the opt-in. If you have run `ldm config` in a shared or recorded session, rotate what it printed.

- **Fatal errors now show you the fix** (LDM-#1971). Five failure paths put the error at one verbosity tier and the remedy at a lower one, so the remedy printed nothing unless `--info` or `--verbose` was already set -- and re-running to recover it was impossible, because the process had exited. The `sudo` guard runs on **every** invocation, so this was the first thing a new Linux user met: a refusal, with `sudo usermod -aG docker $USER` suppressed. The Colima path printed the literal words "To fix this, run:" followed by nothing at all.

- **`scripts/setup_pre_commit.sh` installs the git hook** (LDM-#1950). It tested for `pre-commit` anywhere on `PATH`, so a global install satisfied the check while the project venv had none, and the hook installation was nested inside that same test -- meaning it never ran once dependencies were present, which is exactly when it is needed. `.git/hooks/pre-commit` carries an absolute interpreter path and is shared by every worktree, so a deleted checkout left the hook pointing at a missing interpreter and commits stopped being gated with nothing reporting it. Contributors should re-run the script.

## [v2.26.0-pre.2] - 2026-09-25

### Fixed

- **LDM mounted a routes directory Liferay never writes to** (LDM-#1944). Liferay names the per-extension config tree from the extension's `projectName`, taken from the `ext.lxc.liferay.com/projectName` ConfigMap label; LDM derived it from the `id` in `LCP.json`. Those are different strings for essentially every extension -- the id drops the hyphens the directory keeps, and all 16 samples in `ldm-cx-samples` differ. Traced through Liferay's own `BaseConfigurationFactory` and `RoutesPortalK8sConfigMapModifier` rather than inferred. **It did not fail loudly**, which is why it survived four diagnoses: the scaffold creates whatever the compose declares, so the extension read a real, empty, *readable* directory while Liferay filled a different one beside it.

- **A client extension with no `LCP.json` id is refused, not silently dropped** (LDM-#1962). Such an artifact previously crashed compose generation: `ldm run` exited **0** having written no `docker-compose.yml` at all, and every client extension in the project vanished without a word. Every real client extension carries an `LCP.json` beside its `client-extension.yaml`, so this shape is not a supported one -- LDM now refuses it explicitly and moves the zip into a `rejected/` directory rather than deploying something it cannot identify. A site initializer is exempt, matching the existing `is_service` rule.

- **The pre-boot scaffold is re-wired** (LDM-#1917). `migrate_layout` creates every essential directory before the stack starts, specifically to deny Docker the chance to create a bind-mount source itself as an empty root-owned directory. It had been silently unwired by a refactor months earlier, which is why `marketplace` had no creator at all.

### Changed

- **Liferay runs with `UMASK=0022`** (LDM-#1944, LDM-#1946). `catalina.sh` defaults Tomcat's umask to `0027` unless the variable is already set -- `750` on directories, `640` on files -- so every config tree Liferay published was readable only by uid 1000, while a client extension runs as whatever uid its own image declares. Confirmed against the reporting deployment's logs: `java.io.FileNotFoundException` from `RoutesPortalK8sConfigMapModifier` on three occasions before this change, and none after.

- **Service-targeted variables are filtered on the name the container receives** (LDM-#1954). The blacklist was tested against the host spelling, which carries a service prefix the container never sees, so any pattern anchored at the start of the name never matched -- `SERVICEID_LIFERAY_ROUTES_CLIENT_EXTENSION` sailed past a `LIFERAY_ROUTES_*` rule and arrived as `LIFERAY_ROUTES_CLIENT_EXTENSION`, repointing a tree LDM itself mounts.

- **The WSL certificate path in the troubleshooting guide is correct, and asserted** (LDM-#1958).

## [v2.26.0-pre.1] - 2026-09-23

### Added

- **The verification suite can now see things it never could** (LDM-#1952, LDM-#1955, LDM-#1966). Several of its checks passed without being able to fail: the routes pairing compared two names that were the same string, the filesystem probe never ran at all because `stat -f` is BSD's format flag but GNU's `--file-system`, and the client-extension fixture had never been shaped like a real extension -- no `LCP.json`, no OAuth application, nothing at `/opt/liferay/routes` to shadow. Each of those gaps had been hiding a live defect, including the one an external team reported as LDM-#1911. The suite now observes **both sides** of the routes boundary in one run -- the directory Liferay publishes against the one LDM mounts -- asserts the modes that actually land on disk, and announces a check it could not evaluate rather than reporting it as a pass. Every product fix in this release was found this way rather than by reading code.
- **Client extensions receive their own config tree** (LDM-#1928). A client extension declares **two** config trees in its `LCP.json` and reads both -- `LIFERAY_ROUTES_DXP` and `LIFERAY_ROUTES_CLIENT_EXTENSION`. LDM forwarded the second variable straight out of the extension's own `LCP.json` and **mounted nothing at it**, so every extension was pointed at a path containing nothing. That tree, `routes/default/<ext-id>`, is where Liferay publishes per-extension config -- including the OAuth2 credentials it generates when it registers the extension's application, which `lxcConfig.oauthApplication(erc)` resolves from there. Only the DXP half had ever been mounted. The container-side targets are **derived** from the extension's own declaration rather than assumed, so a non-standard path is honoured and the constants are only a fallback.
  **The credential path is fixed, but not yet observed end to end.** Liferay names that directory from the extension's `projectName`, while LDM derived it from the `id` in `LCP.json` -- different strings for essentially every extension, since the id drops the hyphens the directory keeps (`ecopulseheadlessauth` against `ecopulse-headless-auth`; 16 of 16 samples differ). LDM mounted a directory Liferay never wrote to, and because the scaffold creates whatever the compose declares, the extension read a real, empty, *readable* directory rather than hitting an error. That is corrected (LDM-#1944): the mount now follows `projectName`, traced through Liferay's own `BaseConfigurationFactory` and `RoutesPortalK8sConfigMapModifier`. It has been verified against Liferay's source and LDM's generated compose, **not** against a running extension reading its credentials, so LDM-#1944 remains open pending that.
- **Custom services join the shared config space** (LDM-#1928). A `custom_containers` service now receives the routes subtree, the `LIFERAY_LXC_DXP_MAIN_DOMAIN`/`_DOMAINS` variables and `extra_hosts`, the same as a client extension. It previously received none of them, so it could neither read anything Liferay published nor resolve the project host back to the Docker host. Everything LDM adds is **additive**: a volume, variable or `depends_on` declared by the user always wins, because a custom container is an arbitrary third-party image and mounting over its own files is exactly LDM-#1911 done to an image LDM knows nothing about.
- **The routes tree is scaffolded before it is mounted** (LDM-#1928). Docker creates a missing bind-mount source itself, as an empty root-owned directory. Subtrees are now created from the compose that was just generated, so a mount and its host directory cannot drift apart. Skipped for remote targets deliberately -- creating a remote node's directories on the local machine would be worse than not creating them -- and recorded as an open question in `docs/explanation/remote-node-architecture.md` rather than left silent.
- **E2E and architecture coverage for all of the above** (LDM-#1927, LDM-#1934, LDM-#1935). The verification suite now builds the client-extension image and **runs** it, asserting the extension's own code at `/opt/liferay/routes` survives LDM's mounts -- the previous fixture was `FROM alpine`, so nothing could be shadowed and the check would have passed against the very mount that broke a live container. It also asserts both config trees, the `service_healthy` gate specifically, the scaffolded directories and the custom-service case, which was never exercised at all. The mechanism itself is now recorded in `.agents/skills/ldm-architecture/SKILL.md`, with a test that fails if the code and that document disagree.
- **A Docker host that runs out of disk names the remedy** (LDM-#1906). A failure caused by a full Docker host now points at `ldm system prune --images` instead of surfacing the raw error, which read as a corrupt download or a broken image.

### Changed

- **Liferay runs with `UMASK=0022`** (LDM-#1944). Liferay runs under Tomcat, and `catalina.sh` sets `UMASK="0027"` unless the variable is already present -- `750` on directories and `640` on files. Every config tree Liferay published was therefore readable only by uid 1000, while a client extension runs as whatever uid its own image declares. LDM now sets `UMASK=0022` on the Liferay service, so those trees are `755`/`644`. This is **tighter than the previous behaviour, not looser**: `routes` is already reclaimed at `chmod 777` before boot, so the contents were world-readable on the host either way. Note this is a hardening in its own right and **not** the fix for LDM-#1944 -- the extension was never being refused.

- **BEHAVIOUR CHANGE: client-extension and custom-service containers wait for Liferay to be serving** (LDM-#1928). They now declare `depends_on: {liferay: {condition: service_healthy}}`, resolved against the `liferay/dxp` image's **own** `HEALTHCHECK`, which curls `/c/portal/layout` -- so "serving pages", not merely "process started". They previously started concurrently with a boot that takes minutes and read an empty config tree. **Your extension container will appear later than before.** Deploying the extension's artifact is unchanged and still happens immediately, before Liferay is ready -- that ordering is load-bearing, because the artifact is what causes Liferay to register the extension and create its OAuth profile in the first place.
- **Credential-shaped variables are withheld from containers on every route** (LDM-#1910). Where a variable is genuinely needed, an explicit `!` negation re-admits it and the decision is announced rather than silent.
- **`ldm system upgrade` labels the installed version, not the search channel** (LDM-#1912). A stable release was reported as a pre-release when discovered via `--beta`.

### Removed

- **The ROI claim, and `ldm system roi`** (LDM-#1924). `LDM first-boot seeding saved you 14m 0s of manual work!` was a hardcoded constant passed to a function that formats whatever integer it is handed -- `UI.format_duration(840)` is exactly `14m 0s`, which is why the figure was suspiciously round. It was printed identically whether seeding took twenty seconds from a warm cache or ten minutes from a fresh ~1GB download, while the real elapsed time was measured and discarded. No derivation for the number exists anywhere in the repository's history. Removed outright rather than reworded, following the same decision already taken for the database-restore claim -- which had left `ldm system roi` advertising a contributor whose call site had not existed for months. The `--reset` flag goes with it.

### Fixed

- **Client-extension containers could not use the config-tree mechanism at all** (LDM-#1911). The metadata mount landed on `/opt/liferay/routes`, which is correct for the Liferay container but is the **application's own route handlers** in an extension built on `liferay/node-runner` (`COPY . /opt/liferay`) -- 16 `.cjs` handlers sat there on the deployment that reported it. Mounting over it shadows the app and the container will not start. Reported by an external team consuming LDM, not caught here.
- **Seven behaviours lost in the `stack.py` to `composer.py` refactor** (LDM-#1918), restored with tests that assert *why* each value must be what it is, plus a mechanical guard that fails when a parsed field has no consumer.
- **Service-targeted host environment never reached a client-extension container** (LDM-#1903). The code implementing it worked, but nothing ever called it with a `target_id`, so the branch was unreachable and an extension received only what its own `LCP.json` declared.
- **Two unreachable gating parameters** in `UI.patch` and the disk-space helper (LDM-#1905).
- **Verification reports record which environment produced them** (LDM-#1907, LDM-#1909). A CI container and a developer's workstation reporting the same distro shared one compatibility-matrix row, so whichever synced second silently replaced the first -- a Fedora 44 workstation row at Docker 29.8.1 was destroyed that way and replaced by CI's 28.0.4, removing the only evidence LDM had been exercised on the newer engine. PowerShell 5.1 and 7 on one host collided the same way. Reports now declare a `Run Context:`, and the PowerShell half supplies its own edition, which the CLI cannot see for itself.

## [v2.25.0] - 2026-09-22

### Added

- **`ldm config revert [project]`** (LDM-#1854) returns an existing project's LDM-controlled configuration to the resolved defaults. `ldm run` freezes settings into a project's `meta`, which then overrides the cascade for that project forever, and there was no way to clear them. Five keys are **refused by default**, because the value is not the whole of the decision -- each has already had an effect outside LDM that changing the value back does not reverse: `host_name` is written into Liferay's virtualhost table; `db_type` and `database_mode` point at data living in that engine's volume and container; `search_mode` at indices; `tag` at a database already upgraded, and Liferay does not support downgrades. The refusal prints the reason for each rather than merely saying no. `--force-key <key>` reverts one, `--force` reverts all, and both say plainly that LDM changes the setting only -- it does not migrate data, move containers or rewrite virtualhosts. Scope is deliberately narrow: container names, the project UUID, credentials and run history are not configuration and are untouched. Reverting **writes** the value rather than deleting the key, because `port`, `host_name` and `db_type` are read from `meta` by direct indexing in 5, 6 and 3 places and a missing key would raise `KeyError` rather than fall back; the original value's type is preserved, since a real project carries `"port": 8080` as an int while the convention table stores `'8080'` as a string.
- **`ldm config defaults --reset-all`** (LDM-#1853) returns every customised cascading default to convention in one command, instead of removing 23 keys one at a time or hand-editing `~/.ldmrc`. `--global` targets `/etc/ldmrc`, `-y` skips the confirmation, and exit `5` reports an idempotent no-op -- nothing failed and nothing needed to change. Only keys the defaults system owns are touched: `ldm config set` writes other keys into the root of the same file and clearing the whole file would silently discard them. It applies to **new projects only**, and says so, pointing at `ldm config revert` for one that already exists.
- **E2E coverage for the containers-removed-volumes-intact scenario** (LDM-#1873), in both script halves. It boots a project, writes a marker into its data volume, removes the containers with `docker rm -f`, then asserts `ldm list` reports `Not Created`, `ldm start` refuses naming `ldm run`, and `ldm run` recreates from the surviving volume with the marker intact -- so the no-data-loss claim is proven rather than inferred from the absence of a crash. Verified by four real Docker runs: both halves pass against correct code and both fail with the same message against a deliberately neutered build.
- **`ldm wait --probe-url <url>`** (LDM-#1891) names the readiness URL instead of deriving it from the target. LDM cannot infer the right target in every topology: with SSL on a remote node the certificate is issued for the project's host name while the derived URL dials the node's address, so the probe and the certificate disagree by construction -- for any SSL-enabled remote project, not only the one that reported it. Reaching the stack through an SSH tunnel makes it worse, because the address LDM can see is not the one the client uses. The supplied URL is used verbatim: no rewriting of scheme, host or port, on remote and local targets alike.
- **`ldm deploy <project> <file...>` works against a remote node** (LDM-#1894). It used to refuse outright: copying the artifact into the local project directory would have updated something the remote container never reads, silently, so failing loudly was the right guard -- but the capability behind it was missing, and the only guidance was `ldm run` for a full resync, which restarts the stack. That is a fair escape hatch for a developer and useless to a CI run that has already waited out a boot. **The command is the same whatever the target**: the node comes from the project's own `meta`, so a caller never names it, and the local end state does not depend on where the project runs -- the artifact is placed locally exactly as before, including expanding a client-extension zip for image builds, and a project on a node additionally has it shipped there. No container operation and no restart are involved, because `osgi/modules` and `osgi/client-extensions` are bind-mounts from the project directory: a file landing in the node's copy is immediately visible to the running container. The upload is staged and then moved, since Liferay *watches* those directories and a partially transferred artifact is a deploy of a truncated file. Ownership is settled through `docker --context <node> exec`, which removes the `chown` follow-ups callers were doing with bare `docker` -- and which, on a remote target, hit the caller's own daemon where the container does not exist.
- **`ldm wait` has end-to-end coverage for the first time** (LDM-#1893), in both script halves. It had none at all, despite owning the only HTTP probe in the codebase and having had its URL resolution changed twice (LDM-#1223, LDM-#1891) on the strength of unit tests alone. Two assertions: `ldm wait` succeeds against a ready project, and `--probe-url` pointed where nothing listens fails **naming that URL**. The second is deliberately arranged to fail closed -- had the override been ignored, the probe would have reached the real project and succeeded, and the assertion would have passed while proving nothing. Verified against a real booted Liferay in both directions, including with the override neutered.

### Changed

- **Nothing in the binary from LDM-#1886**, which aligned the `ruff` pre-commit `rev` with `requirements-dev.txt`. Three versions were in play -- `0.16.8` in CI, `0.16.1` in the local venv, `0.15.6` in the hook -- because Dependabot updates one and not the other. Nothing was failing; both versions present agreed on the tree. That was luck rather than design, and on a formatter the failure mode is a file that flips between what the hook writes and what a developer's own `ruff format` writes.
- Nothing else. `-pre.2` carries `-pre.1`'s change set plus LDM-#1891, so a `-pre.1` verification result remains meaningful for everything except the readiness probe.
- **Breaking, deliberately: a plain binary in `~/.ldm/bin` no longer resolves** (LDM-#1883). That location is outside the whitelist and LDM cannot vouch for what is in it. A symlink from there to the approved binary keeps working, and recovery for anything else is one line, which the refusal names: set `LDM_LFR_TUNNEL_BIN`, or move the binary. The test asserting *"existing `~/.ldm/bin` setups must not break"* was rewritten rather than deleted -- that guarantee was real and is being withdrawn on purpose.
- Verification runs are roughly 30 seconds longer on every platform, the cost of LDM-#1893's coverage. `ldm wait` is what CI pipelines block on and it was completely unguarded, so the trade was taken deliberately.

### Fixed

- **A remote node that was still booting was reported as stopped or re-addressed** (LDM-#1863). `sshd` accepts a TCP connection on port 22 seconds before it will authenticate, so an EC2 instance roughly 15 seconds into a cold boot is reachable and not yet usable -- and the first LDM command against it failed terminally. The caller had waited; their wait was the obvious one, polling until the port accepts, which proves connectivity rather than readiness. Reported from `liferay-ai-commerce-accelerator`. Two halves: each failure reason now carries its own advice, so refused credentials no longer suggests the IP has changed and a host-key failure names the `ssh-keygen -R` entry to purge; and a **transport** failure is retried while the node may still be coming up.
- **A project whose containers were removed reported `Stopped`** (LDM-#1870, LDM-#1872), which told the user it merely needed starting -- so they ran `ldm start`, which runs `docker compose start` and can only start containers that already exist, surfacing Docker's own wording about a service they never named. Both `ldm list` and `ldm status` now report `Not Created`, and `ldm start` refuses up front naming `ldm run`. The volumes survive, so `ldm run` recovers the project with no data loss. Found by a user on v2.24.0; it was never a regression, both code paths were byte-identical since v2.23.0.
- **LDM executed `lfr-tunnel` by a non-whitelisted path** (LDM-#1871). InfoSec whitelist the binary path itself, and LDM-#1576 stopped LDM *writing* to the legacy `~/.ldm/bin` location -- but not executing from it, and execution is what endpoint protection inspects. A legacy install that is a symlink to the approved binary was still invoked by the symlink's own path. Every candidate is now resolved to its real path before being probed or run, so existing setups keep working and the approved binary is launched as itself.
- **A missing `gh` was reported as an unreachable network, silently skipping three release-verification assertions** (LDM-#1869). `verify_published_release.py` handled a non-zero exit from `gh` but not the binary being absent, so it died with an uncaught `FileNotFoundError`, produced no JSON, and the test read that as "could not reach release" and skipped. The checker now reports a missing tool as a finding -- valid JSON, non-zero exit, because a checker that cannot run is a failure and not a no-op -- and the test distinguishes a genuine network skip from a checker that produced nothing at all. Observed directly: the skip count moved from 1 to 4 when `gh` was deleted from the machine, and back to 1 when it returned.
- **The compatibility sync published the verifying developer's username** (LDM-#1861). `anonymize_content` replaced `Path.home()`, which is the home of the machine *running* the sync -- and a report is almost never generated there, since Windows, WSL2 and Linux reports are collected from other machines and synced from one. Local reports were redacted and every foreign one kept its username: 90 of 159 archived reports, in a public repository. Redaction is now by pattern across `C:\Users\`, `/home/` and `/Users/`, with `/home/runner` kept deliberately because it is CI's generic home and records provenance. A guard refuses to write a report that still names someone, using a deliberately broader pattern than the substitution -- a guard sharing that pattern could never fire, which the first version did. This stops the next leak; it does not undo the last, since history retains every past occurrence.
- **The properties-override setup assertions disagreed across platforms** (LDM-#1860). Measured across four inputs, bash `grep -q` and PowerShell `-match` diverged on two: unescaped dots in a properties key are regex wildcards in both, and `-match` is case-insensitive by default, so Windows accepted a wrong-case key that bash rejected. Both halves now match literally and agree on all four.
- **LDM no longer executes an unapproved binary in order to read its version** (LDM-#1883). `_resolve_existing_binary` decided whether a candidate `lfr-tunnel` was usable by running it, so the first thing LDM did with a binary it had not installed and could not vouch for was launch it -- from a location its own comments call outside the EDR whitelist. The observed response removes the binary and the surrounding toolchain with it, the package manager included. LDM-#1871 made sure an approved binary is invoked by its approved path; this closes the prior question, and the hole it left, where a legacy symlink pointing somewhere arbitrary still ended in executing a non-whitelisted path.

## [v2.25.0-pre.3] - 2026-09-21

### Added

- **`ldm deploy <project> <file...>` works against a remote node** (LDM-#1894). It used to refuse outright: copying the artifact into the local project directory would have updated something the remote container never reads, silently, so failing loudly was the right guard -- but the capability behind it was missing, and the only guidance was `ldm run` for a full resync, which restarts the stack. That is a fair escape hatch for a developer and useless to a CI run that has already waited out a boot. **The command is the same whatever the target**: the node comes from the project's own `meta`, so a caller never names it, and the local end state does not depend on where the project runs -- the artifact is placed locally exactly as before, including expanding a client-extension zip for image builds, and a project on a node additionally has it shipped there. No container operation and no restart are involved, because `osgi/modules` and `osgi/client-extensions` are bind-mounts from the project directory: a file landing in the node's copy is immediately visible to the running container. The upload is staged and then moved, since Liferay *watches* those directories and a partially transferred artifact is a deploy of a truncated file. Ownership is settled through `docker --context <node> exec`, which removes the `chown` follow-ups callers were doing with bare `docker` -- and which, on a remote target, hit the caller's own daemon where the container does not exist.
- **`ldm wait` has end-to-end coverage for the first time** (LDM-#1893), in both script halves. It had none at all, despite owning the only HTTP probe in the codebase and having had its URL resolution changed twice (LDM-#1223, LDM-#1891) on the strength of unit tests alone. Two assertions: `ldm wait` succeeds against a ready project, and `--probe-url` pointed where nothing listens fails **naming that URL**. The second is deliberately arranged to fail closed -- had the override been ignored, the probe would have reached the real project and succeeded, and the assertion would have passed while proving nothing. Verified against a real booted Liferay in both directions, including with the override neutered.

### Fixed

- **LDM no longer executes an unapproved binary in order to read its version** (LDM-#1883). `_resolve_existing_binary` decided whether a candidate `lfr-tunnel` was usable by running it, so the first thing LDM did with a binary it had not installed and could not vouch for was launch it -- from a location its own comments call outside the EDR whitelist. The observed response removes the binary and the surrounding toolchain with it, the package manager included. LDM-#1871 made sure an approved binary is invoked by its approved path; this closes the prior question, and the hole it left, where a legacy symlink pointing somewhere arbitrary still ended in executing a non-whitelisted path.

### Changed

- **Breaking, deliberately: a plain binary in `~/.ldm/bin` no longer resolves** (LDM-#1883). That location is outside the whitelist and LDM cannot vouch for what is in it. A symlink from there to the approved binary keeps working, and recovery for anything else is one line, which the refusal names: set `LDM_LFR_TUNNEL_BIN`, or move the binary. The test asserting *"existing `~/.ldm/bin` setups must not break"* was rewritten rather than deleted -- that guarantee was real and is being withdrawn on purpose.
- Verification runs are roughly 30 seconds longer on every platform, the cost of LDM-#1893's coverage. `ldm wait` is what CI pipelines block on and it was completely unguarded, so the trade was taken deliberately.

## [v2.25.0-pre.2] - 2026-09-21

### Added

- **`ldm wait --probe-url <url>`** (LDM-#1891) names the readiness URL instead of deriving it from the target. LDM cannot infer the right target in every topology: with SSL on a remote node the certificate is issued for the project's host name while the derived URL dials the node's address, so the probe and the certificate disagree by construction -- for any SSL-enabled remote project, not only the one that reported it. Reaching the stack through an SSH tunnel makes it worse, because the address LDM can see is not the one the client uses. The supplied URL is used verbatim: no rewriting of scheme, host or port, on remote and local targets alike.

  **The default is unchanged**, and was deliberately not edited. LDM-#1223 made the probe follow the target node rather than dialling `127.0.0.1` on the client, and that remains correct; the existing derivation moved into a helper untouched, with the override as a branch around it. Verified by diffing the moved block against its predecessor -- 30 lines, byte-identical apart from the assignment becoming a return -- and pinned by tests asserting a remote node still probes its own address, a local target still probes by host name, and the scheme still follows the SSL decision.

  One part of the request was not implemented, and deliberately so: it asked that the switch apply equally to `ldm run`'s implicit wait. There is nothing there to override. The codebase contains exactly one HTTP probe, in `ldm wait`; `ldm run` watches container health and Liferay's own logs and issues no HTTP at all. Honouring the switch there would mean giving `ldm run` an HTTP probe it has never had, which changes its behaviour for everyone rather than adding an opt-in.

### Changed

- Nothing else. `-pre.2` carries `-pre.1`'s change set plus LDM-#1891, so a `-pre.1` verification result remains meaningful for everything except the readiness probe.

## [v2.25.0-pre.1] - 2026-09-21

### Added

- **`ldm config revert [project]`** (LDM-#1854) returns an existing project's LDM-controlled configuration to the resolved defaults. `ldm run` freezes settings into a project's `meta`, which then overrides the cascade for that project forever, and there was no way to clear them. Five keys are **refused by default**, because the value is not the whole of the decision -- each has already had an effect outside LDM that changing the value back does not reverse: `host_name` is written into Liferay's virtualhost table; `db_type` and `database_mode` point at data living in that engine's volume and container; `search_mode` at indices; `tag` at a database already upgraded, and Liferay does not support downgrades. The refusal prints the reason for each rather than merely saying no. `--force-key <key>` reverts one, `--force` reverts all, and both say plainly that LDM changes the setting only -- it does not migrate data, move containers or rewrite virtualhosts. Scope is deliberately narrow: container names, the project UUID, credentials and run history are not configuration and are untouched. Reverting **writes** the value rather than deleting the key, because `port`, `host_name` and `db_type` are read from `meta` by direct indexing in 5, 6 and 3 places and a missing key would raise `KeyError` rather than fall back; the original value's type is preserved, since a real project carries `"port": 8080` as an int while the convention table stores `'8080'` as a string.
- **`ldm config defaults --reset-all`** (LDM-#1853) returns every customised cascading default to convention in one command, instead of removing 23 keys one at a time or hand-editing `~/.ldmrc`. `--global` targets `/etc/ldmrc`, `-y` skips the confirmation, and exit `5` reports an idempotent no-op -- nothing failed and nothing needed to change. Only keys the defaults system owns are touched: `ldm config set` writes other keys into the root of the same file and clearing the whole file would silently discard them. It applies to **new projects only**, and says so, pointing at `ldm config revert` for one that already exists.
- **E2E coverage for the containers-removed-volumes-intact scenario** (LDM-#1873), in both script halves. It boots a project, writes a marker into its data volume, removes the containers with `docker rm -f`, then asserts `ldm list` reports `Not Created`, `ldm start` refuses naming `ldm run`, and `ldm run` recreates from the surviving volume with the marker intact -- so the no-data-loss claim is proven rather than inferred from the absence of a crash. Verified by four real Docker runs: both halves pass against correct code and both fail with the same message against a deliberately neutered build.

### Fixed

- **A remote node that was still booting was reported as stopped or re-addressed** (LDM-#1863). `sshd` accepts a TCP connection on port 22 seconds before it will authenticate, so an EC2 instance roughly 15 seconds into a cold boot is reachable and not yet usable -- and the first LDM command against it failed terminally. The caller had waited; their wait was the obvious one, polling until the port accepts, which proves connectivity rather than readiness. Reported from `liferay-ai-commerce-accelerator`. Two halves: each failure reason now carries its own advice, so refused credentials no longer suggests the IP has changed and a host-key failure names the `ssh-keygen -R` entry to purge; and a **transport** failure is retried while the node may still be coming up.
- **A project whose containers were removed reported `Stopped`** (LDM-#1870, LDM-#1872), which told the user it merely needed starting -- so they ran `ldm start`, which runs `docker compose start` and can only start containers that already exist, surfacing Docker's own wording about a service they never named. Both `ldm list` and `ldm status` now report `Not Created`, and `ldm start` refuses up front naming `ldm run`. The volumes survive, so `ldm run` recovers the project with no data loss. Found by a user on v2.24.0; it was never a regression, both code paths were byte-identical since v2.23.0.
- **LDM executed `lfr-tunnel` by a non-whitelisted path** (LDM-#1871). InfoSec whitelist the binary path itself, and LDM-#1576 stopped LDM *writing* to the legacy `~/.ldm/bin` location -- but not executing from it, and execution is what endpoint protection inspects. A legacy install that is a symlink to the approved binary was still invoked by the symlink's own path. Every candidate is now resolved to its real path before being probed or run, so existing setups keep working and the approved binary is launched as itself.
- **A missing `gh` was reported as an unreachable network, silently skipping three release-verification assertions** (LDM-#1869). `verify_published_release.py` handled a non-zero exit from `gh` but not the binary being absent, so it died with an uncaught `FileNotFoundError`, produced no JSON, and the test read that as "could not reach release" and skipped. The checker now reports a missing tool as a finding -- valid JSON, non-zero exit, because a checker that cannot run is a failure and not a no-op -- and the test distinguishes a genuine network skip from a checker that produced nothing at all. Observed directly: the skip count moved from 1 to 4 when `gh` was deleted from the machine, and back to 1 when it returned.
- **The compatibility sync published the verifying developer's username** (LDM-#1861). `anonymize_content` replaced `Path.home()`, which is the home of the machine *running* the sync -- and a report is almost never generated there, since Windows, WSL2 and Linux reports are collected from other machines and synced from one. Local reports were redacted and every foreign one kept its username: 90 of 159 archived reports, in a public repository. Redaction is now by pattern across `C:\Users\`, `/home/` and `/Users/`, with `/home/runner` kept deliberately because it is CI's generic home and records provenance. A guard refuses to write a report that still names someone, using a deliberately broader pattern than the substitution -- a guard sharing that pattern could never fire, which the first version did. This stops the next leak; it does not undo the last, since history retains every past occurrence.
- **The properties-override setup assertions disagreed across platforms** (LDM-#1860). Measured across four inputs, bash `grep -q` and PowerShell `-match` diverged on two: unescaped dots in a properties key are regex wildcards in both, and `-match` is case-insensitive by default, so Windows accepted a wrong-case key that bash rejected. Both halves now match literally and agree on all four.

### Changed

- **Nothing in the binary from LDM-#1886**, which aligned the `ruff` pre-commit `rev` with `requirements-dev.txt`. Three versions were in play -- `0.16.8` in CI, `0.16.1` in the local venv, `0.15.6` in the hook -- because Dependabot updates one and not the other. Nothing was failing; both versions present agreed on the tree. That was luck rather than design, and on a formatter the failure mode is a file that flips between what the hook writes and what a developer's own `ruff format` writes.

## [v2.24.0] - 2026-09-20

### Added

- **`ldm run --container <name>`** (LDM-#1836) names a project's containers explicitly instead of deriving the name from its directory. The flag had been accepted and read by nothing since it was added. The value is sanitised on the way in, which is the substance of the change: the compose builder sanitises before stamping ownership labels while `prune` matches the stored value raw, so an unsanitised value would be labelled one way and matched another -- and `prune` would offer a **live** project's containers as orphans. Refused on a project that already exists, where changing it would rename every container and orphan its volumes; passing the same name again is accepted.
- **The man page is under the documentation review gate** (LDM-#1833). Its `.TH` date is rewritten on every version bump, so it advertised a current date purely because a release ran -- the last real content change was four stamp-only commits earlier. A freshness signal that lies is worse than none. It now carries a roff-comment review stamp that a bump deliberately does not touch, and `check_docs_review.py` holds it to the same 180-day limit as every Markdown document.

### Changed

- **The CLI `--help` text is corrected and completed** (LDM-#1834). `--quiet` claimed to "suppress all standard output" while it gates only informational output -- warnings and errors still print -- and that wrong copy was the only description a user could reach. Whole namespaces rendered as a bare list of choices with no descriptions: `cloud` 0 of 7 subcommands described, `infra` 1 of 7, `config` 7 of 14, `system` 9 of 15, including `completion` and `man`, the two commands the documentation tells users to run. Verified text-only by fingerprinting all 3,546 parser actions before and after: byte-identical.
- **The documentation site renders what it was written to render** (LDM-#1814). 57 GitHub-style alerts were emitted as plain blockquotes with `[!IMPORTANT]` visible in the prose -- disproportionately the warnings -- and all three architecture diagrams rendered as code blocks, because `mkdocs.yml` declared no `markdown_extensions` at all. Every section now has a landing page, Troubleshooting is reachable from the top level rather than filed under project governance, and four documents that contradicted `ldm_core/defaults.py` about LDM's own defaults are corrected.
- Nothing that ships in the binary. `-pre.2` carries the same `ldm_core` code as `-pre.1` apart from the version stamp; only the verification scripts and one test file changed, so a `-pre.1` verification result remains meaningful for everything the suite reached before it stopped.
- Nothing that ships in the binary. `-pre.3` carries the same `ldm_core` code as `-pre.2` and `-pre.1` apart from the version stamp; only the two verification scripts changed. This is the third pre-release in the cycle burnt by a defect in the verification suite rather than in the product, and all three share one shape: an assertion that had never been exercised against the conditions it would actually meet.

### Fixed

- **`--force` was permanently ON for 54 commands** (LDM-#1835). `-f/--force` is declared once on a shared parent parser, and argparse's `parents=` copies action *references*, so every subparser held the same object. A `conflict_handler="resolve"` child that redeclared `-f` or `--force` emptied its `option_strings` -- and argparse treats an action with no option strings as a **positional**, which with `nargs=0` always matches and fires. Measured on the real runtime path: `ldm run demo`, `ldm status` and `ldm down demo` all set `force=True` with no flag passed. Every guard reading `args.force` was disabled -- the non-interactive downgrade abort, the verification-failure abort, critical-change warnings, and the check on operating in a git repo with no LDM metadata. Each fires only on an unusual path, so the bypass was invisible until the moment a guard should have stopped something. Worse: with `--force` not a valid option string, argparse fell back to prefix matching and resolved `ldm system upgrade --force` to `--force-downgrade`, performing a forced downgrade. Verified by differential test against v2.23.0 -- 33 invocations, 27 differ, 26 of them `force: True -> False`.
- **`ldm db stop` reported a successful stop as a failure** (LDM-#1805). `docker stop` is synchronous and succeeds, but `docker ps` can still list the container for a moment afterwards, and the LDM-#1547 guard read that back with no tolerance for the gap. It cost at least seven CI failures across distros *and* workflows, every one passing on a re-run with no code change. The readback is now a bounded poll: a correct stop pays one `docker ps`, and only a stop that never settles pays the ceiling -- which was already a failure path, so the guard keeps its teeth and still exits 3.
- **`ldm guide` printed a precedence hierarchy that was wrong on every level, and recommended a command LDM refuses** (LDM-#1824). It named `.ldm/config.json`, which is not a cascade level anywhere in the codebase, omitted the project `meta` file entirely, and told the user to run `ldm config set database_mode` -- which `handlers/config.py` rejects for any cascading default. It is step 1 of the front-door tutorial, so it was the first thing a new user saw. It now prints the five levels the resolver implements, CLI down to convention, and recommends `ldm defaults`.
- **Three docs links printed on failure paths were 404s** (LDM-#1825). `ldm doctor` (twice) and the sudo/root failure path pointed at `docs/INSTALLATION.md`, renamed long ago. A user already having a bad time was handed a dead link.
- **The man page documented a value the parser rejects and a no-op as functional** (LDM-#1833). `--jvm-tiered-stop-at-level` was documented taking a `LEVEL` with `1` recommended, while the parser accepts only `true`/`false`; `--no-jvm-verify` was described as skipping a start-up check that does not exist. `ldm db query`'s synopsis could not be typed, and `import` documented a path that has hard-errored since v2.15.16.
- **The verification suite could not complete against `-pre.1`** (LDM-#1855). Two assertions added in the previous pre-release were defective, and both were found by running the real published artefact rather than by CI -- one needs Windows, the other needs a terminal.
- **Three tests failed for any developer who had run the verification suite** (LDM-#1856). Running it installs `lfr-tunnel` and exports `LDM_LFR_TUNNEL_BIN`; `_resolve_existing_binary` checks environment variables before the global config and before `shutil.which`, and three tests exercising those later steps never cleared the first one. The result was that `scripts/agent_push.sh` refused every push on that machine, failing on tests unrelated to the change being pushed. The rule this breaks was already written down -- a test must not depend on the developer's real state -- but the environment was not on the list of hazards beside `~/.ldm`, `~/.ldmrc` and Docker contexts, and it is invisible in CI where the variable is never set.
- **A passing configuration reset was reported as broken, whenever the run's random port happened to contain the digits `123`** (LDM-#1858). The Properties Override Reset assertion paired an anchored positive with an *unanchored* negative -- a bare `grep -q "123"` across the whole of `portal-ext.properties`. The generated file carries `jdbc.default.url=jdbc:postgresql://<project>-db:5432/lportal`, and the project is named `ldm-smoke-test-<TEST_PORT>`, so the JDBC line matched on port 61233 and stopped a macOS verification run on `-pre.2` one check from the end. Three things made it look like a product defect and all three were misleading: `ldm config reset-properties` printed its own success immediately above the failure, Windows passed because its ports did not contain those digits, and the same platform had passed this check in the recorded v2.23.0 run. Confirmed not a regression -- `handlers/config.py` and `handlers/base.py` are untouched since v2.23.0 and the assertion was byte-identical. The properties cascade works; the check could not see it. Both greps are now fixed-string (`grep -qF`, `.Contains()`) and carry the full key, which also tightens the positive: the unescaped dots in `test.override.prop=456` were regex wildcards in both `grep` and PowerShell's `-match`. Verified in both directions in both halves -- a correct reset at port 61233 now passes, a reset that genuinely did not happen still fails.

## [v2.24.0-pre.3] - 2026-09-20

### Fixed

- **A passing configuration reset was reported as broken, whenever the run's random port happened to contain the digits `123`** (LDM-#1858). The Properties Override Reset assertion paired an anchored positive with an *unanchored* negative -- a bare `grep -q "123"` across the whole of `portal-ext.properties`. The generated file carries `jdbc.default.url=jdbc:postgresql://<project>-db:5432/lportal`, and the project is named `ldm-smoke-test-<TEST_PORT>`, so the JDBC line matched on port 61233 and stopped a macOS verification run on `-pre.2` one check from the end. Three things made it look like a product defect and all three were misleading: `ldm config reset-properties` printed its own success immediately above the failure, Windows passed because its ports did not contain those digits, and the same platform had passed this check in the recorded v2.23.0 run. Confirmed not a regression -- `handlers/config.py` and `handlers/base.py` are untouched since v2.23.0 and the assertion was byte-identical. The properties cascade works; the check could not see it. Both greps are now fixed-string (`grep -qF`, `.Contains()`) and carry the full key, which also tightens the positive: the unescaped dots in `test.override.prop=456` were regex wildcards in both `grep` and PowerShell's `-match`. Verified in both directions in both halves -- a correct reset at port 61233 now passes, a reset that genuinely did not happen still fails.

### Changed

- Nothing that ships in the binary. `-pre.3` carries the same `ldm_core` code as `-pre.2` and `-pre.1` apart from the version stamp; only the two verification scripts changed. This is the third pre-release in the cycle burnt by a defect in the verification suite rather than in the product, and all three share one shape: an assertion that had never been exercised against the conditions it would actually meet.

## [v2.24.0-pre.2] - 2026-09-20

### Fixed

- **The verification suite could not complete against `-pre.1`** (LDM-#1855). Two assertions added in the previous pre-release were defective, and both were found by running the real published artefact rather than by CI -- one needs Windows, the other needs a terminal.
  - The `--force` check reported a defect that was not there. It matched the *wrapped continuation* of `--jvm-tiered-stop-at-level`'s description -- indented 24 spaces and beginning with a capital `Force` -- rather than an actual positional entry. This was a **parity defect, not a Windows bug**: the bash half matched case-sensitively and passed, while PowerShell's `-match` is case-insensitive by default and failed, so the two halves returned opposite verdicts on identical, correct output. Both now anchor on argparse's exact two-space option indent, and the PowerShell half uses `-cmatch` so they cannot silently diverge again.
  - The `ldm guide` check hung on macOS. `ldm guide` resolves `non_interactive` as `args.non_interactive or not sys.stdin.isatty()`, so on a real terminal it enters an interactive menu loop and blocks. Measured under a pseudo-terminal the problem is worse than a hang: without `-y` the precedence block is not printed at all, so even where it does not block the assertion fails for the wrong reason. Both halves now pass `-y`.
- **Three tests failed for any developer who had run the verification suite** (LDM-#1856). Running it installs `lfr-tunnel` and exports `LDM_LFR_TUNNEL_BIN`; `_resolve_existing_binary` checks environment variables before the global config and before `shutil.which`, and three tests exercising those later steps never cleared the first one. The result was that `scripts/agent_push.sh` refused every push on that machine, failing on tests unrelated to the change being pushed. The rule this breaks was already written down -- a test must not depend on the developer's real state -- but the environment was not on the list of hazards beside `~/.ldm`, `~/.ldmrc` and Docker contexts, and it is invisible in CI where the variable is never set.

### Changed

- Nothing that ships in the binary. `-pre.2` carries the same `ldm_core` code as `-pre.1` apart from the version stamp; only the verification scripts and one test file changed, so a `-pre.1` verification result remains meaningful for everything the suite reached before it stopped.

## [v2.24.0-pre.1] - 2026-09-19

### Fixed

- **`--force` was permanently ON for 54 commands** (LDM-#1835). `-f/--force` is declared once on a shared parent parser, and argparse's `parents=` copies action *references*, so every subparser held the same object. A `conflict_handler="resolve"` child that redeclared `-f` or `--force` emptied its `option_strings` -- and argparse treats an action with no option strings as a **positional**, which with `nargs=0` always matches and fires. Measured on the real runtime path: `ldm run demo`, `ldm status` and `ldm down demo` all set `force=True` with no flag passed. Every guard reading `args.force` was disabled -- the non-interactive downgrade abort, the verification-failure abort, critical-change warnings, and the check on operating in a git repo with no LDM metadata. Each fires only on an unusual path, so the bypass was invisible until the moment a guard should have stopped something. Worse: with `--force` not a valid option string, argparse fell back to prefix matching and resolved `ldm system upgrade --force` to `--force-downgrade`, performing a forced downgrade. Verified by differential test against v2.23.0 -- 33 invocations, 27 differ, 26 of them `force: True -> False`.
- **`ldm db stop` reported a successful stop as a failure** (LDM-#1805). `docker stop` is synchronous and succeeds, but `docker ps` can still list the container for a moment afterwards, and the LDM-#1547 guard read that back with no tolerance for the gap. It cost at least seven CI failures across distros *and* workflows, every one passing on a re-run with no code change. The readback is now a bounded poll: a correct stop pays one `docker ps`, and only a stop that never settles pays the ceiling -- which was already a failure path, so the guard keeps its teeth and still exits 3.
- **`ldm guide` printed a precedence hierarchy that was wrong on every level, and recommended a command LDM refuses** (LDM-#1824). It named `.ldm/config.json`, which is not a cascade level anywhere in the codebase, omitted the project `meta` file entirely, and told the user to run `ldm config set database_mode` -- which `handlers/config.py` rejects for any cascading default. It is step 1 of the front-door tutorial, so it was the first thing a new user saw. It now prints the five levels the resolver implements, CLI down to convention, and recommends `ldm defaults`.
- **Three docs links printed on failure paths were 404s** (LDM-#1825). `ldm doctor` (twice) and the sudo/root failure path pointed at `docs/INSTALLATION.md`, renamed long ago. A user already having a bad time was handed a dead link.
- **The man page documented a value the parser rejects and a no-op as functional** (LDM-#1833). `--jvm-tiered-stop-at-level` was documented taking a `LEVEL` with `1` recommended, while the parser accepts only `true`/`false`; `--no-jvm-verify` was described as skipping a start-up check that does not exist. `ldm db query`'s synopsis could not be typed, and `import` documented a path that has hard-errored since v2.15.16.

### Added

- **`ldm run --container <name>`** (LDM-#1836) names a project's containers explicitly instead of deriving the name from its directory. The flag had been accepted and read by nothing since it was added. The value is sanitised on the way in, which is the substance of the change: the compose builder sanitises before stamping ownership labels while `prune` matches the stored value raw, so an unsanitised value would be labelled one way and matched another -- and `prune` would offer a **live** project's containers as orphans. Refused on a project that already exists, where changing it would rename every container and orphan its volumes; passing the same name again is accepted.
- **The man page is under the documentation review gate** (LDM-#1833). Its `.TH` date is rewritten on every version bump, so it advertised a current date purely because a release ran -- the last real content change was four stamp-only commits earlier. A freshness signal that lies is worse than none. It now carries a roff-comment review stamp that a bump deliberately does not touch, and `check_docs_review.py` holds it to the same 180-day limit as every Markdown document.

### Changed

- **The CLI `--help` text is corrected and completed** (LDM-#1834). `--quiet` claimed to "suppress all standard output" while it gates only informational output -- warnings and errors still print -- and that wrong copy was the only description a user could reach. Whole namespaces rendered as a bare list of choices with no descriptions: `cloud` 0 of 7 subcommands described, `infra` 1 of 7, `config` 7 of 14, `system` 9 of 15, including `completion` and `man`, the two commands the documentation tells users to run. Verified text-only by fingerprinting all 3,546 parser actions before and after: byte-identical.
- **The documentation site renders what it was written to render** (LDM-#1814). 57 GitHub-style alerts were emitted as plain blockquotes with `[!IMPORTANT]` visible in the prose -- disproportionately the warnings -- and all three architecture diagrams rendered as code blocks, because `mkdocs.yml` declared no `markdown_extensions` at all. Every section now has a landing page, Troubleshooting is reachable from the top level rather than filed under project governance, and four documents that contradicted `ldm_core/defaults.py` about LDM's own defaults are corrected.

## [v2.23.0] - 2026-09-19

### Added

- **`ldm target add --mac-address`**: pin the Liferay container to a node's NIC address so a MAC-bound licence activates. Measured on `aws-1` with `liferay/dxp:2026.q3.0`: the container recreated identically except for the MAC took validation from "MAC address matching failed" to passed, and the portal from the DXP Activation page to the Sign In form. Without a pin the container takes a bridge address and the failure is silent -- healthy container, `License registered` in the log, and a portal that will not sign in. Configured per node and never inferred: a node shows `ens5` beside `docker0` and `br-*`, all plausible and one licensed (LDM-#1752).
- **The pin is verified, not assumed**: after start LDM reads the container's MAC back and refuses (exit 3) when it is not the configured one, naming both values and stating that a recreate is required. Writing `mac_address` into the compose file is a request -- a toolchain that ignores the form, a container created before the value changed, and a wrongly chosen interface all present identically, and one check covers all three (LDM-#1752).
- **The release contracts are checked *before* the tag exists** (LDM-#1758). `release.py` runs the CHANGELOG ratchet and the architectural-contract tests before creating a tag, and aborts saying the number is still available. Both tags burnt in the previous cycle — `v2.22.0-pre.4` and `v2.23.0-pre.1` — failed preconditions that already had fast tests; they simply were not run at the moment the decision was made. Tags are immutable, so the cost of finding out afterwards is a version number that can never be reclaimed.
- **The published release is verified, not just the build** (LDM-#1768). `scripts/verify_published_release.py --tag X` downloads a release and asserts what a *user* finds: every asset present, every checksum matching, and the manifest's claims true. Validated against real published history rather than fixtures — it reports 4 problems on `v2.22.0-pre.7`, 5 on `-pre.5`, and passes `v2.23.0-pre.2`. Four defects in the previous cycle were invisible to green CI and surfaced only by downloading the release.
- **The Promotion Delta Gate is a check, not a documented rule** (LDM-#1765). `--promote` diffs `ldm_core/` against the last `v*-pre.*` tag and refuses when stable would ship code no pre-release ever verified. `constants.py`, `resources/ldm.1` and `/tests/` are excluded — version stamps and files that do not ship. `--allow-promotion-delta` is the deliberate opt-out. Hit promoting `v2.22.0`, where four commits merged after the verification and shipped in stable.
- **`agent_push.sh` refuses to commit onto `master` or `release/*`** unless `LDM_ALLOW_PROTECTED_BRANCH=1` (LDM-#1764). Four branch mistakes were made in a single day.
- **A package pin now records a claim, not just a version** (LDM-#1791). `tag` in an `.ldmp` manifest, and `liferay.workspace.product` in a workspace, are facts about how a package was *built*, and every consumer reads them as statements about what it *supports*. Those are different, and nothing distinguished them — so a consumer deviating from the pin could not tell whether they were doing something the publisher had tested and rejected, something nobody had tried, or something fine. A package can now declare a **ceiling** ("tested above this and it failed"), which is enforced with an explicit, recorded override, because packages get fixed and a consumer may know more than the publisher did. Everything else defaults to **no claim**, announced visibly when the tag is resolved rather than left silent — a field defaulting to anything reassuring would recreate LDM-#1782, which is silence being read as success. Worth stating plainly, because the whole design follows from it: **there is no such thing as a stale pin.** A package legitimately pins an older line because a later one was tried and failed, or because nobody has had time to qualify one. The pin is the statement of what is known to work.
- **`ldm doctor` detects a MAC pin an older client dropped** (LDM-#1789). Compose runs client-side, so a node configured with a MAC gets no pin at all if the binary that rendered the compose file predates the feature — silently, and with the node's configuration looking correct throughout.
- **`ldm start --verify-mac` / `ldm restart --verify-mac`** (LDM-#1804): opts into checking the pinned MAC against the node's own interfaces. It is **off by default on these two commands** because that check is an SSH round trip, and it was measured turning a 2.9s restart into 4.3s -- 48%, unconditionally, on a hot command. That cost was never a considered trade: LDM-#1798 added the check and only its *failure* path was ever exercised, because a mismatch refuses before the cross-check runs, so the success path went unmeasured until it was timed against a real node. It remains **on by default for `ldm run`**, where 1.4s is noise against a multi-minute boot and the container is being created. Little is lost by the new default -- a wrong pin is already caught once at `ldm target add` (LDM-#1780), where a typo is cheapest to fix and costs one round trip rather than one per restart; `--verify-mac` covers the narrower case of a node whose interfaces changed after it was registered.
- **The E2E suite now asserts that the MAC pin's refusal actually fires** (LDM-#1808). That branch had **never executed in the feature's life**: LDM-#1798 established it was unreachable by any supported command, because `mac_address` is part of the compose service spec, so `ldm run` recreates the container whenever it changes and the two values always agree. Exit `3` was in the documented contract and in `-pre.4`'s CHANGELOG, and had never once fired. The suite now pins a MAC, boots, changes only the *configured* value, runs `ldm restart`, and requires exit `3` naming both addresses. It needs **no remote node and no licence**, and takes about 40 seconds: a target named anything other than `local` and pointed at `127.0.0.1` is checked (`configured_mac` skips only the reserved name) while compose drives the local daemon. Observed to FAIL against `v2.23.0-pre.5`, where `restart` performs no check and exits 0, before being committed.

### Fixed

- **`RELEASE_ANNOUNCEMENTS` carries the `v2.23` entry**, which is what `-pre.1` was spent on: `test_release_announcements_contract` fails the first cut of any new minor until that entry exists, and that path is reachable only through `--bump preminor`/`--premajor` -- `--bump beta` reuses a minor that already has one, so the contract had never fired since it was written (LDM-#1756).
- **`--mac-address` now reaches the handler.** The flag was declared and parsed, and then never passed on, so `cmd_target_add` stored `""`. `target add` reported success and `~/.ldmrc` carried the key with an empty value — a node configured with `-pre.2` has no pin at all. Found by the maintainer running the released binary; CI was green throughout, because both tests for the feature asserted the flag was *declared* and that the docs named it, and neither followed the value to storage. The regression test now drives `_build_command_map` and invokes the real lambda, rather than calling `cmd_target_add` with the arguments the dispatch was supposed to supply (LDM-#1759).
- **A wrong `--mac-address` was pinned faithfully and failed silently** (LDM-#1780). There are two ways the MAC pin fails and only one was guarded. `_verify_pinned_mac` compares the *container's* MAC against the *configured* one, so a typo satisfies it perfectly: LDM pins the wrong address, both values agree, the check passes, and Liferay then refuses the licence — leaving the operator with the original LDM-#1752 symptom (healthy container, `License registered` in the log, Activation page instead of Sign In) about twenty minutes after making the mistake. `target add` now reads the node's `/sys/class/net/*/address` over the SSH round trip LDM already makes and **warns** when the configured value matches none of them, naming each interface so the right one can be picked. It warns rather than refuses, because Liferay validates the container's MAC against the licence and does not care what the host's interfaces are — a licence bound to a MAC that is not a current NIC is legitimate, if unusual. A node that cannot be reached stays silent: unreachable is not wrong. Measured against a real node: a wrong MAC warns and names `docker0` and `ens5`, the host NIC is silent, `docker0` is silent (it *is* an interface), and upper case is silent (normalised). Reported by the AICA team from the `-pre.3` run, where it cost two CI runs.
- **An OOM kill was reported as a health-check timeout** (LDM-#1773). LDM printed *"Timed out waiting for Liferay to become healthy"* after eight minutes while `docker inspect` said `OOMKilled: true` and the log said `Killed  start_liferay.sh`. Those send you to opposite places — one says wait longer or look at the application, the other says this machine does not have enough memory. Neither ending of the wait could report it: the JVM is killed while the entrypoint survives, so the container stays up and never becomes healthy, and `ExitCode` is `0` because the entrypoint then exits cleanly, so nothing in the container's state contradicted the message. Both endings now read `{{.State.OOMKilled}}` from the node the project actually runs on. An unreadable flag stays "could not ask" rather than becoming either claim.
- **An imported package silently overrode the resolved search mode** (LDM-#1773). A project that resolved to shared search rendered a stack running an embedded Elasticsearch *inside* the Liferay container as well, whose sidecar JVM defaults to `-Xmx2g` — enough to be OOM-killed on an 8 GB machine sized for a 3 GB ceiling, which is how both halves of this issue were found. Two routes did it: `custom_env`, which `composer` appends *after* the shared-search block so Compose resolves the duplicate to the later entry; and the packaged `portal-ext.properties`, where `module.framework.properties.*` is the highest-precedence route into that configuration. LDM now strips the search settings it owns from both on import and **says what it removed and where it came from**. Search topology is a property of the consumer's machine, like `jdbc.default.url` and `virtual.hosts.valid.hosts`, which `import` already regenerates. Worth noting for package publishers: you no longer need to sanitise search settings out of a package, and if you set one deliberately the announcement is where you will see it discarded.
- **The Promotion Delta Gate diffed the wrong ref** (LDM-#1777). It runs immediately after the "promotion must be run from a `release/` branch" guard, so `HEAD` is the release branch — while `create_and_push_tag` checks out `master` and tags **that**. A commit merged to `master` and never backported therefore shipped in the stable tag while the gate reported a clean delta, which is the exact failure the gate exists to prevent arriving by another route. It now compares the verified tag against both refs and names which one carries each file, because the remedy differs: a master-only file wants a backport, a branch-only file wants another pre-release. It also reports the commits `master` carries that the release branch does not, on every run — reported rather than refused, because the live example was test-only and a gate that fires on a correct configuration gets disabled.
- **A site-initializer client extension present at first boot never initialised its site** (LDM-#1779). `ldm import` moved every `client-extensions/*.zip` into `osgi/client-extensions/`, which for a site initializer is the wrong moment: its bundle tracker opens before the built-in `welcome`/`cms` initializers have created the Guest site's layouts, and `ServiceContextFactory` needs one — so it threw a `NullPointerException` in `PortalImpl.getCanonicalURL` while the portal went on logging `STARTED`. Nothing surfaced it; the site was simply never initialised. The same artifact dropped into the already-running portal initialises in about 100 ms, so LDM now stages a site-initializer zip and deploys it once the portal is ready. Detection keys on `Liferay-Client-Extension-Site-Initializer` in the built zip's `WEB-INF/liferay-plugin-package.properties` — the header the extender itself tracks — and deliberately not on `client-extension.yaml`, which declares `type: siteInitializer` but is a *source* descriptor that is not present in the built artifact at all. A zip that cannot be read is treated as **not** a site initializer: deferring something LDM failed to identify would change the deployment moment for extensions that are fine where they are. Found while building the LDM-#1745 harness, on a live DXP `2026.q3.0`.
- **A failed boot exited zero, and the run did not say what it had booted** (LDM-#1790). Automation could not distinguish a healthy start from a timeout, and a report of either named neither the LDM version nor the tag it resolved — nor whether that tag came from a flag, a workspace pin or discovery. That last distinction is what made LDM-#1782 expensive to diagnose: green runs had been qualifying one product line while declaring another, and nothing in the output showed it.
- **The MAC pin's refusal could not be reached by any supported command** (LDM-#1798). `_verify_pinned_mac` ran only in the `ldm run` pipeline — which is the one path where a mismatch cannot occur, because `mac_address` is part of the compose *service spec*, so changing the configured value makes compose recreate the container by itself. Measured: a changed pin followed by `ldm run` with no `ldm rm` produced a container created seconds earlier carrying the *new* address. Meanwhile `ldm start` and `ldm restart` — the routes that genuinely leave a stale container — never ran the check at all. So exit `3` was in the documented contract, described in `-pre.4`'s CHANGELOG, and unreachable. The check now lives in `ldm_core/runtime/mac_pin.py` and runs after `start` and `restart` too, and the refusal has now fired on a real node for the first time. Its recovery hint is also caller-specific: from `start`/`restart` a plain `ldm run` recreates the container, so the previous `ldm rm && ldm run` was destructive advice for a problem that does not need it.
- **A wrong-but-applied pin passed silently** (LDM-#1798). The check compared the container's MAC against the *configured* one and never consulted the node, so a MAC belonging to no interface satisfied it perfectly — and Liferay then logged `MAC address matching failed` and served the Activation page, which is the exact LDM-#1752 symptom this check exists to prevent. After a match, the configured value is now checked against the node's interfaces and **warns** when it is on none of them. It warns rather than refuses for LDM-#1780's reason: Liferay validates against the licence, not the host, so a licence bound to a MAC that is not a current NIC is legitimate, if unusual.
- **`--dry-run` inspected a container it never created** (LDM-#1799). The guard read `context.get("dry_run")`; nothing writes that key, and the rest of the pipeline reads `manager.dry_run`. So the guard never fired and a dry run against a pinned node warned that it could not read the MAC of a container that did not exist.
- **A target's SSH user was stored twice and the copies drifted silently** (LDM-#1797). `~/.ldmrc` holds `user`; the Docker context holds `ssh://<user>@<host>`. `ldm target add` writes both, and nothing afterwards kept them in step. Found live: a node's context dialled a user nobody had configured, so every command through it failed with `Permission denied (publickey)` while `ldm target ls` showed a correct configuration. `ldm doctor` now reports the divergence, naming both users and the repair.
- **`ldm target add` erased the fields you did not re-pass** (LDM-#1797). It rebuilt the node from only the flags given, so the obvious repair for the drift above — re-running `add` with the right `--user` — reported success and **silently destroyed the MAC pin**, after which Liferay refused the licence. Sharper than it looks, because LDM-#1789 exists specifically to detect a dropped pin and this dropped one using the documented command during routine maintenance. `add` on an existing node now merges: an omitted flag keeps the stored value, a flag passed empty clears it, and the update says both what changed and what it kept.

### Internal

- **Promotion Delta Gate** added to `release-orchestration`: before `--promote`, diff the shipped code between the verified pre-release tag and HEAD. Every other gate guards the road *to* a pre-release; nothing guarded the gap between verifying one and promoting it, and four commits landed in `v2.22.0` after its `-pre.9` verification -- found by asking afterwards rather than by any check (LDM-#1754).
- **The E2E suite asserts `--mac-address` is actually persisted** (LDM-#1771), rather than that the flag exists. This is the assertion whose absence let LDM-#1759 ship.
- **`RELEASE_ANNOUNCEMENTS` precondition documented** (LDM-#1761): the first cut of any new minor fails `test_release_announcements_contract` until that constant carries an entry for the minor, which only `--bump preminor` can reach. This is what `-pre.1` was spent on.
- **CHANGELOG entry for `v2.23.0-pre.3`**, written before this cut rather than after it. `release.py` stubs an entry for the version it is tagging and the ratchet exempts only that one, so the previous release must be described first — the omission that burnt `v2.22.0-pre.4`. Verified rather than assumed: with `VERSION` set to `2.23.0-pre.4` and the stub still in place, the ratchet fails with `['v2.23.0-pre.3'] != []` (LDM-#1756).
- **The MAC interface probe announces itself** instead of pausing silently (LDM-#1780). It is an SSH round trip the operator did not ask for, and registering a node that is currently stopped — a legitimate thing to do — pays the full connect timeout: measured 6s to 16s against an unroutable host. A silent ten-second pause is indistinguishable from a hang. A reachable node answers in 1-2s.
- **The E2E suite asserts that restored search settings are stripped** (LDM-#1786). LDM-#1773's strip shipped with unit tests driving the real methods, but nothing exercised it through the binary. The suite now seeds a search property LDM never emits, plus a non-search canary, before the snapshot, and asserts after the restore that the search key is gone from **both** cascade layers while the canary survives in both — both layers because stripping only the layer-2 copy promotes the key to a layer-5 customisation that outranks everything, which was the first draft's bug. Observed to fail against `v2.23.0-pre.3` (pre-fix) and pass against `-pre.4`, against the real published binaries rather than from source.
- **`agent_push.sh` refuses a branch cut from release history** (LDM-#1801). Creating a feature branch while standing on `release/*` inherits every release commit, so the PR arrives as tens of files and conflicts on files the change never touched. It is documented, with the two commands that catch it, and it happened twice anyway — most recently reaching `pr-sprawl-check` as 24 files in a session where those commands had been run before every other PR that day. The signal is commit reachability, not file names: a commit in `origin/master..HEAD` that is also on a release branch can only have arrived that way, whereas matching filenames would misfire on a legitimate CHANGELOG edit. It prints the rebuild recipe, because resolving the conflicts merges the release commits a second time.

### Unchanged, and deliberately so

- **The MAC comparison itself still runs on `run`, `start` and `restart`.** It is a local `docker inspect`, it is free, and it is the refusal path LDM-#1798 made reachable -- dropping it to save an SSH call would have undone the fix that prompted this. There is an explicit test that switching the cross-check off does not take the exit `3` with it.
- **A loopback host is still treated as a node that may need its MAC pinned.** A change to skip the check for any loopback address was written and then **rejected before merging**: on AWS a target's host can be a loopback address while the Docker daemon is genuinely remote -- an SSH tunnel or an nginx front end -- and that node needs its pin. Configuring a MAC is the statement of intent, and `is_local_host` answers a routing question, not that one.

## [v2.23.0-pre.8] - 2026-09-18

**No change to the binary.** `-pre.7` and `-pre.8` differ only in the E2E
verification scripts. It exists so that a new assertion is exercised on a
pre-release tag rather than first running on the stable one.

### Added

- **The E2E suite now asserts that the MAC pin's refusal actually fires** (LDM-#1808). That branch had **never executed in the feature's life**: LDM-#1798 established it was unreachable by any supported command, because `mac_address` is part of the compose service spec, so `ldm run` recreates the container whenever it changes and the two values always agree. Exit `3` was in the documented contract and in `-pre.4`'s CHANGELOG, and had never once fired. The suite now pins a MAC, boots, changes only the *configured* value, runs `ldm restart`, and requires exit `3` naming both addresses. It needs **no remote node and no licence**, and takes about 40 seconds: a target named anything other than `local` and pointed at `127.0.0.1` is checked (`configured_mac` skips only the reserved name) while compose drives the local daemon. Observed to FAIL against `v2.23.0-pre.5`, where `restart` performs no check and exits 0, before being committed.

### Why this needed its own pre-release

`--promote` tags **`master`**, not the release branch, so a script-only change on
`master` ships in the stable release regardless of which pre-release was
verified. This assertion boots and tears down a container inside a suite that
already juggles ports and projects, and it had never run there. Left alone it
would have executed for the first time during the **stable** tag's CI, across
five distros -- the worst place to discover a problem. The Promotion Delta Gate
would not have caught it either: that gate diffs `ldm_core/`, and this is
`scripts/`.

## [v2.23.0-pre.7] - 2026-09-18

A single measured change. `-pre.6` and `-pre.7` differ only in whether an SSH
round trip happens on `start` and `restart`; the licence path, activation and the
MAC refusal are identical between them.

### Added

- **`ldm start --verify-mac` / `ldm restart --verify-mac`** (LDM-#1804): opts into checking the pinned MAC against the node's own interfaces. It is **off by default on these two commands** because that check is an SSH round trip, and it was measured turning a 2.9s restart into 4.3s -- 48%, unconditionally, on a hot command. That cost was never a considered trade: LDM-#1798 added the check and only its *failure* path was ever exercised, because a mismatch refuses before the cross-check runs, so the success path went unmeasured until it was timed against a real node. It remains **on by default for `ldm run`**, where 1.4s is noise against a multi-minute boot and the container is being created. Little is lost by the new default -- a wrong pin is already caught once at `ldm target add` (LDM-#1780), where a typo is cheapest to fix and costs one round trip rather than one per restart; `--verify-mac` covers the narrower case of a node whose interfaces changed after it was registered.

### Unchanged, and deliberately so

- **The MAC comparison itself still runs on `run`, `start` and `restart`.** It is a local `docker inspect`, it is free, and it is the refusal path LDM-#1798 made reachable -- dropping it to save an SSH call would have undone the fix that prompted this. There is an explicit test that switching the cross-check off does not take the exit `3` with it.
- **A loopback host is still treated as a node that may need its MAC pinned.** A change to skip the check for any loopback address was written and then **rejected before merging**: on AWS a target's host can be a loopback address while the Docker daemon is genuinely remote -- an SSH tunnel or an nginx front end -- and that node needs its pin. Configuring a MAC is the statement of intent, and `is_local_host` answers a routing question, not that one.

## [v2.23.0-pre.6] - 2026-09-18

**Everything here came out of verifying `-pre.5` on a real remote node.** The MAC
pin worked; what did not work was the machinery that exists to tell you when it
does not. Two of the three fixes below close branches that had never executed in
the feature's life.

### Fixed

- **The MAC pin's refusal could not be reached by any supported command** (LDM-#1798). `_verify_pinned_mac` ran only in the `ldm run` pipeline — which is the one path where a mismatch cannot occur, because `mac_address` is part of the compose *service spec*, so changing the configured value makes compose recreate the container by itself. Measured: a changed pin followed by `ldm run` with no `ldm rm` produced a container created seconds earlier carrying the *new* address. Meanwhile `ldm start` and `ldm restart` — the routes that genuinely leave a stale container — never ran the check at all. So exit `3` was in the documented contract, described in `-pre.4`'s CHANGELOG, and unreachable. The check now lives in `ldm_core/runtime/mac_pin.py` and runs after `start` and `restart` too, and the refusal has now fired on a real node for the first time. Its recovery hint is also caller-specific: from `start`/`restart` a plain `ldm run` recreates the container, so the previous `ldm rm && ldm run` was destructive advice for a problem that does not need it.
- **A wrong-but-applied pin passed silently** (LDM-#1798). The check compared the container's MAC against the *configured* one and never consulted the node, so a MAC belonging to no interface satisfied it perfectly — and Liferay then logged `MAC address matching failed` and served the Activation page, which is the exact LDM-#1752 symptom this check exists to prevent. After a match, the configured value is now checked against the node's interfaces and **warns** when it is on none of them. It warns rather than refuses for LDM-#1780's reason: Liferay validates against the licence, not the host, so a licence bound to a MAC that is not a current NIC is legitimate, if unusual.
- **`--dry-run` inspected a container it never created** (LDM-#1799). The guard read `context.get("dry_run")`; nothing writes that key, and the rest of the pipeline reads `manager.dry_run`. So the guard never fired and a dry run against a pinned node warned that it could not read the MAC of a container that did not exist.
- **A target's SSH user was stored twice and the copies drifted silently** (LDM-#1797). `~/.ldmrc` holds `user`; the Docker context holds `ssh://<user>@<host>`. `ldm target add` writes both, and nothing afterwards kept them in step. Found live: a node's context dialled a user nobody had configured, so every command through it failed with `Permission denied (publickey)` while `ldm target ls` showed a correct configuration. `ldm doctor` now reports the divergence, naming both users and the repair.
- **`ldm target add` erased the fields you did not re-pass** (LDM-#1797). It rebuilt the node from only the flags given, so the obvious repair for the drift above — re-running `add` with the right `--user` — reported success and **silently destroyed the MAC pin**, after which Liferay refused the licence. Sharper than it looks, because LDM-#1789 exists specifically to detect a dropped pin and this dropped one using the documented command during routine maintenance. `add` on an existing node now merges: an omitted flag keeps the stored value, a flag passed empty clears it, and the update says both what changed and what it kept.

### Internal

- **`agent_push.sh` refuses a branch cut from release history** (LDM-#1801). Creating a feature branch while standing on `release/*` inherits every release commit, so the PR arrives as tens of files and conflicts on files the change never touched. It is documented, with the two commands that catch it, and it happened twice anyway — most recently reaching `pr-sprawl-check` as 24 files in a session where those commands had been run before every other PR that day. The signal is commit reachability, not file names: a commit in `origin/master..HEAD` that is also on a release branch can only have arrived that way, whereas matching filenames would misfire on a legitimate CHANGELOG edit. It prints the rebuild recipe, because resolving the conflicts merges the release commits a second time.

## [v2.23.0-pre.5] - 2026-09-18

**The build the stable release is cut from.** `-pre.4` was verified on three remote nodes,
two with a MAC-bound licence; this closes every remaining actionable issue so the final
verification happens once, against what ships.

### Added

- **A package pin now records a claim, not just a version** (LDM-#1791). `tag` in an `.ldmp` manifest, and `liferay.workspace.product` in a workspace, are facts about how a package was *built*, and every consumer reads them as statements about what it *supports*. Those are different, and nothing distinguished them — so a consumer deviating from the pin could not tell whether they were doing something the publisher had tested and rejected, something nobody had tried, or something fine. A package can now declare a **ceiling** ("tested above this and it failed"), which is enforced with an explicit, recorded override, because packages get fixed and a consumer may know more than the publisher did. Everything else defaults to **no claim**, announced visibly when the tag is resolved rather than left silent — a field defaulting to anything reassuring would recreate LDM-#1782, which is silence being read as success. Worth stating plainly, because the whole design follows from it: **there is no such thing as a stale pin.** A package legitimately pins an older line because a later one was tried and failed, or because nobody has had time to qualify one. The pin is the statement of what is known to work.
- **`ldm doctor` detects a MAC pin an older client dropped** (LDM-#1789). Compose runs client-side, so a node configured with a MAC gets no pin at all if the binary that rendered the compose file predates the feature — silently, and with the node's configuration looking correct throughout.

### Fixed

- **A site-initializer client extension present at first boot never initialised its site** (LDM-#1779). `ldm import` moved every `client-extensions/*.zip` into `osgi/client-extensions/`, which for a site initializer is the wrong moment: its bundle tracker opens before the built-in `welcome`/`cms` initializers have created the Guest site's layouts, and `ServiceContextFactory` needs one — so it threw a `NullPointerException` in `PortalImpl.getCanonicalURL` while the portal went on logging `STARTED`. Nothing surfaced it; the site was simply never initialised. The same artifact dropped into the already-running portal initialises in about 100 ms, so LDM now stages a site-initializer zip and deploys it once the portal is ready. Detection keys on `Liferay-Client-Extension-Site-Initializer` in the built zip's `WEB-INF/liferay-plugin-package.properties` — the header the extender itself tracks — and deliberately not on `client-extension.yaml`, which declares `type: siteInitializer` but is a *source* descriptor that is not present in the built artifact at all. A zip that cannot be read is treated as **not** a site initializer: deferring something LDM failed to identify would change the deployment moment for extensions that are fine where they are. Found while building the LDM-#1745 harness, on a live DXP `2026.q3.0`.
- **A failed boot exited zero, and the run did not say what it had booted** (LDM-#1790). Automation could not distinguish a healthy start from a timeout, and a report of either named neither the LDM version nor the tag it resolved — nor whether that tag came from a flag, a workspace pin or discovery. That last distinction is what made LDM-#1782 expensive to diagnose: green runs had been qualifying one product line while declaring another, and nothing in the output showed it.

### Internal

- **The E2E suite asserts that restored search settings are stripped** (LDM-#1786). LDM-#1773's strip shipped with unit tests driving the real methods, but nothing exercised it through the binary. The suite now seeds a search property LDM never emits, plus a non-search canary, before the snapshot, and asserts after the restore that the search key is gone from **both** cascade layers while the canary survives in both — both layers because stripping only the layer-2 copy promotes the key to a layer-5 customisation that outranks everything, which was the first draft's bug. Observed to fail against `v2.23.0-pre.3` (pre-fix) and pass against `-pre.4`, against the real published binaries rather than from source.

## [v2.23.0-pre.4] - 2026-09-17

**The build the remote-node verification runs against.** `-pre.3` was verified in CI but
never on a node; this carries the shipped-code work that was briefly deferred to `v2.23.1`
and then pulled forward, so the verification happens once, against what will ship.

### Fixed

- **A wrong `--mac-address` was pinned faithfully and failed silently** (LDM-#1780). There are two ways the MAC pin fails and only one was guarded. `_verify_pinned_mac` compares the *container's* MAC against the *configured* one, so a typo satisfies it perfectly: LDM pins the wrong address, both values agree, the check passes, and Liferay then refuses the licence — leaving the operator with the original LDM-#1752 symptom (healthy container, `License registered` in the log, Activation page instead of Sign In) about twenty minutes after making the mistake. `target add` now reads the node's `/sys/class/net/*/address` over the SSH round trip LDM already makes and **warns** when the configured value matches none of them, naming each interface so the right one can be picked. It warns rather than refuses, because Liferay validates the container's MAC against the licence and does not care what the host's interfaces are — a licence bound to a MAC that is not a current NIC is legitimate, if unusual. A node that cannot be reached stays silent: unreachable is not wrong. Measured against a real node: a wrong MAC warns and names `docker0` and `ens5`, the host NIC is silent, `docker0` is silent (it *is* an interface), and upper case is silent (normalised). Reported by the AICA team from the `-pre.3` run, where it cost two CI runs.
- **An OOM kill was reported as a health-check timeout** (LDM-#1773). LDM printed *"Timed out waiting for Liferay to become healthy"* after eight minutes while `docker inspect` said `OOMKilled: true` and the log said `Killed  start_liferay.sh`. Those send you to opposite places — one says wait longer or look at the application, the other says this machine does not have enough memory. Neither ending of the wait could report it: the JVM is killed while the entrypoint survives, so the container stays up and never becomes healthy, and `ExitCode` is `0` because the entrypoint then exits cleanly, so nothing in the container's state contradicted the message. Both endings now read `{{.State.OOMKilled}}` from the node the project actually runs on. An unreadable flag stays "could not ask" rather than becoming either claim.
- **An imported package silently overrode the resolved search mode** (LDM-#1773). A project that resolved to shared search rendered a stack running an embedded Elasticsearch *inside* the Liferay container as well, whose sidecar JVM defaults to `-Xmx2g` — enough to be OOM-killed on an 8 GB machine sized for a 3 GB ceiling, which is how both halves of this issue were found. Two routes did it: `custom_env`, which `composer` appends *after* the shared-search block so Compose resolves the duplicate to the later entry; and the packaged `portal-ext.properties`, where `module.framework.properties.*` is the highest-precedence route into that configuration. LDM now strips the search settings it owns from both on import and **says what it removed and where it came from**. Search topology is a property of the consumer's machine, like `jdbc.default.url` and `virtual.hosts.valid.hosts`, which `import` already regenerates. Worth noting for package publishers: you no longer need to sanitise search settings out of a package, and if you set one deliberately the announcement is where you will see it discarded.
- **The Promotion Delta Gate diffed the wrong ref** (LDM-#1777). It runs immediately after the "promotion must be run from a `release/` branch" guard, so `HEAD` is the release branch — while `create_and_push_tag` checks out `master` and tags **that**. A commit merged to `master` and never backported therefore shipped in the stable tag while the gate reported a clean delta, which is the exact failure the gate exists to prevent arriving by another route. It now compares the verified tag against both refs and names which one carries each file, because the remedy differs: a master-only file wants a backport, a branch-only file wants another pre-release. It also reports the commits `master` carries that the release branch does not, on every run — reported rather than refused, because the live example was test-only and a gate that fires on a correct configuration gets disabled.

### Internal

- **CHANGELOG entry for `v2.23.0-pre.3`**, written before this cut rather than after it. `release.py` stubs an entry for the version it is tagging and the ratchet exempts only that one, so the previous release must be described first — the omission that burnt `v2.22.0-pre.4`. Verified rather than assumed: with `VERSION` set to `2.23.0-pre.4` and the stub still in place, the ratchet fails with `['v2.23.0-pre.3'] != []` (LDM-#1756).
- **The MAC interface probe announces itself** instead of pausing silently (LDM-#1780). It is an SSH round trip the operator did not ask for, and registering a node that is currently stopped — a legitimate thing to do — pays the full connect timeout: measured 6s to 16s against an unroutable host. A silent ten-second pause is indistinguishable from a hang. A reachable node answers in 1-2s.

## [v2.23.0-pre.3] - 2026-09-17

**The first build in this cycle that can actually exercise the MAC pin.** `-pre.2`
could not: `--mac-address` was parsed and then never passed to the handler, so every
node added with it recorded an empty string. Use this build or later for the remote-node
verification.

Alongside that, four release-hardening mechanisms, written after the maintainer asked what
more could be automated "to prevent burning too many tags". Each replaces something someone
had to remember with something that refuses.

### Fixed

- **`--mac-address` now reaches the handler.** The flag was declared and parsed, and then never passed on, so `cmd_target_add` stored `""`. `target add` reported success and `~/.ldmrc` carried the key with an empty value — a node configured with `-pre.2` has no pin at all. Found by the maintainer running the released binary; CI was green throughout, because both tests for the feature asserted the flag was *declared* and that the docs named it, and neither followed the value to storage. The regression test now drives `_build_command_map` and invokes the real lambda, rather than calling `cmd_target_add` with the arguments the dispatch was supposed to supply (LDM-#1759).

### Added

- **The release contracts are checked *before* the tag exists** (LDM-#1758). `release.py` runs the CHANGELOG ratchet and the architectural-contract tests before creating a tag, and aborts saying the number is still available. Both tags burnt in the previous cycle — `v2.22.0-pre.4` and `v2.23.0-pre.1` — failed preconditions that already had fast tests; they simply were not run at the moment the decision was made. Tags are immutable, so the cost of finding out afterwards is a version number that can never be reclaimed.
- **The published release is verified, not just the build** (LDM-#1768). `scripts/verify_published_release.py --tag X` downloads a release and asserts what a *user* finds: every asset present, every checksum matching, and the manifest's claims true. Validated against real published history rather than fixtures — it reports 4 problems on `v2.22.0-pre.7`, 5 on `-pre.5`, and passes `v2.23.0-pre.2`. Four defects in the previous cycle were invisible to green CI and surfaced only by downloading the release.
- **The Promotion Delta Gate is a check, not a documented rule** (LDM-#1765). `--promote` diffs `ldm_core/` against the last `v*-pre.*` tag and refuses when stable would ship code no pre-release ever verified. `constants.py`, `resources/ldm.1` and `/tests/` are excluded — version stamps and files that do not ship. `--allow-promotion-delta` is the deliberate opt-out. Hit promoting `v2.22.0`, where four commits merged after the verification and shipped in stable.
- **`agent_push.sh` refuses to commit onto `master` or `release/*`** unless `LDM_ALLOW_PROTECTED_BRANCH=1` (LDM-#1764). Four branch mistakes were made in a single day.

### Internal

- **The E2E suite asserts `--mac-address` is actually persisted** (LDM-#1771), rather than that the flag exists. This is the assertion whose absence let LDM-#1759 ship.
- **`RELEASE_ANNOUNCEMENTS` precondition documented** (LDM-#1761): the first cut of any new minor fails `test_release_announcements_contract` until that constant carries an entry for the minor, which only `--bump preminor` can reach. This is what `-pre.1` was spent on.

## [v2.23.0-pre.2] - 2026-09-16

The first *published* cut of the `v2.23.0` cycle. `-pre.1` was tagged but published
nothing, so this carries that entire change set -- the MAC pinning work described under
`-pre.1` -- plus the `RELEASE_ANNOUNCEMENTS` entry that unblocked it.

**It cannot exercise its own headline feature.** `ldm target add --mac-address` is
accepted and parsed, and then never passed to the handler, so the value is stored as an
empty string and no pin is ever written into the compose file (LDM-#1759). A node added
with this build reports success and records nothing. Use `-pre.3` or later for the
remote-node verification; there is nothing to test here. Found by the maintainer running
the released binary: CI was green, and both tests for the feature asserted the flag was
*declared* rather than following the value to storage.

### Fixed

- **`RELEASE_ANNOUNCEMENTS` carries the `v2.23` entry**, which is what `-pre.1` was spent on: `test_release_announcements_contract` fails the first cut of any new minor until that entry exists, and that path is reachable only through `--bump preminor`/`--premajor` -- `--bump beta` reuses a minor that already has one, so the contract had never fired since it was written (LDM-#1756).

## [v2.23.0-pre.1] - 2026-09-16

Opened the `v2.23.0` cycle with the MAC pinning work. **Published nothing**: the first cut
of a new minor fails `test_release_announcements_contract` until `RELEASE_ANNOUNCEMENTS`
carries an entry for that minor, which only `--bump preminor` can hit -- `--bump beta`
reuses a minor that already has one. The same change set ships in `v2.23.0-pre.2`.

### Added

- **`ldm target add --mac-address`**: pin the Liferay container to a node's NIC address so a MAC-bound licence activates. Measured on `aws-1` with `liferay/dxp:2026.q3.0`: the container recreated identically except for the MAC took validation from "MAC address matching failed" to passed, and the portal from the DXP Activation page to the Sign In form. Without a pin the container takes a bridge address and the failure is silent -- healthy container, `License registered` in the log, and a portal that will not sign in. Configured per node and never inferred: a node shows `ens5` beside `docker0` and `br-*`, all plausible and one licensed (LDM-#1752).
- **The pin is verified, not assumed**: after start LDM reads the container's MAC back and refuses (exit 3) when it is not the configured one, naming both values and stating that a recreate is required. Writing `mac_address` into the compose file is a request -- a toolchain that ignores the form, a container created before the value changed, and a wrongly chosen interface all present identically, and one check covers all three (LDM-#1752).

### Internal

- **Promotion Delta Gate** added to `release-orchestration`: before `--promote`, diff the shipped code between the verified pre-release tag and HEAD. Every other gate guards the road *to* a pre-release; nothing guarded the gap between verifying one and promoting it, and four commits landed in `v2.22.0` after its `-pre.9` verification -- found by asking afterwards rather than by any check (LDM-#1754).

## [v2.22.0] - 2026-09-16

### Added

- **Workspace Product Pin**: `ldm run` now compares the tag it resolved against the linked workspace's `liferay.workspace.product` and warns when they disagree, offering the pinned tag interactively and proceeding under `-y`. A shared OSGi fragment/override bundle carries `Import-Package` ranges bound to one product line, so a bundle built for `dxp-2026.q3.0` does not resolve on `2026.q3.2` -- and the failure surfaces as an unresolved bundle in the OSGi log at boot, a long way from the tag decision that caused it. Silent on an explicit `-t`, on agreement, and where the workspace has no pin (LDM-#1658).
- **Removal is no longer silent or irreversible by accident**: `ldm rm --delete` asks before removing anything, naming the project's size and whether a snapshot exists -- `ldm snapshot` is the real undo for database state, and nothing surfaced that at the moment of deletion. It then archives `meta`, `files/`, `osgi/configs/` and `routes/` to `~/.ldm/removed/<project>-<timestamp>.tar.gz` before deleting: ~20 KB against 1.1 GB of reproducible `osgi/`, so the delete still frees the space. Database and admin passwords are **removed** from that archive by default; `--keep-credentials` or `ldm config set tombstone_keep_credentials true` keeps them, and says that securing the file becomes yours (LDM-#1703).
- **`ldm link --no-monitor` and `--no-run`**: link a workspace without leaving a file watcher running, or without booting it. `cmd_link` previously ended in a watcher that ran until Ctrl-C, so the command could not be scripted -- or asserted on, which is why LDM-#1684's verification had to be deferred (LDM-#1689).
- **`verification-bundle.zip` is published per release**: `docs/TESTING.md` told a verifier to `curl` a single script, and the suite also needs `common/` -- the DXP activation key and the Elasticsearch configuration. Without it LDM emits a *warning*, not a failure, so the run completed, exited 0 and reported success having applied neither. Every assertion genuinely passed; they tested a smaller system than the report claimed. That is the LDM-#1662 family one layer quieter: there a *failed* run was reported as passed, here a *degraded* one. The bundle also fixes a second hazard -- `LDM_REF` is derived from whichever binary is on `PATH`, so the script could come from a different release than the binary under test; published per tag, the pairing is structural. The builder refuses to produce a bundle missing any required member, because a quiet omission would recreate the original hole while looking like the fix for it (LDM-#1718).
- **`scripts/install_verification.sh` / `.ps1`**: one command stages the whole E2E suite -- fetches the bundle for a tag, verifies every checksum, unpacks so `common/` sits beside the script, sets the executable bit, and fetches and checksums the matching binary. It deliberately does **not** install the binary onto `PATH` (that needs elevation, and a verification helper making a machine-wide change silently is a surprise) and cannot invent an activation key (LDM-#1735).
- **The installer is published as a release asset** and covered by the release `checksums.txt`. Without that it existed only in a checkout, which defeats its purpose: a release should be verifiable from its published artifacts alone, the way a user does it. It is staged **before** the checksums are generated -- staged after, it would ship unchecksummed (LDM-#1735).
- **The installer verifies itself** against the release it is staging, before downloading anything else, so the bootstrap is no longer the one unverified link in a chain that checksums everything else. A mismatch **warns** rather than fails -- reusing one installer across releases is legitimate -- and a release predating the asset passes quietly rather than complaining about its own absence (LDM-#1735).
- **Activation-key discovery**: the key lives in a `common/` folder on each machine, relative to where the suite is run, so the installer looks there before asking. It checks the target's own `common/` first, then `./common/`. Requiring a flag for something already on disk in a known place is one more thing to remember, and forgetting it fails **silently** -- LDM only warns, the suite exits 0, and reports success having verified an unlicensed DXP (LDM-#1740).

### Fixed

- **Liferay Cloud Import**: importing or linking an LCP workspace copied **none** of its standalone services and read its code from the repository root rather than the nested `liferay/` workspace, where an LCP repository keeps nothing. Detection ran through a `hasattr` guard naming a method that has never existed at any commit, so it was permanently false; the service scan it gated was also walking one level *above* the repository, so repointing detection alone would still have found nothing. `--cloud-project` was declared on four commands and read by none, with `handlers/cloud.py` falling through to the project directory name -- so `lcp` commands ran against a guessed project. A non-interactive import that cannot determine the ID now refuses with exit `2` rather than guessing (LDM-#1681).
- **Workspace Product Tag**: an imported or linked workspace recorded no `tag` at all, so tag resolution fell through to discovery and the project booted whatever line that returned. The workspace's own `liferay.workspace.product` is read again, along with the portal/DXP flag that decides the image repository. Unlike the original, an explicit `-t` now wins over the pin -- an explicit tag is a decision, not an accident, which is the same rule LDM-#1658 applies (LDM-#1693).
- **Linked Workspace Path**: `ldm link` stopped recording the workspace it linked to, so `ldm monitor <project>` with no path argument refused, and `ldm snapshot` silently dropped the workspace's git origin. `ldm link` itself kept working, which is why this stayed quiet -- it is re-attaching the watcher afterwards that failed (LDM-#1684).
- **Import Rollback**: a failed import into an **existing** project now restores the artifact directories it had already overwritten, instead of leaving them half-written. A brand-new project was always cleaned up; an existing one never was (LDM-#1677).
- **Logging Output**: `logging` records are routed into the trace log at `~/.ldm/last-command.log` instead of raw stderr, so library output no longer interleaves with LDM's own console rendering (LDM-#1669).
- **Workspace OSGi configuration was never applied**: a workspace's `configs/<env>/` was copied wholesale into `osgi/configs/`, putting everything one environment-directory too deep -- `osgi/configs/local/osgi/configs/x.config`, where Liferay scans `osgi/configs/*.config`. `ls <project>/osgi/configs/*.config` matched nothing. `--target-env` was ignored with it, so every environment was copied rather than the chosen one, and the workspace's `portal-ext.properties` reached nowhere the portal reads. Properties are now **merged** into the project's file rather than copied over it, so LDM's own generated values survive (LDM-#1692).
- **`ldm run --dry-run` reported a healthy host as broken**: it always failed with `FATAL: VOLUME MOUNTING IS BROKEN` and told the user to stop and reconfigure Colima. The check writes a sentinel and probes it from a container; under dry run the sentinel goes to the dry-run VFS and the container is announced rather than started, so it compared a token never written against a probe that never ran. A second defect was hiding behind it -- with the check skipped, a dry run reached the readiness poll for the first time and waited the full `--timeout` for a container that was never started (LDM-#1704, LDM-#1712).
- **Three declared flags did nothing**: `--env` is published in the CLI reference and was read by no code at all, though `composer.py` still consumed `meta["custom_env"]` and `ldm config env` still wrote it -- only the flag-to-meta step was missing. `--gogo-port` had a live consumer and no producer. `--mount-logs` has neither, and is now reported as the no-op it is rather than silently ignored. `ldm_version` is stamped into an imported project again, and the Gradle JVM check is called again (LDM-#1695).
- **The linked workspace path is recorded**: `ldm link` stopped writing `workspace_path`, so `ldm monitor <project>` with no path refused and `ldm snapshot` silently dropped the workspace's git origin (LDM-#1684).
- **`LDM Release E2E` had never passed**: the job installs no JDK, and `ubuntu-latest` ships 17 while `ldm import` requires 21 -- so every `ldm import` in the suite died before doing anything, on six consecutive releases including the v2.21.1 stable. The failure never said "Java": it surfaced as a manifest-parse assertion, which was that check working correctly but phrased in terms of the manifest (LDM-#1701).
- **The removal archive ignored `LDM_HOME`**: `tombstone_dir()` used `Path.home()`, so `sudo ldm rm --delete` wrote to root's home where the user would never find it, and tests could not isolate the store -- eight archives were found in a developer's real `~/.ldm/removed` (LDM-#1715).
- **Fresh clones failed five healthy tests**: `ldm_core/ui_colors.py` is generated and gitignored, so a new clone or `git worktree` lacked it, `ui.py` fell back to empty colour codes, and tests asserting ANSI output failed on strings differing only by escapes nobody can see (LDM-#1707).
- **A macOS runner with no Docker was reported as a failure**: the best-effort platform job was permanently red for an environment nobody had verified either way. Skip is now a third state, distinct from both pass and fail, and its report is written outside the `verify-*` glob deliberately -- `sync_compatibility.py` derives status from report *content*, not filename, so a skip report inside that glob would have been ingested as a pass (LDM-#1720).
- **The port pre-flight asked the wrong machine**: `ldm --target <node> run` refused on a port conflict detected on the *operator's laptop*, naming a local PID and telling the user to free a local port -- none of which bears on whether the node can bind it. It was wrong in both directions: unable to see the node, it also **cleared** ports genuinely taken there, and the conflict resurfaced later as a container that would not start. `DockerService.published_host_ports`, `is_running` and `container_publishing_port` had all accepted a target since remote nodes shipped; the call sites simply never passed one. The socket probe stays local-only deliberately -- measured against a real node, a port held by a running container *times out* rather than connecting, because the security group drops it, so a probe there reports "free" for a port that is in use (LDM-#1727).
- **`--fragment-patch-timeout` was a retry count, not a timeout**: it is documented, named and announced as a budget in seconds, then converted to `timeout // 5` retries with the `sleep(5)` *inside* the loop next to the HTTP calls. Nothing measured elapsed time, so the real cost was `retries x (request latency + 5s)` -- a floor rather than a ceiling, widening exactly when the API is slow, which is the case the budget exists to bound. Both loops took the full count independently, doubling it again, and the macOS external-drive bump makes the default 180 retries *per loop*. Observed: `ldm run` still polling after **27 minutes** against a nominal 900s budget with Liferay healthy throughout. Now a real `time.monotonic()` deadline, shared by both loops (LDM-#1728).
- **The wait was also silent**: the per-attempt messages were `UI.detail`, which is gated behind INFO_MODE/VERBOSE, so a default run printed nothing at all for the entire poll -- indistinguishable from a hang, and diagnosing it needed a stack sample. There is now a visible line each minute naming the remaining budget (LDM-#1728).
- **The verification bundle claimed to carry a DXP activation key it cannot contain**: `MANIFEST.txt` stated that `common/` carries the activation key, while listing its own contents -- showing no key -- immediately underneath. It never can: `.gitignore` excludes `common/activation-key-*.xml` because the key is licensed, so no CI checkout has one to package. LDM-#1718 built the bundle precisely because a run without `common/` applies neither the key nor the search configuration, LDM only *warns*, and the suite still exits 0 reporting success. The bundle closes that for the search configuration; for the licensed half it does not, and saying otherwise told a verifier the gap was closed when it was open. The manifest now declares the absence and says how to supply a key. No test caught it because `collect()` walks the filesystem rather than git, and a developer's checkout has the key sitting there untracked -- so the test verified the developer's machine, not the artifact users download (LDM-#1733).
- **A newer macOS was recorded as Tahoe, overwriting its verification report**: `sync_compatibility.py` mapped the Darwin kernel with an open-ended `>= 25`, so *every* macOS after Tahoe was labelled Tahoe -- `darwin27` and `darwin40` alike. The label becomes the **slug**, so a Golden Gate run canonicalised onto the existing Tahoe report and was ingested as a re-verification of it: one row where there should be two, the new OS appearing verified when it was not, and the old one's evidence overwritten by a different machine's run. Underneath it, the report took its platform from `$OSTYPE`, which bash bakes in at *compile* time -- measured on a macOS 27.0 machine, `$OSTYPE` said `darwin26.0` while `uname -r` said `27.0.0`. Reports now carry `macos-<productVersion> <arch>` from `sw_vers`, which the parser matches ahead of any kernel guess, and an unmapped kernel renders distinctly rather than borrowing the newest known name (LDM-#1737).
- **The architecture nearly went with it**: the Architecture column is derived from the same platform string via `arm64`/`aarch64` plus a hardcoded `darwin25` hint, so replacing the `$OSTYPE` token without adding `uname -m` relabelled every Apple Silicon run as Apple Intel -- a different wrong row in place of the one being fixed. Caught by running the parser, not by reading it (LDM-#1737).
- **Commits pushed to a branch whose PR had already merged were silently lost**: two reached no branch anyone would merge. The push succeeded, `agent_push.sh` printed its success banner, and CI stayed green because there was nothing new to test. The first symptom was `v2.22.0-pre.7` publishing without `install_verification.sh`, found only by listing the release's assets. The wrapper now warns when the branch's PR is `MERGED`/`CLOSED` -- after the push, never fatal, and naming the remedy (LDM-#1740).

### Internal

- **Rollback Allowlist**: narrowed to the one real gap, and the stage-ownership rule it rests on is now asserted rather than described (LDM-#1643).
- **`hasattr` Ratchet**: a dead `hasattr`-guarded branch removed, and an AST check added that fails when such a guard names something defined nowhere under `ldm_core`. `hasattr` around a call is uniquely quiet -- no `AttributeError`, no failing test, nothing for a linter to object to -- and two instances survived a God Object decomposition, a pipeline refactor and a shim cleanup between them. The allowlist is now **empty** (LDM-#1679, LDM-#1681).
- **E2E Coverage**: `scripts/verify_e2e_refactor.{sh,ps1}` gain assertions for the cloud import, the cloud project ID refusal and the workspace product pin. Each was run against the pre-fix commit and observed to fail before being committed; a test that has never failed has not been shown to test anything. The `ldm link` assertion for LDM-#1684 is deliberately deferred and tracked, because `cmd_link` ends in a watcher that never returns (LDM-#1690, LDM-#1689).
- **Environment Setup**: the testing-and-ci skill pointed at `.pytest_venv/bin/pre-commit`, a console script endpoint protection deletes by name, in a venv the developer skill says is not a safe fallback. It now points at `scripts/setup_pre_commit.sh`, which has existed since July and was referenced nowhere (LDM-#1687).
- **Fragment-override harness**: `scripts/verify_fragment_override.py` builds its own fragment collection, overrides its configuration and records what `element_id` actually is -- the one genuinely unverified thing in LDM-#1618, and the measurement that decides whether the module rung can ever fire. Deliberately outside the default gate: it needs a Liferay boot and content it creates over HTTP (LDM-#1714).
- **E2E coverage**: assertions added for the cloud import, the cloud project-ID refusal, the workspace product pin, the product tag and the linked workspace path, in both halves of the verification pair. Each was run against the pre-fix code and observed to fail before being committed -- in one case by constructing a state that does not exist in history, because the obvious pre-fix commit failed for the wrong reason (LDM-#1690, LDM-#1689).
- **Dependencies**: `mcp` 2.1.1 -> 2.2.0 and `ruff` 0.16.6 -> 0.16.7. The `mcp` bump needed a third pin site Dependabot does not know about, which the LDM-#1483 guard caught.
- **Fragment harness**: creates its own page rather than requiring one placed by hand, fetches the per-DXP-line module jar, and gained `--node` to run against a registered compute node and `--search-mode` defaulting to `sidecar` -- a shared Global Search node is infrastructure a throwaway verification project should not have to provision (LDM-#1719).
- **`jira-tracker` skill retired** in favour of the `github-jira-sync` plugin: one mechanism for raising and tracking upstream issues rather than two that could disagree.
- **The fragment-override module rung was verified, and cannot fire**: LDM-#1618 asked for the measurement its own docstring said was missing (*"may not be a `fragmentEntryLinkId` at all ... confirm against a live instance"*). It is not one. On DXP 2026.q1.7-lts the Headless page-element `id` is a **UUID** (`6f4d9b77-4a14-a5dc-74b8-e0ef4dcee23c`), while the `fragmententrylink` rows behind the same page are numeric (`33693`); `PageElement.id` is declared `string` in the schema, and the module's own source takes `@PathParam("fragmentEntryLinkId") long`. A UUID cannot coerce, so JAX-RS answers 404 before the method body runs -- the rung has never fired once since it shipped. It is **kept**, not deleted: it routes through `FragmentEntryLinkLocalService`, so cache invalidation, model listeners and indexing all happen, none of which the SQL fallback can do. What changed is that LDM no longer spends a request proving the id is the wrong shape. The other two rungs stay, upstream-blocked and tracked by #883 (LDM-#1618).
- **The fragment harness stopped producing a false negative**: it reported `No page element carried the fragment key` -- clean, plausible and wrong, and a message four independent faults each produce. Fixed: the page payload went to headless-admin-site carrying headless-delivery's schema (`400 The property "title" is not defined in SitePage.`); `friendlyUrlPath` needs a leading slash; and the fixture's `fragmentEntryKey` was ignored by Liferay, which derives the key from `name`, so the harness matched a key that never existed. A fourth is worked around: the collection is deployed during `ldm import`, before resource permissions exist, and the deploy throws `NoSuchResourcePermissionException` while Liferay still logs *"Deployed ... successfully"* -- leaving `fragmententry` empty. It is now redeployed after boot (LDM-#1729).
- **And one that cannot be fixed**: there is no Headless endpoint that creates a page element. `POST .../site-pages` accepts nested `pageElements`, returns 2xx and discards them -- confirmed as zero `fragmententrylink` rows, with both the bare and collection-qualified key; delivery `PUT` is 405; the admin-site element `PUT` needs an element that already exists. #883 records the same wall from the other side. A page carrying a fragment must come from a site initializer or the UI, which is precisely the scenario the override feature is *for*. The harness now reads the page back, treats that as the authority rather than the create status, and says so plainly (LDM-#1729).
- **CHANGELOG entries for `v2.22.0-pre.3` and `v2.22.0-pre.4`**: `release.py` stubs an entry for the version it is cutting and the ratchet exempts only that one, so the *previous* release must be described before the next cut. `pre.3` was left a stub, which passed locally -- it was still the exempt "being released" version at the time -- and failed in CI once `pre.4` became current. Tags are immutable, so `pre.4` is burnt rather than re-cut (LDM-#1699).
- **Convention recorded**: name a macOS release by its project/code name wherever one exists -- "Golden Gate", not "macOS 27" -- keyed on the product version. Stated beside the table it governs, because that is where someone adding the next version will be looking (LDM-#1737).
- **The installer cleans up after itself**: the bundle archive is removed once its contents are extracted **and verified**, and `checksums.txt` once the binary hash matches. Ordering is deliberate -- a failed checksum is exactly when the archive is worth keeping, being the evidence of what arrived. `SHA256SUMS` and `MANIFEST.txt` stay, since one re-checks the extracted files and the other records what the release does not contain (LDM-#1741).
- **The installer cleans up after itself**: the bundle archive is removed once its contents are extracted **and verified**, and `checksums.txt` once the binary hash matches. Ordering is deliberate -- a failed checksum is exactly when the archive is worth keeping, being the evidence of what arrived. `SHA256SUMS` and `MANIFEST.txt` stay: one re-checks the extracted files, the other records what the release does not contain (LDM-#1741).

## [v2.22.0-pre.9] - 2026-09-16

The pre-release the v2.22.0 stable was verified against, across six environments: macOS 27
Golden Gate (Colima), macOS 26 Tahoe (OrbStack), Fedora 44, WSL2, and Windows 11 under both
PowerShell 5.1 and 7.

### Internal

- **The installer cleans up after itself**: the bundle archive is removed once its contents are extracted **and verified**, and `checksums.txt` once the binary hash matches. Ordering is deliberate -- a failed checksum is exactly when the archive is worth keeping, being the evidence of what arrived. `SHA256SUMS` and `MANIFEST.txt` stay: one re-checks the extracted files, the other records what the release does not contain (LDM-#1741).

## [v2.22.0-pre.8] - 2026-09-16

The first release whose verification installer is actually reachable. `v2.22.0-pre.7`
shipped the installer in the repository but not in the release, so the documented download
404'd.

### Added

- **The installer is published as a release asset** and covered by the release `checksums.txt`. Without that it existed only in a checkout, which defeats its purpose: a release should be verifiable from its published artifacts alone, the way a user does it. It is staged **before** the checksums are generated -- staged after, it would ship unchecksummed (LDM-#1735).
- **The installer verifies itself** against the release it is staging, before downloading anything else, so the bootstrap is no longer the one unverified link in a chain that checksums everything else. A mismatch **warns** rather than fails -- reusing one installer across releases is legitimate -- and a release predating the asset passes quietly rather than complaining about its own absence (LDM-#1735).
- **Activation-key discovery**: the key lives in a `common/` folder on each machine, relative to where the suite is run, so the installer looks there before asking. It checks the target's own `common/` first, then `./common/`. Requiring a flag for something already on disk in a known place is one more thing to remember, and forgetting it fails **silently** -- LDM only warns, the suite exits 0, and reports success having verified an unlicensed DXP (LDM-#1740).

### Fixed

- **Commits pushed to a branch whose PR had already merged were silently lost**: two reached no branch anyone would merge. The push succeeded, `agent_push.sh` printed its success banner, and CI stayed green because there was nothing new to test. The first symptom was `v2.22.0-pre.7` publishing without `install_verification.sh`, found only by listing the release's assets. The wrapper now warns when the branch's PR is `MERGED`/`CLOSED` -- after the push, never fatal, and naming the remedy (LDM-#1740).

### Internal

- **The installer cleans up after itself**: the bundle archive is removed once its contents are extracted **and verified**, and `checksums.txt` once the binary hash matches. Ordering is deliberate -- a failed checksum is exactly when the archive is worth keeping, being the evidence of what arrived. `SHA256SUMS` and `MANIFEST.txt` stay, since one re-checks the extracted files and the other records what the release does not contain (LDM-#1741).

## [v2.22.0-pre.7] - 2026-09-16

The macOS labelling fix, and the first installer -- though the installer is **not usable
from this release**: the commits publishing it as a release asset were lost to a merge (see
`v2.22.0-pre.8`), so it exists only in the repository here. Verify with `-pre.8`.

### Added

- **`scripts/install_verification.sh` / `.ps1`**: one command stages the whole E2E suite -- fetches the bundle for a tag, verifies every checksum, unpacks so `common/` sits beside the script, sets the executable bit, and fetches and checksums the matching binary. It deliberately does **not** install the binary onto `PATH` (that needs elevation, and a verification helper making a machine-wide change silently is a surprise) and cannot invent an activation key (LDM-#1735).

### Fixed

- **A newer macOS was recorded as Tahoe, overwriting its verification report**: `sync_compatibility.py` mapped the Darwin kernel with an open-ended `>= 25`, so *every* macOS after Tahoe was labelled Tahoe -- `darwin27` and `darwin40` alike. The label becomes the **slug**, so a Golden Gate run canonicalised onto the existing Tahoe report and was ingested as a re-verification of it: one row where there should be two, the new OS appearing verified when it was not, and the old one's evidence overwritten by a different machine's run. Underneath it, the report took its platform from `$OSTYPE`, which bash bakes in at *compile* time -- measured on a macOS 27.0 machine, `$OSTYPE` said `darwin26.0` while `uname -r` said `27.0.0`. Reports now carry `macos-<productVersion> <arch>` from `sw_vers`, which the parser matches ahead of any kernel guess, and an unmapped kernel renders distinctly rather than borrowing the newest known name (LDM-#1737).
- **The architecture nearly went with it**: the Architecture column is derived from the same platform string via `arm64`/`aarch64` plus a hardcoded `darwin25` hint, so replacing the `$OSTYPE` token without adding `uname -m` relabelled every Apple Silicon run as Apple Intel -- a different wrong row in place of the one being fixed. Caught by running the parser, not by reading it (LDM-#1737).

### Internal

- **Convention recorded**: name a macOS release by its project/code name wherever one exists -- "Golden Gate", not "macOS 27" -- keyed on the product version. Stated beside the table it governs, because that is where someone adding the next version will be looking (LDM-#1737).

## [v2.22.0-pre.6] - 2026-09-15

Adds the bundle-manifest honesty fix. Everything else in the cycle is unchanged from
`v2.22.0-pre.5`.

### Fixed

- **The verification bundle claimed to carry a DXP activation key it cannot contain**: `MANIFEST.txt` stated that `common/` carries the activation key, while listing its own contents -- showing no key -- immediately underneath. It never can: `.gitignore` excludes `common/activation-key-*.xml` because the key is licensed, so no CI checkout has one to package. LDM-#1718 built the bundle precisely because a run without `common/` applies neither the key nor the search configuration, LDM only *warns*, and the suite still exits 0 reporting success. The bundle closes that for the search configuration; for the licensed half it does not, and saying otherwise told a verifier the gap was closed when it was open. The manifest now declares the absence and says how to supply a key. No test caught it because `collect()` walks the filesystem rather than git, and a developer's checkout has the key sitting there untracked -- so the test verified the developer's machine, not the artifact users download (LDM-#1733).

## [v2.22.0-pre.5] - 2026-09-14

Carries the `v2.22.0-pre.4` change set -- that tag published nothing, because its own CI
failed the CHANGELOG ratchet -- plus the CHANGELOG entries whose absence caused it. See
the `v2.22.0-pre.4` entry below for what the fixes actually are.

### Internal

- **CHANGELOG entries for `v2.22.0-pre.3` and `v2.22.0-pre.4`**: `release.py` stubs an entry for the version it is cutting and the ratchet exempts only that one, so the *previous* release must be described before the next cut. `pre.3` was left a stub, which passed locally -- it was still the exempt "being released" version at the time -- and failed in CI once `pre.4` became current. Tags are immutable, so `pre.4` is burnt rather than re-cut (LDM-#1699).

## [v2.22.0-pre.4] - 2026-09-14

Three defects found by *using* LDM rather than by reading it -- each one silent, and two of
them the reason another one took so long to find. Tagged but never published: its own CI
failed the CHANGELOG ratchet, because `v2.22.0-pre.3` below was left an empty stub. The
same change set ships in `v2.22.0-pre.5`.

### Fixed

- **The port pre-flight asked the wrong machine**: `ldm --target <node> run` refused on a port conflict detected on the *operator's laptop*, naming a local PID and telling the user to free a local port -- none of which bears on whether the node can bind it. It was wrong in both directions: unable to see the node, it also **cleared** ports genuinely taken there, and the conflict resurfaced later as a container that would not start. `DockerService.published_host_ports`, `is_running` and `container_publishing_port` had all accepted a target since remote nodes shipped; the call sites simply never passed one. The socket probe stays local-only deliberately -- measured against a real node, a port held by a running container *times out* rather than connecting, because the security group drops it, so a probe there reports "free" for a port that is in use (LDM-#1727).
- **`--fragment-patch-timeout` was a retry count, not a timeout**: it is documented, named and announced as a budget in seconds, then converted to `timeout // 5` retries with the `sleep(5)` *inside* the loop next to the HTTP calls. Nothing measured elapsed time, so the real cost was `retries x (request latency + 5s)` -- a floor rather than a ceiling, widening exactly when the API is slow, which is the case the budget exists to bound. Both loops took the full count independently, doubling it again, and the macOS external-drive bump makes the default 180 retries *per loop*. Observed: `ldm run` still polling after **27 minutes** against a nominal 900s budget with Liferay healthy throughout. Now a real `time.monotonic()` deadline, shared by both loops (LDM-#1728).
- **The wait was also silent**: the per-attempt messages were `UI.detail`, which is gated behind INFO_MODE/VERBOSE, so a default run printed nothing at all for the entire poll -- indistinguishable from a hang, and diagnosing it needed a stack sample. There is now a visible line each minute naming the remaining budget (LDM-#1728).

### Internal

- **The fragment-override module rung was verified, and cannot fire**: LDM-#1618 asked for the measurement its own docstring said was missing (*"may not be a `fragmentEntryLinkId` at all ... confirm against a live instance"*). It is not one. On DXP 2026.q1.7-lts the Headless page-element `id` is a **UUID** (`6f4d9b77-4a14-a5dc-74b8-e0ef4dcee23c`), while the `fragmententrylink` rows behind the same page are numeric (`33693`); `PageElement.id` is declared `string` in the schema, and the module's own source takes `@PathParam("fragmentEntryLinkId") long`. A UUID cannot coerce, so JAX-RS answers 404 before the method body runs -- the rung has never fired once since it shipped. It is **kept**, not deleted: it routes through `FragmentEntryLinkLocalService`, so cache invalidation, model listeners and indexing all happen, none of which the SQL fallback can do. What changed is that LDM no longer spends a request proving the id is the wrong shape. The other two rungs stay, upstream-blocked and tracked by #883 (LDM-#1618).
- **The fragment harness stopped producing a false negative**: it reported `No page element carried the fragment key` -- clean, plausible and wrong, and a message four independent faults each produce. Fixed: the page payload went to headless-admin-site carrying headless-delivery's schema (`400 The property "title" is not defined in SitePage.`); `friendlyUrlPath` needs a leading slash; and the fixture's `fragmentEntryKey` was ignored by Liferay, which derives the key from `name`, so the harness matched a key that never existed. A fourth is worked around: the collection is deployed during `ldm import`, before resource permissions exist, and the deploy throws `NoSuchResourcePermissionException` while Liferay still logs *"Deployed ... successfully"* -- leaving `fragmententry` empty. It is now redeployed after boot (LDM-#1729).
- **And one that cannot be fixed**: there is no Headless endpoint that creates a page element. `POST .../site-pages` accepts nested `pageElements`, returns 2xx and discards them -- confirmed as zero `fragmententrylink` rows, with both the bare and collection-qualified key; delivery `PUT` is 405; the admin-site element `PUT` needs an element that already exists. #883 records the same wall from the other side. A page carrying a fragment must come from a site initializer or the UI, which is precisely the scenario the override feature is *for*. The harness now reads the page back, treats that as the authority rather than the create status, and says so plainly (LDM-#1729).

## [v2.22.0-pre.3] - 2026-09-14

Mostly about the verification suite being able to tell the truth: what it ships with, and
what it reports when it cannot run.

### Added

- **`verification-bundle.zip` is published per release**: `docs/TESTING.md` told a verifier to `curl` a single script, and the suite also needs `common/` -- the DXP activation key and the Elasticsearch configuration. Without it LDM emits a *warning*, not a failure, so the run completed, exited 0 and reported success having applied neither. Every assertion genuinely passed; they tested a smaller system than the report claimed. That is the LDM-#1662 family one layer quieter: there a *failed* run was reported as passed, here a *degraded* one. The bundle also fixes a second hazard -- `LDM_REF` is derived from whichever binary is on `PATH`, so the script could come from a different release than the binary under test; published per tag, the pairing is structural. The builder refuses to produce a bundle missing any required member, because a quiet omission would recreate the original hole while looking like the fix for it (LDM-#1718).

### Fixed

- **A macOS runner with no Docker was reported as a failure**: the best-effort platform job was permanently red for an environment nobody had verified either way. Skip is now a third state, distinct from both pass and fail, and its report is written outside the `verify-*` glob deliberately -- `sync_compatibility.py` derives status from report *content*, not filename, so a skip report inside that glob would have been ingested as a pass (LDM-#1720).

### Internal

- **Fragment harness**: creates its own page rather than requiring one placed by hand, fetches the per-DXP-line module jar, and gained `--node` to run against a registered compute node and `--search-mode` defaulting to `sidecar` -- a shared Global Search node is infrastructure a throwaway verification project should not have to provision (LDM-#1719).
- **`jira-tracker` skill retired** in favour of the `github-jira-sync` plugin: one mechanism for raising and tracking upstream issues rather than two that could disagree.

## [v2.22.0-pre.2] - 2026-09-14

Carries everything in `v2.22.0-pre.1` plus the rest of the PR #497 family, the removal
safety work, and the first `LDM Release E2E` run that could actually pass.

The `[Unreleased]` items below this entry were folded into it -- they describe work that is
in this pre-release.

### Added

- **Removal is no longer silent or irreversible by accident**: `ldm rm --delete` asks before removing anything, naming the project's size and whether a snapshot exists -- `ldm snapshot` is the real undo for database state, and nothing surfaced that at the moment of deletion. It then archives `meta`, `files/`, `osgi/configs/` and `routes/` to `~/.ldm/removed/<project>-<timestamp>.tar.gz` before deleting: ~20 KB against 1.1 GB of reproducible `osgi/`, so the delete still frees the space. Database and admin passwords are **removed** from that archive by default; `--keep-credentials` or `ldm config set tombstone_keep_credentials true` keeps them, and says that securing the file becomes yours (LDM-#1703).
- **`ldm link --no-monitor` and `--no-run`**: link a workspace without leaving a file watcher running, or without booting it. `cmd_link` previously ended in a watcher that ran until Ctrl-C, so the command could not be scripted -- or asserted on, which is why LDM-#1684's verification had to be deferred (LDM-#1689).

### Fixed

- **Workspace OSGi configuration was never applied**: a workspace's `configs/<env>/` was copied wholesale into `osgi/configs/`, putting everything one environment-directory too deep -- `osgi/configs/local/osgi/configs/x.config`, where Liferay scans `osgi/configs/*.config`. `ls <project>/osgi/configs/*.config` matched nothing. `--target-env` was ignored with it, so every environment was copied rather than the chosen one, and the workspace's `portal-ext.properties` reached nowhere the portal reads. Properties are now **merged** into the project's file rather than copied over it, so LDM's own generated values survive (LDM-#1692).
- **`ldm run --dry-run` reported a healthy host as broken**: it always failed with `FATAL: VOLUME MOUNTING IS BROKEN` and told the user to stop and reconfigure Colima. The check writes a sentinel and probes it from a container; under dry run the sentinel goes to the dry-run VFS and the container is announced rather than started, so it compared a token never written against a probe that never ran. A second defect was hiding behind it -- with the check skipped, a dry run reached the readiness poll for the first time and waited the full `--timeout` for a container that was never started (LDM-#1704, LDM-#1712).
- **Three declared flags did nothing**: `--env` is published in the CLI reference and was read by no code at all, though `composer.py` still consumed `meta["custom_env"]` and `ldm config env` still wrote it -- only the flag-to-meta step was missing. `--gogo-port` had a live consumer and no producer. `--mount-logs` has neither, and is now reported as the no-op it is rather than silently ignored. `ldm_version` is stamped into an imported project again, and the Gradle JVM check is called again (LDM-#1695).
- **The linked workspace path is recorded**: `ldm link` stopped writing `workspace_path`, so `ldm monitor <project>` with no path refused and `ldm snapshot` silently dropped the workspace's git origin (LDM-#1684).
- **`LDM Release E2E` had never passed**: the job installs no JDK, and `ubuntu-latest` ships 17 while `ldm import` requires 21 -- so every `ldm import` in the suite died before doing anything, on six consecutive releases including the v2.21.1 stable. The failure never said "Java": it surfaced as a manifest-parse assertion, which was that check working correctly but phrased in terms of the manifest (LDM-#1701).
- **The removal archive ignored `LDM_HOME`**: `tombstone_dir()` used `Path.home()`, so `sudo ldm rm --delete` wrote to root's home where the user would never find it, and tests could not isolate the store -- eight archives were found in a developer's real `~/.ldm/removed` (LDM-#1715).
- **Fresh clones failed five healthy tests**: `ldm_core/ui_colors.py` is generated and gitignored, so a new clone or `git worktree` lacked it, `ui.py` fell back to empty colour codes, and tests asserting ANSI output failed on strings differing only by escapes nobody can see (LDM-#1707).

### Internal

- **Fragment-override harness**: `scripts/verify_fragment_override.py` builds its own fragment collection, overrides its configuration and records what `element_id` actually is -- the one genuinely unverified thing in LDM-#1618, and the measurement that decides whether the module rung can ever fire. Deliberately outside the default gate: it needs a Liferay boot and content it creates over HTTP (LDM-#1714).
- **E2E coverage**: assertions added for the cloud import, the cloud project-ID refusal, the workspace product pin, the product tag and the linked workspace path, in both halves of the verification pair. Each was run against the pre-fix code and observed to fail before being committed -- in one case by constructing a state that does not exist in history, because the obvious pre-fix commit failed for the wrong reason (LDM-#1690, LDM-#1689).
- **Dependencies**: `mcp` 2.1.1 -> 2.2.0 and `ruff` 0.16.6 -> 0.16.7. The `mcp` bump needed a third pin site Dependabot does not know about, which the LDM-#1483 guard caught.

## [v2.22.0-pre.1] - 2026-09-14

Five of the six user-visible items in this release are **restorations**. A single commit --
`ba24c012`, "Refactor cmd_import into modular ImportPipeline" (PR #497, 2026-07-10) --
rebuilt a `project_meta` literal and a workspace-path derivation from scratch and silently
dropped fields on the way. Nothing failed, no test went red, and the losses shipped for
two months. They were found one at a time while fixing the first, then enumerated
mechanically so the set could be closed rather than kept being stumbled into (LDM-#1695).

### Added

- **Workspace Product Pin**: `ldm run` now compares the tag it resolved against the linked workspace's `liferay.workspace.product` and warns when they disagree, offering the pinned tag interactively and proceeding under `-y`. A shared OSGi fragment/override bundle carries `Import-Package` ranges bound to one product line, so a bundle built for `dxp-2026.q3.0` does not resolve on `2026.q3.2` -- and the failure surfaces as an unresolved bundle in the OSGi log at boot, a long way from the tag decision that caused it. Silent on an explicit `-t`, on agreement, and where the workspace has no pin (LDM-#1658).

### Fixed

- **Liferay Cloud Import**: importing or linking an LCP workspace copied **none** of its standalone services and read its code from the repository root rather than the nested `liferay/` workspace, where an LCP repository keeps nothing. Detection ran through a `hasattr` guard naming a method that has never existed at any commit, so it was permanently false; the service scan it gated was also walking one level *above* the repository, so repointing detection alone would still have found nothing. `--cloud-project` was declared on four commands and read by none, with `handlers/cloud.py` falling through to the project directory name -- so `lcp` commands ran against a guessed project. A non-interactive import that cannot determine the ID now refuses with exit `2` rather than guessing (LDM-#1681).
- **Workspace Product Tag**: an imported or linked workspace recorded no `tag` at all, so tag resolution fell through to discovery and the project booted whatever line that returned. The workspace's own `liferay.workspace.product` is read again, along with the portal/DXP flag that decides the image repository. Unlike the original, an explicit `-t` now wins over the pin -- an explicit tag is a decision, not an accident, which is the same rule LDM-#1658 applies (LDM-#1693).
- **Linked Workspace Path**: `ldm link` stopped recording the workspace it linked to, so `ldm monitor <project>` with no path argument refused, and `ldm snapshot` silently dropped the workspace's git origin. `ldm link` itself kept working, which is why this stayed quiet -- it is re-attaching the watcher afterwards that failed (LDM-#1684).
- **Import Rollback**: a failed import into an **existing** project now restores the artifact directories it had already overwritten, instead of leaving them half-written. A brand-new project was always cleaned up; an existing one never was (LDM-#1677).
- **Logging Output**: `logging` records are routed into the trace log at `~/.ldm/last-command.log` instead of raw stderr, so library output no longer interleaves with LDM's own console rendering (LDM-#1669).

### Internal

- **Rollback Allowlist**: narrowed to the one real gap, and the stage-ownership rule it rests on is now asserted rather than described (LDM-#1643).
- **`hasattr` Ratchet**: a dead `hasattr`-guarded branch removed, and an AST check added that fails when such a guard names something defined nowhere under `ldm_core`. `hasattr` around a call is uniquely quiet -- no `AttributeError`, no failing test, nothing for a linter to object to -- and two instances survived a God Object decomposition, a pipeline refactor and a shim cleanup between them. The allowlist is now **empty** (LDM-#1679, LDM-#1681).
- **E2E Coverage**: `scripts/verify_e2e_refactor.{sh,ps1}` gain assertions for the cloud import, the cloud project ID refusal and the workspace product pin. Each was run against the pre-fix commit and observed to fail before being committed; a test that has never failed has not been shown to test anything. The `ldm link` assertion for LDM-#1684 is deliberately deferred and tracked, because `cmd_link` ends in a watcher that never returns (LDM-#1690, LDM-#1689).
- **Environment Setup**: the testing-and-ci skill pointed at `.pytest_venv/bin/pre-commit`, a console script endpoint protection deletes by name, in a venv the developer skill says is not a safe fallback. It now points at `scripts/setup_pre_commit.sh`, which has existed since July and was referenced nowhere (LDM-#1687).

## [v2.21.1] - 2026-09-11

### Added

- **Package Manifest Verification**: `ldm import` now verifies a local or downloaded `.ldmp` manifest before acting on it, and both E2E verification scripts assert the refusal (LDM-#1621).
- **Tag Discovery Canary**: A scheduled workflow exercises tag discovery against the live Docker Hub registry every Monday, so a systemic upstream outage is detectable. Every unit test of discovery mocks the registry, so nothing previously observed a real failure (LDM-#1650).

### Changed

- **Verification Reporting**: A failed platform verification now fails its job. Previously the Windows and macOS arms swallowed the verify script's exit status and reported success while uploading a failure report, manufacturing coverage that did not exist.
- **Compatibility Matrix**: Each Linux distribution gets its own row, every Linux verification arm is published rather than two of five, and the Provider column is derived from the declared environment label.
- **Platform Verification**: Every arm of the multi-OS verification workflow now installs JDK 21; previously none did, which is why the matrix was red on all arms from 2026-09-07 (LDM-#1659).
- **Platform Verification Split**: the Windows arms are removed (GitHub-hosted Windows runners run Windows containers, so a Linux image can never start there) and macOS moves to a separate best-effort workflow, leaving the Linux workflow -- the one that publishes the compatibility matrix -- meaningfully green or red. Windows coverage remains the manually verified PowerShell 5.1 / 7 / WSL2 runs (LDM-#1662).
- **Release Notes**: a patch release now carries its own upgrade-banner highlights instead of repeating the minor series', and the man page documents the full value sets for `--database-mode` and `--search-mode` (LDM-#1663, LDM-#1664).

### Fixed

- **Tag Discovery**: Discovery asked Docker Hub for its *oldest* tags rather than its newest, so `latest`, `--tag-latest`, `qr`, `u`, `nightly` and every `--portal` lookup resolved to a years-old image. This is the principal reason to take this release (LDM-#1647).
- **Stale Tag Fallback**: The bundled `.product_info.json` fallback was two and a half years out of date and contained no quarterly releases, so a discovery failure fell back to an unusable list (LDM-#1648).
- **Withdrawn Tags**: `--release-type u` resolved to `7.4.13-u999`, an upstream placeholder tag whose images are not served. Withdrawn tags are now skipped (LDM-#1649).
- **Rollback on Fatal Error**: A pipeline stage calling `UI.die` bypassed rollback entirely, because `SystemExit` is not an `Exception`. Rollback now runs and the original exit code is preserved (LDM-#1630).
- **Idempotent No-Op**: Exit code 5 -- returned when a non-interactive `ldm run`/`ldm up` finds the project already running -- triggered a rollback as though the run had failed (LDM-#1636).
- **Stage-Owned Rollback**: Each import stage now owns the rollback of the filesystem state it creates, instead of one stage hosting another's cleanup (LDM-#1635).
- **Meta File Validation**: `read_meta` now refuses a meta file that is in neither supported format, rather than silently returning an empty mapping (LDM-#1629).
- **`ldm db stop` Diagnostics**: A failure now names the container and the cause instead of exiting quietly (LDM-#1615).
- **Cascading Default Writes**: A cascading-default write that nothing would ever read is refused rather than silently accepted, and the configuration documentation no longer names paths and command scopes that do not exist (LDM-#1651).
- **Report Header Parsing**: Every verification-report header capture is bounded to its own line, so a blank `Platform:` no longer absorbs the header beneath it (LDM-#1633).
- **Windows PowerShell 5.1**: The verification report's `Platform:` header was empty on Windows PowerShell 5.1, because `$PSVersionTable.OS` arrived in PowerShell 6 (LDM-#1639).
- **Java Version Detection**: `_check_java_version` and the Gradle JVM check required a dotted component, so a General Availability JDK string with none -- `openjdk version "25"` -- matched nothing and was reported as a version *mismatch* for a JDK newer than required (LDM-#1660).
- **Controlled Refusals No Longer Look Like Crashes**: a deliberate `UI.die` inside a pipeline stage printed a full Python traceback on top of its own message, so a clean refusal -- a missing JDK, a port already in use -- was presented to the user as a crash. Introduced in this cycle by LDM-#1630 and not present in v2.21.0 (LDM-#1668).
- **Release Tagging**: the CHANGELOG ratchet introduced in LDM-#1663 had no exemption for the version being released. Because `scripts/release.py` writes the entry stub, commits it and pushes the tag in a single run, the check could never be satisfied at tag time -- it failed `lint-and-test`, which `build` and then `release` depend on, so **`v2.21.1-pre.3` published no binaries**. The rule is now "one release behind": the entry for the current version is exempt, every earlier entry must be populated (LDM-#1673).

### Internal

- **Test Isolation**: `test_fragments.py` mocked the wrong transport, so its tests could open real sockets to a Liferay running on localhost. The full test gate went from 19m40s to roughly 7m as a result (LDM-#1644).
- **Manifest Recovery**: The package-listing recovery path is now driven through a real import rather than asserted in isolation (LDM-#1588).

## [v2.21.1-pre.4] - 2026-09-11

Carries everything in `v2.21.1-pre.3`, which published no release assets (see below).

### Fixed

- **Release Tagging**: the CHANGELOG ratchet introduced in LDM-#1663 had no exemption for the version being released. Because `scripts/release.py` writes the entry stub, commits it and pushes the tag in a single run, the check could never be satisfied at tag time -- it failed `lint-and-test`, which `build` and then `release` depend on, so **`v2.21.1-pre.3` published no binaries**. The rule is now "one release behind": the entry for the current version is exempt, every earlier entry must be populated (LDM-#1673).

## [v2.21.1-pre.3] - 2026-09-11

### Fixed

- **Controlled Refusals No Longer Look Like Crashes**: a deliberate `UI.die` inside a pipeline stage printed a full Python traceback on top of its own message, so a clean refusal -- a missing JDK, a port already in use -- was presented to the user as a crash. Introduced in this cycle by LDM-#1630 and not present in v2.21.0 (LDM-#1668).

### Changed

- **Platform Verification Split**: the Windows arms are removed (GitHub-hosted Windows runners run Windows containers, so a Linux image can never start there) and macOS moves to a separate best-effort workflow, leaving the Linux workflow -- the one that publishes the compatibility matrix -- meaningfully green or red. Windows coverage remains the manually verified PowerShell 5.1 / 7 / WSL2 runs (LDM-#1662).
- **Release Notes**: a patch release now carries its own upgrade-banner highlights instead of repeating the minor series', and the man page documents the full value sets for `--database-mode` and `--search-mode` (LDM-#1663, LDM-#1664).

## [v2.21.1-pre.2] - 2026-09-11

### Fixed

- **Java Version Detection**: `_check_java_version` and the Gradle JVM check required a dotted component, so a General Availability JDK string with none -- `openjdk version "25"` -- matched nothing and was reported as a version *mismatch* for a JDK newer than required (LDM-#1660).

### Changed

- **Platform Verification**: Every arm of the multi-OS verification workflow now installs JDK 21; previously none did, which is why the matrix was red on all arms from 2026-09-07 (LDM-#1659).

## [v2.21.1-pre.1] - 2026-09-10

### Added

- **Package Manifest Verification**: `ldm import` now verifies a local or downloaded `.ldmp` manifest before acting on it, and both E2E verification scripts assert the refusal (LDM-#1621).
- **Tag Discovery Canary**: A scheduled workflow exercises tag discovery against the live Docker Hub registry every Monday, so a systemic upstream outage is detectable. Every unit test of discovery mocks the registry, so nothing previously observed a real failure (LDM-#1650).

### Fixed

- **Tag Discovery**: Discovery asked Docker Hub for its *oldest* tags rather than its newest, so `latest`, `--tag-latest`, `qr`, `u`, `nightly` and every `--portal` lookup resolved to a years-old image. This is the principal reason to take this release (LDM-#1647).
- **Stale Tag Fallback**: The bundled `.product_info.json` fallback was two and a half years out of date and contained no quarterly releases, so a discovery failure fell back to an unusable list (LDM-#1648).
- **Withdrawn Tags**: `--release-type u` resolved to `7.4.13-u999`, an upstream placeholder tag whose images are not served. Withdrawn tags are now skipped (LDM-#1649).
- **Rollback on Fatal Error**: A pipeline stage calling `UI.die` bypassed rollback entirely, because `SystemExit` is not an `Exception`. Rollback now runs and the original exit code is preserved (LDM-#1630).
- **Idempotent No-Op**: Exit code 5 -- returned when a non-interactive `ldm run`/`ldm up` finds the project already running -- triggered a rollback as though the run had failed (LDM-#1636).
- **Stage-Owned Rollback**: Each import stage now owns the rollback of the filesystem state it creates, instead of one stage hosting another's cleanup (LDM-#1635).
- **Meta File Validation**: `read_meta` now refuses a meta file that is in neither supported format, rather than silently returning an empty mapping (LDM-#1629).
- **`ldm db stop` Diagnostics**: A failure now names the container and the cause instead of exiting quietly (LDM-#1615).
- **Cascading Default Writes**: A cascading-default write that nothing would ever read is refused rather than silently accepted, and the configuration documentation no longer names paths and command scopes that do not exist (LDM-#1651).
- **Report Header Parsing**: Every verification-report header capture is bounded to its own line, so a blank `Platform:` no longer absorbs the header beneath it (LDM-#1633).
- **Windows PowerShell 5.1**: The verification report's `Platform:` header was empty on Windows PowerShell 5.1, because `$PSVersionTable.OS` arrived in PowerShell 6 (LDM-#1639).

### Changed

- **Verification Reporting**: A failed platform verification now fails its job. Previously the Windows and macOS arms swallowed the verify script's exit status and reported success while uploading a failure report, manufacturing coverage that did not exist.
- **Compatibility Matrix**: Each Linux distribution gets its own row, every Linux verification arm is published rather than two of five, and the Provider column is derived from the declared environment label.

### Internal

- **Test Isolation**: `test_fragments.py` mocked the wrong transport, so its tests could open real sockets to a Liferay running on localhost. The full test gate went from 19m40s to roughly 7m as a result (LDM-#1644).
- **Manifest Recovery**: The package-listing recovery path is now driven through a real import rather than asserted in isolation (LDM-#1588).

## [v2.21.0] - 2026-09-06

### Added

-

## [v2.21.0-pre.2] - 2026-09-03

### Added

-

## [v2.21.0-pre.1] - 2026-09-03

### Added

-

## [v2.20.0] - 2026-09-02

### Added

-

## [v2.20.0-pre.6] - 2026-09-02

### Added

-

## [v2.20.0-pre.5] - 2026-09-02

### Added

-

## [v2.20.0-pre.4] - 2026-09-02

### Added

-

## [v2.20.0-pre.3] - 2026-09-01

### Added

-

## [v2.20.0-pre.2] - 2026-09-01

### Added

-

## [v2.20.0-pre.1] - 2026-09-01

### Added

-

## [v2.19.0] - 2026-08-31

### Added

-

## [v2.19.0-pre.3] - 2026-08-31

### Added

-

## [v2.19.0-pre.2] - 2026-08-31

### Added

-

## [v2.19.0-pre.1] - 2026-08-29

### Added

-

## [v2.18.0] - 2026-08-28

### Added

-

## [v2.18.0-pre.11] - 2026-08-28

### Added

-

## [v2.18.0-pre.10] - 2026-08-27

### Added

-

## [v2.18.0-pre.9] - 2026-08-27

### Added

-

## [v2.18.0-pre.8] - 2026-08-27

### Added

-

## [v2.18.0-pre.7] - 2026-08-27

### Added

-

## [v2.18.0-pre.6] - 2026-08-27

### Added

-

## [v2.18.0-pre.5] - 2026-08-27

### Added

-

## [v2.18.0-pre.4] - 2026-08-27

### Added

-

## [v2.18.0-pre.3] - 2026-08-27

### Added

-

## [v2.18.0-pre.2] - 2026-08-27

### Added

-

## [v2.18.0-pre.1] - 2026-08-26

### Added

-

## [v2.17.0] - 2026-08-25

### Added

-

## [v2.17.0-pre.3] - 2026-08-25

### Added

-

## [v2.17.0-pre.2] - 2026-08-25

### Added

-

## [v2.17.0-pre.1] - 2026-08-24

### Added

-

## [v2.16.0] - 2026-08-24

### Added

-

## [v2.16.0-pre.3] - 2026-08-24

### Added

-

## [v2.16.0-pre.2] - 2026-08-23

### Added

-

## [v2.16.0-pre.1] - 2026-08-22

### Added

-

## [v2.15.33] - 2026-08-21

### Added

-

## [v2.15.33-pre.3] - 2026-08-21

### Added

-

## [v2.15.33-pre.2] - 2026-08-21

### Added

-

## [v2.15.33-pre.1] - 2026-08-21

### Added

-

## [v2.15.32] - 2026-08-21

### Added

-

## [v2.15.32-pre.1] - 2026-08-21

### Added

-

## [v2.15.31] - 2026-08-20

### Added

-

## [v2.15.31-pre.1] - 2026-08-20

### Added

-

## [v2.15.30] - 2026-08-20

### Added

-

## [v2.15.30-pre.19] - 2026-08-20

### Added

-

## [v2.15.30-pre.18] - 2026-08-20

### Added

-

## [v2.15.30-pre.17] - 2026-08-20

### Added

-

## [v2.15.30-pre.16] - 2026-08-20

### Added

-

## [v2.15.30-pre.15] - 2026-08-20

### Added

-

## [v2.15.30-pre.14] - 2026-08-20

### Added

-

## [v2.15.30-pre.13] - 2026-08-20

### Added

-

## [v2.15.30-pre.12] - 2026-08-20

### Added

-

## [v2.15.30-pre.11] - 2026-08-20

### Added

-

## [v2.15.30-pre.10] - 2026-08-20

### Added

-

## [v2.15.30-pre.9] - 2026-08-20

### Added

-

## [v2.15.30-pre.8] - 2026-08-19

### Added

-

## [v2.15.30-pre.7] - 2026-08-19

### Added

-

## [v2.15.30-pre.6] - 2026-08-19

### Added

-

## [v2.15.30-pre.5] - 2026-08-19

### Added

-

## [v2.15.30-pre.4] - 2026-08-19

### Added

-

## [v2.15.30-pre.3] - 2026-08-19

### Added

-

## [v2.15.30-pre.2] - 2026-08-19

### Added

-

## [v2.15.30-pre.1] - 2026-08-19

### Added

-

## [v2.15.29] - 2026-08-19

### Added

-

## [v2.15.29-pre.18] - 2026-08-18

### Added

-

## [v2.15.29-pre.17] - 2026-08-18

### Added

-

## [v2.15.29-pre.16] - 2026-08-18

### Added

-

## [v2.15.29-pre.15] - 2026-08-18

### Added

-

## [v2.15.29-pre.14] - 2026-08-18

### Added

-

## [v2.15.29-pre.13] - 2026-08-18

### Added

-

## [v2.15.29-pre.12] - 2026-08-18

### Added

-

## [v2.15.29-pre.11] - 2026-08-18

### Added

-

## [v2.15.29-pre.10] - 2026-08-18

### Added

-

## [v2.15.29-pre.9] - 2026-08-18

### Added

-

## [v2.15.29-pre.8] - 2026-08-18

### Added

-

## [v2.15.29-pre.7] - 2026-08-18

### Added

-

## [v2.15.29-pre.6] - 2026-08-18

### Added

-

## [v2.15.29-pre.5] - 2026-08-18

### Added

-

## [v2.15.29-pre.4] - 2026-08-18

### Added

-

## [v2.15.29-pre.3] - 2026-08-18

### Added

-

## [v2.15.29-pre.2] - 2026-08-18

### Added

-

## [v2.15.29-pre.1] - 2026-08-18

### Added

-

## [v2.15.28] - 2026-08-18

### Added

-

## [v2.15.28-pre.6] - 2026-08-18

### Added

-

## [v2.15.28-pre.5] - 2026-08-18

### Added

-

## [v2.15.28-pre.4] - 2026-08-15

### Added

-

## [v2.15.28-pre.3] - 2026-08-14

### Added

-

## [v2.15.28-pre.2] - 2026-08-14

### Added

-

## [v2.15.28-pre.1] - 2026-08-14

### Added

-

## [v2.15.27] - 2026-08-11

### Added

-

## [v2.15.27-pre.4] - 2026-08-11

### Added

-

## [v2.15.27-pre.3] - 2026-08-10

### Added

-

## [v2.15.27-pre.2] - 2026-08-10

### Added

-

## [v2.15.27-pre.1] - 2026-08-07

### Added

-

## [v2.15.26] - 2026-08-07

### Added

-

## [v2.15.26-pre.10] - 2026-08-06

### Added

-

## [v2.15.26-pre.9] - 2026-08-06

### Added

-

## [v2.15.26-pre.8] - 2026-08-05

### Added

-

## [v2.15.26-pre.7] - 2026-08-05

### Added

-

## [v2.15.26-pre.6] - 2026-08-04

### Added

-

## [v2.15.26-pre.5] - 2026-08-04

### Added

-

## [v2.15.26-pre.4] - 2026-08-04

### Added

-

## [v2.15.26-pre.3] - 2026-08-04

### Added

-

## [v2.15.26-pre.2] - 2026-08-03

### Added

-

## [v2.15.26-pre.1] - 2026-08-03

### Added

-

## [v2.15.25] - 2026-08-03

### Added

-

## [v2.15.25-pre.14] - 2026-07-31

### Added

-

## [v2.15.25-pre.13] - 2026-07-31

### Added

-

## [v2.15.25-pre.12] - 2026-07-31

### Added

-

## [v2.15.25-pre.11] - 2026-07-31

### Added

-

## [v2.15.25-pre.10] - 2026-07-31

### Added

-

## [v2.15.25-pre.9] - 2026-07-30

### Added

-

## [v2.15.25-pre.8] - 2026-07-30

### Added

-

## [v2.15.25-pre.7] - 2026-07-30

### Added

-

## [v2.15.25-pre.6] - 2026-07-30

### Added

-

## [v2.15.25-pre.5] - 2026-07-30

### Added

-

## [v2.15.25-pre.4] - 2026-07-29

### Added

-

## [v2.15.25-pre.3] - 2026-07-29

### Added

-

## [v2.15.25-pre.1] - 2026-07-29

### Added

-

## [v2.15.23] - 2026-07-29

### Added

-

## [v2.15.23-pre.9] - 2026-07-29

### Added

-

## [v2.15.23-pre.8] - 2026-07-28

### Added

-

## [v2.15.23-pre.7] - 2026-07-28

### Added

-

## [v2.15.23-pre.6] - 2026-07-28

### Added

-

## [v2.15.23-pre.5] - 2026-07-28

### Added

-

## [v2.15.23-pre.4] - 2026-07-27

### Added

-

## [v2.15.23-pre.3] - 2026-07-27

### Added

-

## [v2.15.23-pre.2] - 2026-07-27

### Added

-

## [v2.15.23-pre.1] - 2026-07-27

### Added

-

## [v2.15.22] - 2026-07-27

### Added

-

## [v2.15.22-pre.25] - 2026-07-26

### Added

-

## [v2.15.22-pre.24] - 2026-07-26

### Added

-

## [v2.15.22-pre.14] - 2026-07-23

### Added

-

## [v2.15.22-pre.13] - 2026-07-23

### Added

-

## [v2.15.22-pre.12] - 2026-07-23

### Added

-

## [v2.15.22-pre.11] - 2026-07-23

### Added

-

## [v2.15.22-pre.10] - 2026-07-23

### Added

-

## [v2.15.22-pre.9] - 2026-07-23

### Added

-

## [v2.15.22-pre.8] - 2026-07-22

### Added

-

## [v2.15.22-pre.7] - 2026-07-22

### Added

-

## [v2.15.22-pre.6] - 2026-07-22

### Added

-

## [v2.15.22-pre.5] - 2026-07-22

### Added

-

## [v2.15.22-pre.4] - 2026-07-22

### Added

-

## [v2.15.22-pre.3] - 2026-07-22

### Added

-

## [v2.15.22-pre.2] - 2026-07-22

### Added

-

## [v2.15.22-pre.1] - 2026-07-21

### Added

-

## [v2.15.21] - 2026-07-21

### Added

-

## [v2.15.21-pre.2] - 2026-07-21

### Added

-

## [v2.15.21-pre.1] - 2026-07-21

### Added

-

## [v2.15.20] - 2026-07-20

### Added

-

## [v2.15.19-pre.8] - 2026-07-20

### Added

-

## [v2.15.19-pre.8] - 2026-07-20

### Added

-

## [v2.15.19-pre.7] - 2026-07-20

### Added

-

## [v2.15.19-pre.5] - 2026-07-19

### Added

-

## [v2.15.19-pre.4] - 2026-07-17

### Added

-

## [v2.15.19-pre.3] - 2026-07-17

### Added

-

## [v2.15.19-pre.2] - 2026-07-17

### Added

-

## [v2.15.19-pre.1] - 2026-07-16

### Added

-

## [v2.15.18] - 2026-07-16

### Added

-

## [v2.15.18-pre.12] - 2026-07-16

### Added

-

## [v2.15.18-pre.11] - 2026-07-16

### Added

-

## [v2.15.16-pre.11] - 2026-07-14

### Fixed

- **PowerShell 5.1 Compatibility**: Set `$ErrorActionPreference = "Continue"` in the E2E verification script to prevent fatal `NativeCommandError` exceptions on Windows PowerShell 5.1 when LDM or Docker writes to stderr.
- **PowerShell String Parsing**: Fixed a variable reference parse error in the verification script where `$LASTEXITCODE:` was incorrectly interpreted as a drive reference; replaced with `${LASTEXITCODE}:`.
- **Windows Port Checks**: Updated `infra-setup` to check wildcard IP `0.0.0.0` for port availability on Windows, resolving false-positive port conflict errors when Docker Desktop binds on all interfaces.

## [v2.15.16-pre.10] - 2026-07-13

### Added

- **`ldm link` Subcommand** (Issue #547): Introduced `ldm link` as the new canonical command for linking a local Liferay Workspace to an LDM project. The previous `ldm init-from` command is now a supported legacy alias.
- **`ldm clone` Subcommand** (Issue #548): Introduced `ldm clone` for cloning a remote Git repository and initializing an LDM project in one step. `ldm import` is now restricted to `.ldmp` data packages only.
- **Post-Upgrade Release Notes Banner** (Issue #550): After a successful `ldm system upgrade`, LDM now displays a "What's New" banner summarising key changes in the new version on the next command run.

### Fixed

- **SSH Git URL Compatibility**: Resolved a URL parsing issue where SSH-style Git URLs (e.g., `git@github.com:org/repo.git`) were incorrectly rejected during `ldm clone`.
- **Non-TTY Banner Check**: The post-upgrade banner is now suppressed in non-interactive (headless) environments to avoid corrupting CI output.
- **Windows Path Resolution Safety**: Fixed edge cases where Windows-style path separators caused workspace path resolution failures.

## [v2.15.16-pre.9] - 2026-07-13

### Fixed

- **Snapshot Failure Handling**: `ldm snapshot` and `ldm package` now cleanly fail and remove partial/empty output files when the underlying database dump command exits with a non-zero code, preventing corrupted snapshots from being silently retained.

## [v2.15.16-pre.8] - 2026-07-13

### Fixed

- **E2E PowerShell Cleanup**: Wrapped all E2E verification script cleanup steps in a dedicated `Invoke-Cleanup` helper function to prevent native command exceptions from aborting cleanup on Windows.

## [v2.15.16-pre.7] - 2026-07-13

### Fixed

- **Windows File Locking Typo**: Corrected a `msvcrt` locking mode attribute typo (`LK_NBND` → `LK_NBLCK`) that caused intermittent file lock failures on Windows during concurrent operations.
- **E2E Script Encoding**: Saved `verify_e2e_refactor.ps1` with UTF-8 BOM encoding to ensure correct parsing by Windows PowerShell 5.1.

## [v2.15.16-pre.6] - 2026-07-13

### Fixed

- **WSL Deadlock Resolution**: Added a configurable `run_command` timeout and resolved WSL-specific process deadlocks caused by blocking stdin reads in subprocess calls.
- **PowerShell 5.1 Compatibility**: Disabled `$PSNativeCommandUseErrorActionPreference` in the E2E verification script for Windows PowerShell 5.1 compatibility.

## [v2.15.16-pre.5] - 2026-07-13

### Fixed

- **Database Data Loss Prevention**: Fixed a critical vulnerability where stopping a project while a database volume snapshot was in progress could result in partial or corrupted data. LDM now coordinates a safe shutdown before any volume dehydration.

### Added

- **`ldm db start` / `ldm start` Alias**: Added `ldm db start` to start only the database container without booting the full Liferay stack. The bare `ldm start` keyword is also routed as an alias.
- **E2E Property Override Cascade Checks**: Extended the E2E verification suite to validate the 5-layer properties override cascade, `--reset-properties`, and `ldm db query` behaviours.

## [v2.15.16-pre.4] - 2026-07-10

### Added

- **Extended E2E Test Coverage**: Added unit and E2E tests for log export, `ldm wait` milestone reporting, and trace log (`last-command.log`) integration.

## [v2.15.16-pre.3] - 2026-07-10

### Changed

- **Dependency Bumps**: Updated `psutil`, `keyring`, `mypy`, `ruff`, and `mkdocs-material` to latest compatible versions. Updated GitHub Actions dependencies.

## [v2.15.16-pre.2] - 2026-07-10

### Added

- **Improved Wait UX and Milestone Reporting**: `ldm wait` now displays progressive milestone markers (Container Health → OSGi Ready → HTTP Ready) with elapsed time, giving clear feedback during long Liferay startup sequences.

## [v2.15.16-pre.1] - 2026-07-10

### Added

- **Global `last-command.log` Trace File**: LDM now writes a persistent `last-command.log` trace file capturing the full output of the most recent command. This file is included in diagnostic bundles (`ldm system doctor --bundle`) to aid support and debugging.

## [v2.15.15] - 2026-07-08

### Added

-

## [v2.15.14] - 2026-07-08

### Added

-

## [v2.15.13] - 2026-07-08

### Added

-

## [v2.15.12] - 2026-07-08

### Added

-

## [v2.15.11] - 2026-07-08

### Added

-

## [v2.15.10] - 2026-07-08

### Added

-

## [v2.15.9] - 2026-07-08

### Added

-

## [v2.15.8] - 2026-07-08

### Added

-

## [v2.15.7] - 2026-07-08

### Added

-

## [v2.15.6] - 2026-07-08

### Added

-

## [v2.15.5] - 2026-07-08

### Added

-

## [v2.15.4] - 2026-07-07

### Added

-

## [v2.15.3] - 2026-07-07

### Added

-

## [v2.15.2] - 2026-07-07

### Added

-

## [v2.15.1] - 2026-07-07

### Added

-

## [v2.15.0] - 2026-07-07

### Added

-

## [v2.14.4] - 2026-07-07

### Added

-

## [v2.14.3] - 2026-07-07

### Added

-

## [v2.14.2] - 2026-07-07

### Added

-

## [v2.14.1] - 2026-07-07

### Added

-

## [v2.14.0] - 2026-07-06

### Added

-

## [v2.13.0] - 2026-07-06

### Added

-

## [v2.12.2] - 2026-07-06

### Added

-

## [v2.12.1] - 2026-07-06

### Added

-

## [v2.12.0] - 2026-07-06

### Added

-

## [v2.11.85] - 2026-07-03

### Added

-

## [v2.11.84] - 2026-07-03

### Added

-

## [v2.11.83] - 2026-07-03

### Added

-

## [v2.11.82] - 2026-07-03

### Added

-

## [v2.11.81] - 2026-07-02

### Added

-

## [v2.11.80] - 2026-07-02

### Added

-

## [v2.11.79] - 2026-07-02

### Added

-

## [v2.11.78] - 2026-07-02

### Added

-

## [v2.11.77] - 2026-07-02

### Added

-

## [v2.11.76] - 2026-07-02

### Added

-

## [v2.11.74] - 2026-07-01

### Added

-

## [v2.11.73] - 2026-07-01

### Added

-

## [v2.11.72] - 2026-07-01

### Added

-

## [v2.11.71] - 2026-07-01

### Added

-

## [v2.11.70] - 2026-07-01

### Added

-

## [v2.11.69-pre.4] - 2026-06-30

### Added

-

## [v2.11.69-pre.3] - 2026-06-30

### Added

- Added Shared Database Mode (`use_shared_db`) supporting `--global` setting to host multiple projects on a single database container.
- Throttled database connection pool sizes (`maxActive=15`, `minIdle=2`, `maxIdle=5`) to lower default stack RAM footprints.
- Capped Elasticsearch memory allocation (512MB heap limit) and constrained CPU threads (`processors=1`).
- Implemented SHA256-based smart cache volume hydration to bypass redundant document library asset extractions.
- Added comprehensive technical walkthrough documentation in `docs/explanation/faq.md` covering resource optimization, pre-baked seed packages, and sharing tunnels.

### Fixed

- Resolved missing `manager` attribute bug in `ldm fix-hosts` command.

## [v2.11.68] - 2026-06-29

### Added

-

## [v2.11.67] - 2026-06-29

### Added

-

## [v2.11.66] - 2026-06-29

### Added

-

## [v2.11.65] - 2026-06-29

### Added

-

## [v2.11.64] - 2026-06-29

### Added

-

## [v2.11.63] - 2026-06-29

### Added

-

## [v2.11.62] - 2026-06-29

### Added

-

## [v2.11.61] - 2026-06-29

### Added

-

## [v2.11.60] - 2026-06-29

### Added

-

## [v2.11.59] - 2026-06-26

### Added

-

## [v2.11.58] - 2026-06-26

### Added

-

## [v2.11.57] - 2026-06-26

### Added

-

## [v2.11.56] - 2026-06-26

### Added

-

## [v2.11.55] - 2026-06-25

### Added

-

## [v2.11.54] - 2026-06-25

### Added

-

## [v2.11.53] - 2026-06-25

### Added

-

## [v2.11.52] - 2026-06-24

### Added

- Added support for immediate search reindexing on running containers via OSGi Gogo telnet command.

## [v2.11.51] - 2026-06-24

### Added

-

## [v2.11.50] - 2026-06-24

### Added

-

## [v2.11.49] - 2026-06-24

### Added

-

## [v2.11.48] - 2026-06-24

### Added

-

## [v2.11.47] - 2026-06-24

### Added

-

## [v2.11.46] - 2026-06-24

### Added

-

## [v2.11.45] - 2026-06-24

### Added

-

## [v2.11.44] - 2026-06-24

### Added

-

## [v2.11.43] - 2026-06-24

### Added

-

## [v2.11.42] - 2026-06-24

### Added

-

## [v2.11.41] - 2026-06-23

### Added

-

## [v2.11.40] - 2026-06-23

### Added

-

## [v2.11.39] - 2026-06-23

### Added

-

## [v2.11.38] - 2026-06-23

### Added

-

## [v2.11.37] - 2026-06-23

### Added

- Added automated resolution for project registry collisions, including auto-cleaning stale paths, interactive prompts, and the `--overwrite-registry` CLI flag.

## [v2.11.35] - 2026-06-23

### Added

- Added `--auto-install-lfr-tunnel` command-line argument and `lfr_tunnel_bin` / `lfr_tunnel_install_cmd` settings to configure custom paths or commands for the `lfr-tunnel` binary.
- Added mapping and propagation of global configuration preferred admin user details (e.g. `admin_password`, `admin_first_name`, etc.) directly into target `portal-ext.properties`.

### Fixed

- Fixed `--tag-latest` and `--tag-prefix` to correctly override the project's locally stored metadata tag during run commands.
- Fixed subprocess invocation to use `encoding="utf-8"`, preventing `UnicodeDecodeError` exceptions on Windows environments under non-UTF-8 locale encodings.

## [v2.11.34] - 2026-06-23

### Added

- Added `--leave-running` option for the `import` command to keep the running project active and abort the import cleanly.

### Changed

- Automatically stop running projects in non-interactive/yes (`-y`) mode during `import` commands.

## [v2.11.33] - 2026-06-23

### Added

- Added warning check when running LDM commands directly from the Home directory (CWD is `~`) to prevent folder clutter.
- Documented database container online requirements for `.ldmp` package exports (with AICA CI case study details in `DATA_MANAGEMENT.md`).
- Consolidated `--stop-running` flag support from the pre-release.

## [v2.11.32] - 2026-06-23

### Added

- Skipped due to pre-squash release tag mismatch.

## [v2.11.32-pre.1] - 2026-06-23

### Added

- Added `--stop-running` flag to `ldm import` command to automatically stop a running instance.

## [v2.11.31] - 2026-06-22

### Added

- Support for custom quickstart templates overrides via `~/.ldm_templates.json`.
- Automatic tunnel sharing exposure under `ldm import` when the `--share` flag is passed.
- Robust unit test coverage for package commands and template overrides.

### Fixed

- Typo in workspace quickstart test patching (`AssetsService` -> `AssetService`).
- Mock manager sharing helper verification in unit tests.
- Standalone package export command (`ldm package`) snapshot listing and dynamic test path assertions.

## [v2.11.21] - 2026-06-20

### Added

-

## [v2.11.20] - 2026-06-20

### Added

- Added explicit container naming (e.g. `[project_name]-lfr-tunnel`) for the `lfr-tunnel-docker` sidecar service to improve clean container teardown.
- Persisted `tunnel_container_name` in project metadata and included it in the `ldm status` diagnostics display.

## [v2.11.19] - 2026-06-20

### Added

- Added automatic Liferay portal proxy configuration (`web.server.host`, `web.server.https.port`, and `web.server.protocol` in `portal-ext.properties`) when sharing a project via standard tunnel.

### Fixed

- Added automatic cleanup of previous `portal-ext.properties` proxy/tunnel overrides when neither sharing nor SSL proxy is active.

## [v2.11.18] - 2026-06-20

### Added

- Added `--share-inspector` option to `ldm run` command.
- Added `--inspector` option to `ldm share start` subcommand.

### Changed

- Made the tunnel inspector dashboard opt-in and bound it to `127.0.0.1` inside the container by default (no exposed port `4040` unless opted in).
- Cleaned the local host-side `.env` configuration files of the `LFT_INSPECTOR_BIND` setting.

## [v2.11.17] - 2026-06-19

### Added

- Mapped port `4040` for the `lfr-tunnel` container sidecar to allow host machine access to the web inspector dashboard.
- Added support and automatic `.env` initialization for `LFT_INSPECTOR_BIND` binding address overrides.

## [v2.11.16] - 2026-06-19

### Added

- Added support for resolving and printing public tunnel URLs when using share/expose providers.
- Added support for `.env` overrides (`LFT_SUBDOMAIN`, `LFT_CLIENT_TOKEN`, and `LFT_SERVER_URL`) for the tunnel container.

## [v2.11.15] - 2026-06-19

### Added

- **Custom share image flags**: Added `--share-image` (to `ldm run`) and `--image` (to `ldm share start`) CLI flags to specify custom tunnel Docker image sources.

### Fixed

- **lfr-tunnel Docker image namespace**: Updated default sidecar namespace from `peterrichards` to `peterjrichards` to match the official container repository.

## [v2.11.14] - 2026-06-19

### Added

- **Integrated lfr-tunnel-docker Compose Service**: Added support for running the containerized `lfr-tunnel` client as a service sidecar directly inside the generated `docker-compose.yml` stack.
- **EDR & SentinelOne Bypass**: Encapsulating the client inside the Docker runtime space prevents host-native Go binary blockages.
- **Resource Optimization**: Imposed minimal CPU (`0.10` limits, `0.05` reservations) and memory (`50M` limits, `20M` reservations) constraints on the sidecar service.
- **Host Header & Redirection**: Directly routes external subdomain traffic internally to Tomcat at `http://liferay:8080`, facilitating correct absolute URL redirects.

## [v2.11.9] - 2026-06-15

### Fixed

- Resolved LDM self-upgrade failures due to unauthenticated GitHub API rate limiting by implementing a fallback check via HTML redirect.

## [v2.11.5] - 2026-06-10

### Added

- **lfr-tunnel Integration**: Integrated `lfr-tunnel` host-side Go client for wildcard subdomain routing (`*.lfr-demo.se` and `*.lfr-demo.online`).
- **Unified Tunnel Provider Namespace**: Added `ldm share` subcommands (`start`, `status`, `stop`) to manage sharing tunnels under a single interface.
- **Automated Container Sharing**: Integrated `--share`, `--share-subdomain`, and `--share-provider` flags into `ldm run` to automatically boot the sharing tunnel once Liferay is healthy.
- **Expose Legacy Support**: Mapped the legacy `--expose` flag as a backward-compatible alias for `--share --share-provider ngrok`.

## [v2.11.4] - 2026-06-10

### Added

- **Directory Deletion Safety Validator**: Integrated JIT validation in `safe_rmtree` to prevent accidental deletion of git repository roots, system directories, CWD, user home directories, and LDM source files.
- **Ngrok Tunneling Integration**: Embedded Ngrok tunneling directly into the local LDM stack to facilitate seamless remote testing of client extensions (#27).
- **Secrets Prevention Scanner**: Configured pre-commit secrets detection with Yelp's `detect-secrets` hook and baseline configurations (#36).
- **OSGi State Persistence**: Added support for persisting OSGi state across container lifecycles (#28).

### Fixed

- **CWD FileNotFoundError Bug**: Resolved a CLI crash when running commands (like `ldm list`) inside a directory that has been deleted.
- **CSP Compliance**: Removed inline styles and `<style>` tags from the developer dashboard to prevent Content Security Policy violations (#37).

## [v2.11.4-pre.1] - 2026-06-04

### Added

-

## [v2.11.3] - 2026-06-04

### Added

- **Video Showcase**: Added a new video showcase to the documentation (`docs/showcase/`) featuring HTML5 video demonstrations of Fast Provisioning, Cloud Hydration, and Snapshots & Restoration with full text transcripts for SEO and accessibility (Fixes #21).

### Fixed

- **Port Allocation Conflict**: Resolved a bug in the proxy infrastructure where fallback logic could mistakenly assign the same available port (e.g., 1024) to multiple services (like HTTP and HTTPS) if the host's ports were already occupied and the first fallback port had not yet been bound (Fixes #21).

## [v2.11.2] - 2026-06-03

### Fixed

- **Windows PowerShell Input Prompt Hang**: Refactored `UI.ask` to use native `input(prompt)` on Windows (`sys.platform == "win32"`) with a clean ASCII-only fallback prompt, preventing console host queue blocking and allowing user inputs and Ctrl+C abort sequences to process correctly in PowerShell and cmd.exe.

## [v2.11.1] - 2026-06-03

### Fixed

- **Windows Console Unicode Output**: Added `isinstance(str)` guard to `UI._print` encoding pre-check to prevent `TypeError` when stdout is mocked, and ensures emoji symbols (e.g. `❌`) fall back to ASCII equivalents (`[X]`) on consoles that cannot encode UTF-8.
- **`system fix-hosts` CLI Registration**: Registered the missing `fix-hosts` subcommand under `system_subparsers` in the CLI parser. Previously, `ldm system fix-hosts --help` caused argparse to exit with code 2, failing the WSL2 E2E Sudo Guard verification.
- **SIGPIPE Broken Pipe Traceback**: Restored the default OS-level `SIGPIPE` signal handler (`SIG_DFL`) on Unix/macOS at startup. Python's override was causing `BrokenPipeError: [Errno 32] Broken pipe` tracebacks when `ldm` output was piped to tools like `grep -q` that exit early.
- **Pre-commit Hook Python Resolution**: Added `scripts/run_python.sh` portable resolver so pre-commit hooks use `.venv/bin/python3` when available (local dev) and fall back to system `python3` (CI), preventing hook failures caused by Homebrew Python taking priority on macOS.

## [v2.11.0] - 2026-06-03

### Added

- Proactive remote Liferay tag validation against the releases.json endpoint.
- Automated cleanup of remote pre-release tags upon pull request merge.

## [v2.11.0-pre.2] - 2026-06-03

### Added

- Documented branching and tagging rules in CONTRIBUTING.md.

## [v2.11.0-pre.1] - 2026-06-02

### Added

- **CLI Namespacing**: Restructured flat commands into logical namespaces (`ldm infra`, `ldm cloud`, `ldm config`, `ldm system`) for improved discoverability. All legacy flat commands remain fully supported as transparent aliases (e.g. `ldm prune` → `ldm system prune`).
- **`--open` switch on `ldm run`**: Automatically launches the project URL in your system browser after startup completes.
- **`--scale` switch on `ldm run`**: Boot a scaled multi-replica stack in a single command (e.g. `ldm run demo --scale liferay=2`), bypassing the separate `ldm scale` step.
- **`ldm logs --instance N` / `-i N`**: Target a specific replica of a scaled service directly (e.g. `ldm logs demo liferay --instance 2`). Routes to `docker logs` for exact container targeting.
- **Container naming pattern in metadata**: `ldm scale` now persists `container_name_pattern_{service}` to project metadata, enabling O(1) replica name resolution without a `docker ps` lookup.
- **Updated man page and CLI reference**: Full documentation of all namespaced commands, new switches, backward-compatibility table, and `--instance` usage guide.

## [v2.10.27] - 2026-06-02

### Changed

- Moved fine-grained inner-loop file sync, monitoring, archiving, and database wiping logs to `UI.detail` (visible only via `--verbose`/`-v`) to streamline CLI Developer Experience (DX).

## [v2.10.26] - 2026-06-02

### Added

- Added a dedicated third-party tool dependencies guide ([THIRD_PARTY_TOOLS.md](./docs/reference/third_party_tools.md)) detailing mandatory/optional status, purposes, and impacts of missing dependencies.

### Changed

- Deprecated legacy `nc`/`ncat` (netcat/nmap) diagnostic checks and retired related warnings and installation instructions, as log-level sync is now handled natively via Log4j2 file hot-reloading.

## [v2.10.25] - 2026-06-02

### Added

- **Liferay Cloud Golden Path Hydration**: Automated database extraction, flattening, SQL scrubbing, and volume synchronization.
- **PostgreSQL Restoration Hardening**: Complete wipe mechanism including Large Objects and UNIX socket retries, and high-performance streaming database import.
- **Self-Tuning JVM & Search Indexing**: Automated code cache and heap scaling during reindexing, and real-time indexing progress spinner.
- **Smart Store Detection**: Automatically detects and switches between FileSystemStore and AdvancedFileSystemStore configurations.
- **Smart Volume Naming**: Bypasses Docker Compose prefixing lag and naming mismatches via explicit Named Volume properties.
- **CI/CD Hardening**: Configured shellcheck using system binary to ensure stable GitHub Action runs.
- **Local Dev Hardening**: Added `/modern-intranet/` to gitignore to prevent accidental credential and database leaks.

## [v2.10.24] - 2026-06-01

### Added

-

## [v2.10.23] - 2026-06-01

### Added

-

## [v2.10.22] - 2026-06-01

### Added

-

## [v2.10.21] - 2026-06-01

### Added

-

## [v2.10.20] - 2026-06-01

### Added

-

## [v2.10.19] - 2026-06-01

### Added

-

## [v2.10.18] - 2026-06-01

### Added

-

## [v2.10.17] - 2026-06-01

### Added

-

## [v2.10.16] - 2026-06-01

### Added

-

## [v2.10.15] - 2026-06-01

### Added

-

## [v2.10.14] - 2026-06-01

### Added

-

## [v2.10.13] - 2026-06-01

### Added

-

## [v2.10.12] - 2026-06-01

### Added

-

## [v2.10.11] - 2026-06-01

### Added

-

## [v2.10.10] - 2026-06-01

### Added

-

## [v2.10.9] - 2026-06-01

### Added

-

## [v2.10.8] - 2026-06-01

### Added

-

## [v2.10.7] - 2026-06-01

### Added

-

## [v2.10.6] - 2026-06-01

### Added

-

## [v2.10.5] - 2026-06-01

### Added

-

## [v2.10.4] - 2026-06-01

### Added

-

## [v2.10.3] - 2026-06-01

### Added

-

## [v2.10.2] - 2026-06-01

### Added

-

## [v2.10.1] - 2026-06-01

### Added

-

## [v2.10.0] - 2026-05-31

### Added

-

## [v2.9.9] - 2026-05-31

### Added

-

## [v2.9.8] - 2026-05-31

### Added

-

## [v2.9.7] - 2026-05-31

### Added

-

## [v2.9.6] - 2026-05-31

### Added

-

## [v2.9.5] - 2026-05-31

### Added

-

## [v2.9.4] - 2026-05-31

### Added

-

## [v2.9.3] - 2026-05-31

### Added

-

## [v2.9.2] - 2026-05-31

### Added

-

## [v2.9.1] - 2026-05-31

### Added

-

## [v2.9.0] - 2026-05-31

### Added

-

## [v2.8.39] - 2026-05-29

### Added

-

## [v2.8.38] - 2026-05-28

### Added

-

## [v2.8.37] - 2026-05-28

### Added

-

## [v2.8.36] - 2026-05-27

### Added

-

## [v2.8.35] - 2026-05-27

### Added

-

## [v2.8.34] - 2026-05-27

### Added

-

## [v2.8.33] - 2026-05-27

### Added

-

## [v2.8.32] - 2026-05-27

### Added

-

## [v2.8.31] - 2026-05-27

### Added

-

## [v2.8.30] - 2026-05-27

### Added

-

## [v2.8.29] - 2026-05-27

### Added

-

## [v2.8.28] - 2026-05-27

### Added

-

## [v2.8.27] - 2026-05-27

### Added

-

## [v2.8.26] - 2026-05-27

### Added

-

## [v2.8.25] - 2026-05-27

### Added

-

## [v2.8.24] - 2026-05-27

### Added

-

## [v2.8.23] - 2026-05-27

### Added

-

## [v2.8.22] - 2026-05-27

### Added

-

## [v2.8.21] - 2026-05-27

### Added

-

## [v2.8.20] - 2026-05-27

### Added

-

## [v2.8.19] - 2026-05-27

### Added

-

## [v2.8.18] - 2026-05-27

### Added

-

## [2.7.28] - 2026-05-21

### Fixed

- **Port Mapping Verification**: Fixed a hardcoded port issue in the terminal output where LDM would always report `http://localhost:8080` for local access, even when a custom `--port` (like `8082` in E2E tests) was configured.

## [2.7.27] - 2026-05-21

### Fixed

- **E2E Script Adjustments**: Updated the verification script to skip the behavioral sudo guard check in CI, matching LDM's CI root allowance.
- **Workflow Dependencies**: Added `which` to the Fedora verification job to support script-level path discovery.

## [2.7.26] - 2026-05-21

### Fixed

- **CI Test Stability**: Updated security policy tests to correctly account for the `GITHUB_ACTIONS` environment, fixing a false-positive failure in CI.
- **Workflow Resilience**: Hardened system dependency installation in the verification workflow. Now uses `python3-full` and `hostname` to ensure all platforms have necessary tools for automated testing.

## [2.7.25] - 2026-05-21

### Fixed

- **CI Root Execution**: LDM now automatically allows root execution when `GITHUB_ACTIONS=true` is detected. This prevents security warnings from corrupting output slugs in CI environments.
- **Workflow Dependencies**: Added missing system packages (`python3-venv`, `hostname`) to the new verification workflow to ensure smooth execution on Fedora and Ubuntu runners.

## [2.7.24] - 2026-05-21

### Fixed

- **JVM Argument Deduplication**: Fixed a critical bug in the JVM options parser that caused `-Xms` (initial heap) and `-Xmx` (max heap) to collide, resulting in JVM initialization failures in resource-constrained environments.

## [2.7.23] - 2026-05-21

### Added

- **Lean JVM Profile**: Introduced a resource-optimized JVM profile (2GB heap) for constrained environments.
- **Automatic CI Optimization**: LDM now automatically switches to the Lean profile when it detects a GitHub Actions environment, ensuring reliable Liferay boots on standard 7GB runners.
- **`--lean` Flag**: Added a manual flag to `run` and `import` for users on low-memory local machines.

## [2.7.22] - 2026-05-21

### Fixed

- **Intelligent `ldm env`**: Fixed a discrepancy where hitting "Enter" at the environment prompt would save but not apply shell variables.
- **Unattended Environment Sync**: Enabled `ldm env <project> -y` to automatically synchronize all passthrough shell variables (e.g. AI keys) without requiring explicit arguments, significantly easing CI automation.

## [2.7.21] - 2026-05-21

### Fixed

- **Polished DNS Output**: Cleaned up the pre-flight hostname verification to suppress redundant manual fix suggestions when multiple subdomains are missing. LDM now provides a concise, unified summary before offering the automated `/etc/hosts` fix.

## [2.7.20] - 2026-05-21

### Fixed

- **Polished Upgrade Errors**: Improved the error handling in `ldm upgrade` to provide user-friendly messages when elevated privilege requests fail, replacing raw command strings with actionable advice.

## [2.7.19] - 2026-05-21

### Added

- **Comprehensive Project Rollback**: Enhanced the atomic initialization logic to ensure failed brand-new projects are not only deleted from disk but also removed from the global registry.
- **Improved Workspace Cleanup**: `ldm import` now proactively cleans up the temporary extraction directory and its parent `.ldm_temp` folder on completion or failure.

## [2.7.18] - 2026-05-21

### Added

- **Atomic Project Initialization**: Implemented a "Commit/Rollback" pattern for `ldm run` and `ldm import`. If a brand-new project fails to initialize (e.g., due to DNS errors or build failures), LDM now automatically cleans up the half-baked directory to prevent inconsistent project states.
- Enhanced `ldm import` with the shared **Intelligent Subdomain Fixing** engine, ensuring consistency with the `run` command.

## [2.7.17] - 2026-05-21

### Fixed

- Fixed `ldm fix-hosts` to correctly fallback to treating the target as a direct hostname if no matching project is found.
- Hardened `_apply_hosts_fix` to prevent adding empty configuration blocks to `/etc/hosts`.
- Improved `check_hostname` to verify that domains resolve to local IPs, preventing false-positive resolution reports for remote addresses.

## [2.7.16] - 2026-05-21

### Fixed

- Fixed `ldm scale` command failing in non-interactive environments when the project was already running.

## [2.7.15] - 2026-05-21

### Documentation

- Formally documented the **Environment Variable Forwarding** logic in `README.md`, covering `LDM_` prefix stripping, automatic AI passthrough, and service-specific targeting.
- Documented the **Automatic Volume Hardening** behavior for macOS external drives.

## [2.7.14] - 2026-05-21

### Changed

- **Refactored Environment Forwarding**: Consolidated global and service-specific variable logic. LDM now supports:
  - **`LDM_` Strip-Forwarding**: Global variables (e.g. `LDM_VAR=xxx`) are forwarded to all containers with the prefix removed (`VAR=xxx`).
  - **Unified Passthrough**: Liferay Cloud and AI provider keys (`OPENAI_`, `GEMINI_`, etc.) are automatically forwarded as-is.
  - **Custom Passthrough**: Users can now extend the passthrough list by setting `LDM_FORWARD_PREFIXES` (comma-separated) on the host.

## [2.7.13] - 2026-05-21

### Added

- **Flexible `ldm deploy`**: Now accepts optional specific services or file paths (JAR, WAR, ZIP). ZIP files are intelligently handled as either Client Extensions or OSGi Fragments.
- **Dedicated `ldm wait` Command**: Standardized way to block scripts until Liferay is responding to HTTP requests.
- **Automatic Volume Hardening**: Proactively detects macOS `/Volumes/` paths and enables `--internal-state` for reliable OSGi file locking.
- **Automatic AI Environment Forwarding**: Host variables starting with `OPENAI_`, `GEMINI_`, `ANTHROPIC_`, or `MISTRAL_` are now automatically available inside all project containers.
- **Automatic Non-Interactive Hosts Fix**: When running with `-y`, LDM now attempts to fix missing `/etc/hosts` entries automatically using `sudo -n`.
- **Smarter Subdomain Fixing**: `ldm fix-hosts` and pre-flight checks now scan for active client extensions and include their required subdomains in the resolution check.

### Fixed

- **AutoDeployScanner Permissions**: Resolved "Unable to write" errors on Linux by ensuring the `osgi/` directory tree is included in proactive permission reclamation.
- **Zero-Race Atomic Deployment**: All file deployments now use hidden staging files with proactive permission fixups before the final atomic rename.
- **Developer Guardrails**: Pass-through for `-y/--non-interactive` to bypass internal developer mode prompts while maintaining protection for unattended CI environments.

## [2.7.11] - 2026-05-21

### Added

- **Smarter Subdomain Fixing**: Enhanced `ldm fix-hosts` and pre-flight checks to automatically identify and fix missing client extension subdomains. LDM now scans projects for active extensions and ensures every required URL resolves to 127.0.0.1 before starting.
- Updated documentation to clarify that LDM handles the entire project DNS tree (main host + wildcards/subdomains) automatically.

## [2.7.10] - 2026-05-21

### Fixed

- Implemented universal permission fixup (`chmod 666` and `chown 1000:1000`) for all file deployment operations on Unix. This definitively resolves the "Unable to write" errors in Liferay's `AutoDeployScanner` when LDM is running as root (e.g. in CI or with sudo).

## [2.7.9] - 2026-05-21

### Fixed

- Enabled true non-interactive execution for commands requiring elevation (`fix-hosts`, `upgrade`) on Linux and macOS. By passing `-y/--non-interactive`, LDM now uses `sudo -n` to suppress password prompts and fail fast if a password is required.

## [2.7.8] - 2026-05-21

### Documentation

- Formally documented the **Client Extension Routing & Wildcard SSL** logic in `README.md`.
- Reorganized the `README.md` to move the `LDM_COMMON_DIR` section under "Configuration Files" for better logical flow.

## [2.7.7] - 2026-05-21

### Documentation

- Formally documented the **Zero-Race Atomic Deployment Strategy** in `docs/LDM_ARCHITECTURE.md`, detailing the staging and permission fixup pattern.

## [2.7.6] - 2026-05-21

### Fixed

- Hardened atomic deployment logic by ensuring Unix permission fixups occur on hidden staging files *before* they are moved into Liferay's scanner path. This eliminates the race condition where `AutoDeployScanner` could see a file before its ownership was handed off to the `liferay` user.

## [2.7.4] - 2026-05-21

### Fixed

- Fixed permission errors (`Unable to write [file.zip]`) in `AutoDeployScanner` on Linux host environments (e.g. GitHub Actions) by ensuring the `osgi` directory is targeted during proactive volume permission reclamation.

## [2.7.3] - 2026-05-21

### Fixed

- Fixed port check conflict where `ldm import` would start a project and `ldm run` would fail.
- Added runtime state-awareness checks to commands (run, import) to prevent unexpected container collisions.
- Enabled non-interactive bypass for internal developer utility prompts.

## [v2.7.2-beta.40] - 2026-05-18

### Fixed

- **Enhanced Readiness Detection**: Updated `ldm run` and E2E scripts to monitor Liferay logs for the Tomcat "Server startup" marker. This provides a faster and more reliable signal that Liferay is ready for access, especially in CI environments where the Docker healthcheck status may be significantly delayed.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-08-04* | *Last Reviewed: 2026-07-09*
