# Exit Codes

Every `ldm` command ends with one of the codes below. Scripts and CI pipelines
can branch on them: the number says what kind of failure it was, so a script
can retry an infrastructure failure and stop on a mistake in its own input.

| Code | Meaning | What a script should usually do |
|---|---|---|
| `0` | Success. | Carry on. |
| `1` | Generic or validation error: bad input, an unknown project, a missing flag, or a precondition that does not hold. | Stop. Retrying the same command will fail the same way. |
| `2` | Authentication or permission error, e.g. a Liferay Cloud login is needed, or LDM will not guess an identity it was not given. | Stop and supply the credential or identity. |
| `3` | Infrastructure or data error: the input was accepted, but the environment did not deliver, e.g. a download failed, shared search is down, or a boot never became healthy. | Fix or restart the infrastructure, then retry. |
| `4` | Orchestration or deployment error inside LDM, e.g. a port conflict detected while starting a project. | Read the message; usually a conflict to resolve before retrying. |
| `5` | Idempotent no-op: the desired state already held, so nothing was done. **Only with `-y`/`--non-interactive`.** | Treat as success if "already done" is fine for you. |
| `130` | Interrupted with `Ctrl+C` (`128 + SIGINT`, the shell convention). | Stop. |

Code `126` (command invocation error) is reserved and not currently returned.

## Code `5` needs `-y`

`5` is returned only in non-interactive mode. Run interactively, the same
command asks what to do instead, e.g. `ldm run` on a project that is already
running offers to reconfigure and restart it. A script that leaves out `-y`
gets a prompt rather than `5`, and will hang if nothing answers it. Always pass
`-y` in automation.

## Notable cases

Most commands use only `0` and `1`. These are the cases a script is most
likely to meet.

| Command | Code | When |
|---|---|---|
| `ldm run` / `ldm up` | `5` | With `-y`, the project is already running. See [Core Commands](cli/core.md). |
| `ldm run` | `3` | The project started but never became healthy within the timeout. The stack is left running, as evidence. |
| `ldm run` / `ldm wait` | `3` | A shared-search index answers no searches when the portal becomes ready. See [Data management](../how-to/data_management.md). |
| `ldm snapshot restore`, `ldm quickstart`, package `ldm import` | `3` | Shared search cannot be started or created. Nothing is reset before the refusal. See [Data management](../how-to/data_management.md). |
| `ldm snapshot restore` | `3` | A restored search index cannot answer a search. LDM names the index and does not report the restore complete. |
| `ldm infra down` / `ldm infra restart` | `3` | With `-y`, any LDM project is still running. See [System Commands](cli/system.md). |
| `ldm config revert`, `ldm config defaults --reset-all` | `5` | Nothing differed from the defaults, so nothing changed. See [System Commands](cli/system.md). |
| `ldm run`, `init`, `import`, `link`, `clone`, `quickstart`, `fork` | `1` | The project name is reserved (`ldm-infra` or `resources`, used by LDM's shared infrastructure). Nothing is created or started. See [Core Commands](cli/core.md#reserved-project-name). |
| `ldm link` (a Liferay Cloud workspace) | `2` | With `-y`, no Liferay Cloud project ID can be found. LDM refuses rather than guess. See [Core Commands](cli/core.md). |

## Not LDM's own codes

When LDM runs another program and that program fails, the code you see can
be that program's: `124` for a command that timed out (as GNU `timeout`
reports it), `127` for a command that was not found (as the shell reports it),
or the program's own exit code passed through unchanged, so its failure is
not hidden.

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-11-23* | *Last Reviewed: 2026-11-23*
