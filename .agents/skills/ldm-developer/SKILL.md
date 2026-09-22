---
name: ldm-developer
description: Guides and scripts for developing, testing, linting, and releasing Liferay Docker Manager (LDM).
---

# LDM Developer Skill & Runbook

This skill guides you through the standards, commands, and scripts required to develop, verify, and release updates in the Liferay Docker Manager (LDM) codebase.

---

## 1. Environment Constraints

- **Virtual Environment Gate**: You MUST conduct all development, testing, linting, and Git operations within the project's Python virtual environment (`.venv`).

- **Hook Isolation**: Git hooks and pre-commit checks rely on packages installed in the virtual environment. Running operations outside the virtual environment (e.g. using global system Python) will trigger hook failures.

### Environment Layout

`.venv` is **authoritative** for everything that actually runs: `scripts/run_python.sh` (the resolver behind every local pre-commit hook) prefers `.venv/bin/python3`, `lint.sh` resolves `.venv`, and `scripts/agent_push.sh` uses `.venv/bin/python3 -m pre_commit`. `.pytest_venv`, `.smoke_venv` and `.temp_venv` are auxiliary environments created by tooling; do not reach for them to work around a missing command (see below for why that is a trap).

> [!IMPORTANT]
> **Always invoke Python tooling as a module, never via its console script.**
>
> ```bash
> .venv/bin/python3 -m pre_commit run --all-files   # correct
> .venv/bin/pre-commit run --all-files              # fails: no such file or directory
> ```
>
> The packages are installed and fully functional — it is the generated `.venv/bin/<tool>` wrapper scripts that are **not reliably present on developer machines**. The module form invokes the installed package directly and is immune to this, which is why `agent_push.sh` has kept working while documented console-script commands broke ([#1240](https://github.com/peterrichards-lr/liferay-docker-manager/issues/1240)).

#### Why the console scripts disappear

This is **not** a packaging quirk or an incomplete install — the wrappers were created and later removed. Endpoint protection (SentinelOne) deletes them by name when it terminates a Python process tree, and it targets the canonical tool names while leaving byte-identical aliases alone:

| Package | Deleted | Survived | Identical content? |
|---|---|---|---|
| `pytest` | `pytest` | `py.test` | Yes — same SHA-256, both 229 bytes |
| `pip` | `pip` | `pip3`, `pip3.14` | Yes — same SHA-256, both 219 bytes |

Each package's `dist-info/RECORD` still lists the deleted file, which is how to confirm this rather than guess. Scripts observed removed from `.venv/bin`: `bandit`, `detect-secrets`, `detect-secrets-hook`, `mypy`, `pip`, `pre-commit`, `pysemgrep`, `pytest`, `semgrep` — overwhelmingly the security, analysis and test-runner names an EDR agent intercepts.

Two consequences worth internalising:

- **Reinstalling is not a durable fix.** The wrappers will be removed again. Use the module form.
- **`.pytest_venv` is not a safe fallback.** It has lost its own `pytest` wrapper the same way. Any instruction of the form `.pytest_venv/bin/<tool>` may break at any time for the same reason.

To audit the current damage:

```bash
# Lists every console script pip recorded as installed but which is now absent
.venv/bin/python3 - <<'EOF'
import pathlib
root = pathlib.Path(".venv"); sp = next(root.glob("lib/*/site-packages"))
for rec in sp.glob("*.dist-info/RECORD"):
    for line in rec.read_text(errors="replace").splitlines():
        p = line.split(",")[0]
        if p.startswith("../../../bin/") and not (root / "bin" / p.split("/")[-1]).exists():
            print(f"MISSING .venv/bin/{p.split('/')[-1]}  (from {rec.parent.name})")
EOF
```

#### EDR removes `git-submodule`, and it stops pre-commit provisioning hooks

The same endpoint-protection behaviour reaches outside `.venv`. `git submodule`
is the one git subcommand still shipped as a POSIX shell script rather than a
compiled builtin, and it gets deleted by name:

```text
git-sh-setup           present      git-submodule    GONE
git-mergetool          present      git-bisect       present
git-filter-branch      present      git-web--browse  present
git-request-pull       present      git-quiltimport  present
```

Measured 2026-09-22 on both installations present on the machine -- Homebrew
2.55.0 and Xcode's Apple Git-157 -- independently packaged, six months apart.
Every other shell subcommand survived in both; exactly one was missing from
each. That is selective removal by name, not a packaging variation.

**It is not a Homebrew-vs-Xcode question.** `brew reinstall git` fixes it
because reinstalling restores the deleted file, not because Homebrew's build
differs -- Xcode's git is missing the same file and fails the same way. Either
git works once the file is back, and either will lose it again.

**Why it matters here.** `pre_commit/store.py:182` runs
`git submodule update --init --recursive` whenever it clones a hook repository:

```python
git_cmd("submodule", "update", "--init", "--recursive")
```

So a missing `git-submodule` breaks hook provisioning -- but only for a repo
pre-commit has to fetch. Already-cached hook environments are untouched, which
is why the failure looks intermittent and arrives at unrelated moments. It
surfaced during LDM-#1886, which changed the `ruff` rev and so forced a fresh
clone.

**Check it before you need it**, at the start of any session that may touch
hooks, `scripts/agent_push.sh`, or a `.pre-commit-config.yaml` rev:

```bash
git submodule --help >/dev/null 2>&1 || echo "git-submodule is missing -- run: brew reinstall git"
```

Use the subcommand, not the file. `ls "$(git --exec-path)/git-submodule"`
answers for whichever git is first on `PATH`, and `git-submodule--helper` (the
compiled helper, a symlink to the `git` binary) is still present when the
script is gone -- so a `grep submodule` over that directory returns a match and
reads as healthy.

Recovery is `brew reinstall git`. Expect to repeat it.

---

## 2. Standard Developer Commands

Always run these commands from the repository root:

### Linting and Formatting

```bash
# Auto-format Python files
.venv/bin/ruff format .

# Check and auto-fix simple linting warnings
.venv/bin/ruff check . --fix
```

### Running Unit Tests

```bash
# Run the entire pytest suite (automatically gathers coverage)
.venv/bin/python -m pytest

# Run a specific test file
.venv/bin/python -m pytest ldm_core/tests/test_config.py

# Run a specific test case
.venv/bin/python -m pytest ldm_core/tests/test_config.py -k test_sync_common_assets_cascade_and_important
```

### Pre-commit Verification

```bash
# Run all pre-commit hooks across the codebase (runs Ruff, MyPy, ShellCheck, Pytest, bandit, markdownlint-cli2, etc.)
# NOTE the module form: there is no `pre-commit` console script in .venv -- see Environment Layout in section 1.
.venv/bin/python3 -m pre_commit run --all-files
```

---

## 3. Exit Code Contract

**Canonical definition: [`.agents/skills/ldm-architecture/SKILL.md`](../ldm-architecture/SKILL.md).**
Per `AGENTS.md`, exit codes are an architecture mandate and live there — do not
restate the table here. This section previously carried its own copy, which
drifted: it was missing `5` (Idempotent No-Op, shipped with
[#1094](https://github.com/peterrichards-lr/liferay-docker-manager/issues/1094))
long after that code was in use.

The one point worth repeating for day-to-day work, because it is easy to get
wrong when writing automation or E2E assertions:

> [!IMPORTANT]
> **`5` (Idempotent No-Op) is only returned in non-interactive mode.**
> `ldm run`/`ldm up` against an already-running project returns `5` *only* when
> `-y`/`--non-interactive` is in effect (`ldm_core/pipelines/run.py:371`);
> interactively it prompts to reconfigure and restart instead. Automation that
> omits `-y` will hang on a prompt rather than receive the code.

---

## 4. Releasing Updates (Automated Script)

Do not manually bump versions or tag releases. Instead, use the automated release script:

```bash
.venv/bin/python scripts/release.py --bump [patch|minor|major|beta]
```

### How the release script works

1. Verifies that the workspace only contains modified version files (`pyproject.toml`, `constants.py`, `CHANGELOG.md`) and documentation files (`.md`). **Any Python source file edits must be committed first.**

2. Bumps the SemVer version in LDM metadata configuration files.

3. Automatically runs all pre-commit quality checks.

4. Commits changes and pushes a new branch (e.g., `release/v2.11.43`).

5. Raises a Pull Request via GitHub CLI (`gh pr create`).

6. Enables auto-merge (`gh pr merge --auto`).

7. Polls until GitHub Actions builds pass and the PR merges.

8. Checks out master locally, pulls changes, tags the release (`v2.11.43`), and pushes the tag to trigger the final GitHub release workflows.

---

## 5. Documentation Surfaces

`AGENTS.md` says to review "the project documentation" after a code change.
That reads as `docs/*.md` -- the files with timestamp footers -- and two
user-facing surfaces sit outside it. Both shipped stale for four minor
releases before anyone looked (LDM-#1482).

| Surface | Path | Guarded by |
|---------|------|-----------|
| Reference docs | `docs/**/*.md` | `sync-docs`, `check-docs-review` |
| CLI reference | `docs/reference/cli/*.md`, `docs/reference/advanced_cli.md` | `check-cli-drift` (both directions) |
| **Man page** | `ldm_core/resources/ldm.1` | `check-cli-drift` (stale flags), `check-version-sync` (`.TH` version), `check-docs-review` (roff review stamp) |
| **CLI help text** | `help=` strings in `ldm_core/cli.py` | `check-cli-drift`, indirectly |

The man page is **not** a mirror of the CLI reference. It documents well under
a third of the parser's options **by design**, so an undocumented flag is not
drift there -- which is why the guard only checks the reverse direction, that
everything it *does* document still exists.

No exact ratio is recorded here, deliberately (LDM-#1842). This file carried
"roughly 42 of 238" long enough for both numbers to be wrong; LDM-#1833
corrected it to a measured 54 of 246, and documenting a few more options **in
that same change** made it 59 before it landed. A precise count in prose decays
on every edit to either side and nothing checks it, so it is a liability rather
than information. `stale_man_page_options` in `ldm_core/utils.py` carries the
one-line recipe if you need the current figure.

When adding or renaming a command or a flag that a user would reasonably look
up:

- add it to `docs/reference/cli/*.md` (enforced -- `check-cli-drift` fails on
  any parser option missing from the docs)
- consider `ldm_core/resources/ldm.1` if it belongs in a curated overview.
  It ships inside the binary, and `ldm system completion` and `ldm system man`
  refresh a symlink to it under `~/.ldm/man/man1`, so `man ldm` is a real
  surface, not a vestigial file
- never hand-edit the `.TH` version: `ldm system version --bump` stamps it,
  the same way it stamps the two verify scripts
- do hand-edit the roff review stamp at the foot of `ldm.1`
  (`.\" Last Updated: ... | Last Reviewed: ...`). `check-docs-review` holds it
  to the same 180-day limit as every `.md` document, and the bump deliberately
  does **not** touch it -- a review date moved by a release is exactly the lie
  LDM-#1833 removed

<!-- markdownlint-disable MD049 -->
---
*Last Updated: 2026-09-22* | *Last Reviewed: 2026-09-22*
