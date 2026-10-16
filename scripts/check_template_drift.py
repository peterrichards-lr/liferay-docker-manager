#!/usr/bin/env python3
"""
check_template_drift.py - Upstream Template Drift Checker

A repository bootstrapped from `ai-agent-template` inherits its AI-agent governance
files once and then never hears from the template again. Nothing pulls updates; a rule
the template corrects after a real incident stays wrong downstream indefinitely, and a
lesson learned downstream never travels back up.

`.agents/TEMPLATE_REF.md` is the deliberate manual checkpoint against that: it records
the upstream repository, the exact version/commit last compared against, and a
known-drift list whose entries link real issues. This script turns the comparison from
a chore into one command -- it clones the upstream template, reports what changed there
since the recorded commit, diffs the governed paths against the local copies, and
offers to move the "Last checked" stamp forward.

Degrading gracefully offline is a hard requirement, not a nicety. A checkout with no
network is a normal state, and a governance helper that fails the build because a DNS
lookup did not resolve would simply be disabled. When the upstream cannot be reached
the script says so and exits 0.

What does NOT degrade is a check that compares nothing. An upstream repository that is not
found or refuses access, and a governed path that does not exist upstream, will not fix
themselves on the next run; passing them as green would report "in sync" about files that
were never compared (ai-agent-template#135, from LDM's shared-files design).

An adopter may keep a governed file at another path (LDM keeps the alerts hook at
mkdocs_hooks/github_alerts.py). The "Path mapping" section of .agents/TEMPLATE_REF.md
records `local` <- `upstream` pairs, and the moved file is compared with its upstream
original instead of being reported as one file missing and another extra.

Exit codes:
  0  the check ran (with or without drift), or the upstream was unreachable (DNS, timeout,
     5xx, no network): warned, not failed
  1  configuration error: .agents/TEMPLATE_REF.md is missing or unparseable
  2  governed files differ from upstream AND --fail-on-drift was requested. Upstream commits
     since the recorded ref are reported but are not drift: only content is
  3  nothing could be compared: the upstream repository was not found or refused access, or
     a governed path does not exist upstream. Fails with or without --fail-on-drift
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, NamedTuple, Optional, Sequence, Tuple

# Allow importing sibling script helpers regardless of invocation working directory
sys.path.insert(0, str(Path(__file__).parent.resolve()))
from check_docs_review import FOOTER_REGEX, IGNORE_DIRS

# The upstream this template *is*. Recorded into every adopter's reference file at
# bootstrap time and overridable per invocation with --repo-url for forks.
TEMPLATE_REPO_URL = 'https://github.com/peterrichards-lr/ai-agent-template'

# The adopter-side checkpoint, and the tracked seed bootstrap_template.py copies into
# place. The template repository itself ships only the seed: a live reference file here
# would record this repository's drift against itself.
TEMPLATE_REFERENCE_RELPATH = Path('.agents') / 'TEMPLATE_REF.md'
TEMPLATE_REFERENCE_SEED_RELPATH = Path('.agents') / 'templates' / 'template-ref.md'

# The project-name token the seed carries is defined in bootstrap_template.py alongside
# the other bootstrap placeholders: doctor.py exempts the files that *define* placeholder
# tokens from its own placeholder scan, and this script is not one of them.

DEFAULT_UPSTREAM_REMOTE_REF = 'origin/main'
UNKNOWN_STAMP_VALUE = 'unknown'
NEVER_CHECKED_VALUE = 'never'

# The governance surface worth comparing: the agent rules themselves, the routing table
# that activates them, the seeds and subagent definitions, the enforcement scripts, and
# the CI/hook configuration that turns prose rules into gates. Adopter application code
# is deliberately out of scope. Override with --path.
GOVERNED_PATHS = (
    '.agents/skills',
    '.agents/subagents',
    '.agents/templates',
    'AGENTS.md',
    'scripts',
    'overrides/main.html',
    'overrides/partials/copyright.html',
    '.github/workflows',
    '.pre-commit-config.yaml',
)

# The docs site's shared files (ai-agent-template#135), for a docs CI job that should fail on
# them and only them. Upstream paths: the Liferay profile's stylesheet is compared through the
# path mapping bootstrap records. See .agents/skills/docs-site-standard/SKILL.md.
#
# The two override files by name, never `overrides/` as a whole: Material has ONE custom_dir,
# so a site's own extra override (a partial of its own) has nowhere else to live, and governing
# the directory would fail docs CI on a file the template does not ship.
DOCS_SITE_SHARED_PATHS = (
    'overrides/main.html',
    'overrides/partials/copyright.html',
    'scripts/mkdocs_github_alerts.py',
    'scripts/check_docs_rendered.py',
)
# Where bootstrap puts a profile's stylesheets, and where the profiles live upstream: a local
# stylesheet named like a profile's, with no mapping, is the shared file not being compared.
DOCS_PROFILE_STYLESHEET_DIR = 'docs/assets/stylesheets'
DOCS_PROFILE_DIR = '.agents/templates/docs-profiles'

# The "Path mapping" section of .agents/TEMPLATE_REF.md: one `local` <- `upstream` pair per
# line. The seed ships the placeholder line, which bootstrap removes when it adds a mapping.
PATH_MAPPING_HEADING_REGEX = re.compile(r'^## Path mapping[ \t]*$', re.MULTILINE)
PATH_MAPPING_LINE_REGEX = re.compile(
    r'^[-*][ \t]+`(?P<local>[^`]+)`[ \t]+(?:←|<-)[ \t]+`(?P<upstream>[^`]+)`[ \t]*$', re.MULTILINE)
PATH_MAPPING_NONE_LINE = '- _None recorded._'

# What to do about a red check. The instinct is to fix it in the wrong place: edit the local
# copy until it matches, or push the local edit upstream as if it were the canonical one.
DIRECTION_MESSAGE = (
    "To fix: copy the file FROM {repo_url} (the canonical copy). Never edit the local copy\n"
    "  to make this pass, and never push a local edit upstream without a PR to the template:\n"
    "  a change the shared file needs is made there first, then copied here.")

# How many hex digits of SHA-256 the mismatch evidence prints: enough to tell two versions
# apart in CI output, short enough to read.
EVIDENCE_HASH_LENGTH = 16

# git's own words for "this repository does not exist, or you may not read it". Anything else
# a failed clone says (DNS, timeouts, refused connections, 5xx) is transport, and warns. Matched
# only on git's `fatal:` / `remote:` lines: a bare "not found" also appears in harmless warnings
# (`warning: templates not found in <dir>` whenever init.templateDir points nowhere), and read
# there it turned a DNS failure into a hard "not found".
NOT_FOUND_PATTERNS = (
    'repository not found',
    "' not found",
    'does not appear to be a git repository',
    'does not exist',
    'returned error: 401',
    'returned error: 403',
    'returned error: 404',
    'permission denied',
    'authentication failed',
    'could not read username',
    'could not read password',
    'access denied',
)

# check_docs_review.IGNORE_DIRS is tuned for scanning documentation; a tree comparison
# must additionally skip artefacts that exist in a working checkout and in no git tree.
# Left in, `scripts/__pycache__/*.pyc` heads the report for every Python adopter.
COMPARISON_IGNORE_DIRS = IGNORE_DIRS | {'__pycache__', '.idea', '.vscode'}
COMPARISON_IGNORE_SUFFIXES = ('.pyc', '.pyo', '.orig', '.rej')

# Git subcommands are cheap; a hung network call is not. Bound every one of them.
DEFAULT_GIT_TIMEOUT_SECONDS = 120

# Each stanza regex ends in `[ \t]*$` rather than `\s*$`. `\s` matches newlines, so a
# greedy trailing `\s*` swallows the blank line separating the stanza from the next
# heading, and the rewritten file lands with a heading glued to the stamp (MD022).
REPO_URL_REGEX = re.compile(
    r'^\*\*Reference repo\*\*:[ \t]*<?(?P<url>[^<>\s]+)>?[ \t]*$', re.MULTILINE)
REFERENCE_STAMP_REGEX = re.compile(
    r'^\*\*Reference version at last check\*\*:[ \t]*`(?P<version>[^`]*)`[ \t]*'
    r'\((?P<ref>[^@()]*?)[ \t]*@[ \t]*`(?P<commit>[^`]*)`[ \t]*,[ \t]*(?P<date>[^)]*)\)[ \t]*$',
    re.MULTILINE)
LAST_CHECKED_REGEX = re.compile(
    r'^\*\*Last checked\*\*:[ \t]*(?P<value>\S+)[ \t]*$', re.MULTILINE)

MISSING_REFERENCE_MESSAGE = (
    "{path} not found.\n\n"
    "  This file is the manual checkpoint recording which version of the upstream\n"
    "  template this repository was last compared against. Seed it with:\n\n"
    "    cp {seed} {path}\n\n"
    "  then re-run this script with --update-stamp to record the current upstream ref."
)


class TemplateReferenceError(Exception):
    """Raised when .agents/TEMPLATE_REF.md cannot be parsed into a usable reference."""


class TemplateReference(NamedTuple):
    """The machine-readable stanza of .agents/TEMPLATE_REF.md."""

    repo_url: str
    version: str
    upstream_ref: str
    commit: str
    commit_date: str
    last_checked: str

    @property
    def is_stamped(self) -> bool:
        """True once a real upstream commit has been recorded against this repository.

        A freshly bootstrapped file carries `unknown`/`never`: bootstrap cannot know the
        upstream commit when the adopter used GitHub's "Use this template" (which creates
        an unrelated history). That is an honest starting state, not a parse failure.
        """
        return (
            self.commit not in ('', UNKNOWN_STAMP_VALUE)
            and self.last_checked not in ('', NEVER_CHECKED_VALUE)
        )


class UpstreamSnapshot(NamedTuple):
    """The result of cloning the upstream template, successful or not."""

    ok: bool
    reason: str
    worktree: Optional[Path]
    head_commit: str
    head_version: str
    head_date: str
    # True when the clone failed because the repository is missing or refused access, which
    # fails the check; False for a transport failure, which only warns.
    not_found: bool = False


class UpstreamChanges(NamedTuple):
    """What moved upstream between the recorded commit and upstream HEAD."""

    recorded_commit_found: bool
    commits: List[str]
    changed_paths: List[str]


class PathComparison(NamedTuple):
    """How the local governed paths differ from the upstream snapshot.

    Every path is in the UPSTREAM vocabulary. `evidence` maps each modified path to a
    one-line description of both sides (hash and size, and the local path when mapped);
    `missing_upstream` lists governed paths with nothing upstream to compare against.
    """

    only_upstream: List[str]
    only_local: List[str]
    modified: List[str]
    evidence: Optional[dict] = None
    missing_upstream: Tuple[str, ...] = ()

    @property
    def has_drift(self) -> bool:
        return bool(self.only_upstream or self.only_local or self.modified)


def parse_path_mappings(content: str) -> dict:
    """Read the `local` <- `upstream` pairs from the "Path mapping" section, as {upstream: local}.

    Only that section is read, so an example elsewhere in the file is never a mapping. A file
    without the section (every TEMPLATE_REF.md written before #135) maps nothing.
    """
    heading = PATH_MAPPING_HEADING_REGEX.search(content)
    if not heading:
        return {}
    section = content[heading.end():]
    end = re.search(r'^(?:## |---[ \t]*$)', section, re.MULTILINE)
    if end:
        section = section[:end.start()]
    return {match.group('upstream').strip('/'): match.group('local').strip('/')
            for match in PATH_MAPPING_LINE_REGEX.finditer(section)}


def _translate(path: str, pairs) -> str:
    """Rewrite `path` through the first (longest) `from -> to` prefix it falls under."""
    for source, target in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True):
        if path == source:
            return target
        if path.startswith(source + '/'):
            return target + path[len(source):]
    return path


def to_local_path(upstream_path: str, mappings: dict) -> str:
    return _translate(upstream_path, mappings.items())


def to_upstream_path(local_path: str, mappings: dict) -> str:
    return _translate(local_path, [(local, upstream) for upstream, local in mappings.items()])


def classify_clone_failure(stderr: str) -> bool:
    """True when git says the repository is missing or refused access, not that it is unreachable."""
    verdicts = [line.lower() for line in stderr.splitlines()
                if line.lstrip().lower().startswith(('fatal:', 'remote:'))]
    return any(pattern in line for line in verdicts for pattern in NOT_FOUND_PATTERNS)


def describe_bytes(content: bytes) -> str:
    """`sha256 <prefix>, <n> bytes`: both sides of a mismatch, readable from CI output alone."""
    digest = hashlib.sha256(content).hexdigest()[:EVIDENCE_HASH_LENGTH]
    return f"sha256 {digest}, {len(content)} bytes"


def run_git(arguments: Sequence[str], cwd: Optional[Path] = None,
            timeout: int = DEFAULT_GIT_TIMEOUT_SECONDS) -> Tuple[int, str, str]:
    """Run a git subcommand non-interactively, never prompting and never hanging.

    Returns (returncode, stdout, stderr). A missing git binary, a timeout, or an OS
    error are reported as a non-zero return code rather than raised: every caller
    treats an unavailable upstream as a degraded run, not a crash.
    """
    environment = dict(os.environ)
    # A credential prompt in a governance helper would block a CI job forever.
    environment['GIT_TERMINAL_PROMPT'] = '0'
    environment['GIT_ASKPASS'] = 'echo'

    try:
        completed = subprocess.run(
            ['git'] + list(arguments),
            cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, check=False,
            env=environment, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 1, '', f"git {' '.join(arguments)} timed out after {timeout}s"
    except (OSError, FileNotFoundError) as error:
        return 1, '', f"git is unavailable: {error}"

    return completed.returncode, completed.stdout, completed.stderr


def parse_template_reference(content: str) -> TemplateReference:
    """Parse the reference stanza out of a TEMPLATE_REF.md document.

    The format is the one `lfr-tunnel` and `liferay-ai-commerce-accelerator` already
    hand-maintain, so this reads their existing files unmodified:

        **Reference repo**: <https://github.com/owner/ai-agent-template>
        **Reference version at last check**: `v1.2.0` (origin/main @ `83b1cf2`, 2026-08-01)
        **Last checked**: 2026-08-05
    """
    url_match = REPO_URL_REGEX.search(content)
    stamp_match = REFERENCE_STAMP_REGEX.search(content)

    missing = []
    if not url_match:
        missing.append('**Reference repo**: <url>')
    if not stamp_match:
        missing.append('**Reference version at last check**: `<version>` (<ref> @ `<commit>`, <date>)')
    if missing:
        raise TemplateReferenceError(
            'Could not parse the template reference stanza. Missing or malformed line(s): '
            + '; '.join(missing))

    last_checked_match = LAST_CHECKED_REGEX.search(content)

    return TemplateReference(
        repo_url=url_match.group('url').strip(),
        version=stamp_match.group('version').strip(),
        upstream_ref=stamp_match.group('ref').strip() or DEFAULT_UPSTREAM_REMOTE_REF,
        commit=stamp_match.group('commit').strip(),
        commit_date=stamp_match.group('date').strip(),
        last_checked=(last_checked_match.group('value').strip()
                      if last_checked_match else NEVER_CHECKED_VALUE),
    )


def apply_reference_stamp(content: str, version: str, upstream_ref: str, commit: str,
                          commit_date: str, checked_on: str) -> str:
    """Rewrite the two stamp lines and refresh the documentation footer.

    Only the stamp lines change: the known-drift list and the surrounding prose are
    hand-maintained content and must survive verbatim. The footer is refreshed because
    the file was genuinely just updated, and check_docs_review.py would otherwise start
    counting staleness from a date that no longer reflects reality.
    """
    stamped, replaced = REFERENCE_STAMP_REGEX.subn(
        lambda _: (f'**Reference version at last check**: `{version}` '
                   f'({upstream_ref} @ `{commit}`, {commit_date})'),
        content, count=1)
    if replaced == 0:
        raise TemplateReferenceError(
            'Refusing to stamp: the "Reference version at last check" line was not found.')

    stamped, replaced = LAST_CHECKED_REGEX.subn(
        lambda _: f'**Last checked**: {checked_on}', stamped, count=1)
    if replaced == 0:
        raise TemplateReferenceError(
            'Refusing to stamp: the "Last checked" line was not found.')

    return FOOTER_REGEX.sub(
        f'*Last Updated: {checked_on}* | *Last Reviewed: {checked_on}*', stamped)


def resolve_local_template_stamp(root_dir: Path,
                                 timeout: int = DEFAULT_GIT_TIMEOUT_SECONDS) -> Tuple[str, str, str]:
    """Read (version, short commit, commit date) from a local git checkout.

    Used at bootstrap time to stamp the adopter's reference file with the template
    revision they actually started from. Returns `unknown` for anything git cannot
    answer -- notably a "Use this template" repository, whose history is unrelated to
    the template's. An honest `unknown` is recoverable (`--update-stamp` sets a real
    baseline); a confidently wrong commit is not.
    """
    def read(arguments: Sequence[str]) -> str:
        code, out, _ = run_git(['-C', str(root_dir)] + list(arguments), timeout=timeout)
        return out.strip() if code == 0 and out.strip() else UNKNOWN_STAMP_VALUE

    return (
        read(['describe', '--tags', '--abbrev=0']),
        read(['rev-parse', '--short', 'HEAD']),
        read(['log', '-1', '--format=%cs']),
    )


def fetch_upstream_snapshot(repo_url: str, dest: Path,
                            upstream_ref: str = DEFAULT_UPSTREAM_REMOTE_REF,
                            timeout: int = DEFAULT_GIT_TIMEOUT_SECONDS) -> UpstreamSnapshot:
    """Clone the upstream template into `dest`, or explain why it could not be reached.

    The full history is cloned rather than a shallow snapshot: resolving the recorded
    commit -- which may be many releases back -- is the whole point, and a shallow clone
    would not contain it. The template is small enough for this to stay cheap.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    code, _, stderr = run_git(['clone', '--quiet', repo_url, str(dest)], timeout=timeout)
    if code != 0:
        return UpstreamSnapshot(
            ok=False,
            reason=(stderr.strip().splitlines() or ['git clone failed'])[-1],
            worktree=None, head_commit='', head_version='', head_date='',
            not_found=classify_clone_failure(stderr))

    # `origin/main` names a remote-tracking ref in the *adopter's* vocabulary; inside a
    # fresh clone the same content is the local branch. Fall back to the clone's default
    # HEAD when that branch does not exist upstream (a fork renamed its default branch).
    branch_name = upstream_ref.split('/')[-1] if upstream_ref else ''
    if branch_name:
        run_git(['-C', str(dest), 'checkout', '--quiet', branch_name], timeout=timeout)

    def read(arguments: Sequence[str]) -> str:
        sub_code, out, _ = run_git(['-C', str(dest)] + list(arguments), timeout=timeout)
        return out.strip() if sub_code == 0 and out.strip() else UNKNOWN_STAMP_VALUE

    return UpstreamSnapshot(
        ok=True, reason='', worktree=dest,
        head_commit=read(['rev-parse', '--short', 'HEAD']),
        head_version=read(['describe', '--tags', '--abbrev=0']),
        head_date=read(['log', '-1', '--format=%cs']),
    )


def summarise_upstream_changes(worktree: Path, recorded_commit: str,
                               rel_paths: Iterable[str] = GOVERNED_PATHS,
                               timeout: int = DEFAULT_GIT_TIMEOUT_SECONDS) -> UpstreamChanges:
    """List the upstream commits and governed paths that moved since `recorded_commit`.

    An unknown or unreachable recorded commit is reported, not raised: a repository that
    has never stamped a baseline still deserves the local-versus-upstream comparison.
    """
    if not recorded_commit or recorded_commit == UNKNOWN_STAMP_VALUE:
        return UpstreamChanges(recorded_commit_found=False, commits=[], changed_paths=[])

    code, _, _ = run_git(
        ['-C', str(worktree), 'cat-file', '-e', f'{recorded_commit}^{{commit}}'], timeout=timeout)
    if code != 0:
        return UpstreamChanges(recorded_commit_found=False, commits=[], changed_paths=[])

    revision_range = f'{recorded_commit}..HEAD'
    _, log_output, _ = run_git(
        ['-C', str(worktree), 'log', '--oneline', '--no-decorate', revision_range], timeout=timeout)
    _, diff_output, _ = run_git(
        ['-C', str(worktree), 'diff', '--name-only', revision_range, '--'] + list(rel_paths),
        timeout=timeout)

    return UpstreamChanges(
        recorded_commit_found=True,
        commits=[line for line in log_output.splitlines() if line.strip()],
        changed_paths=sorted({line.strip() for line in diff_output.splitlines() if line.strip()}),
    )


def normalise_for_comparison(content, rel_path: Path) -> bytes:
    """Reduce a file to the bytes worth comparing across two repositories.

    Line endings and surrounding blank space are normalised, and the mandated
    `*Last Updated* | *Last Reviewed*` footer is stripped from Markdown. Every adopter's
    footers differ by construction -- reporting all twelve skill files as drifted because
    their dates moved would bury the one file whose rule actually changed.
    """
    if isinstance(content, bytes):
        try:
            text = content.decode('utf-8')
        except UnicodeDecodeError:
            # Binary content has no footer and no line-ending convention; compare raw.
            return content
    else:
        text = content

    text = text.replace('\r\n', '\n')
    if rel_path.suffix.lower() == '.md':
        text = FOOTER_REGEX.sub('', text)
    return text.strip().encode('utf-8')


def _collect_files(root: Path, rel_path: str) -> List[str]:
    """List the comparable files a governed path expands to, relative to `root`."""
    target = root / rel_path
    if target.is_file():
        return [rel_path]
    if not target.is_dir():
        return []

    collected = []
    for candidate in sorted(target.rglob('*')):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root)
        if any(part in COMPARISON_IGNORE_DIRS for part in relative.parts):
            continue
        if relative.suffix.lower() in COMPARISON_IGNORE_SUFFIXES:
            continue
        collected.append(relative.as_posix())
    return collected


def compare_paths(upstream_root: Path, local_root: Path,
                  rel_paths: Iterable[str] = GOVERNED_PATHS,
                  mappings: Optional[dict] = None,
                  explicit_paths: bool = False) -> PathComparison:
    """Classify every governed file as upstream-only, local-only, modified, or in sync.

    A path absent from both sides is silence, not drift: adopters legitimately delete
    template-only scaffolding, and re-reporting it on every run would train readers to
    ignore the output. A path absent UPSTREAM is different when there is something to
    compare -- it exists locally, or it was asked for by name (`explicit_paths`): that is
    reported in `missing_upstream`, because comparing it against nothing is not a pass.

    `rel_paths` may be given in either vocabulary; a local path is translated through
    `mappings` ({upstream: local}, from parse_path_mappings).
    """
    mappings = mappings or {}
    only_upstream, only_local, modified, missing_upstream = [], [], [], []
    evidence = {}

    for requested in rel_paths:
        rel_path = to_upstream_path(requested.strip('/'), mappings)
        upstream_files = set(_collect_files(upstream_root, rel_path))

        # The local files that stand for this upstream path: those at its own location, and
        # those at the local side of every mapping that falls under it. A mapped file is
        # collected last, so it wins over a stray unmapped copy at the upstream location.
        local_roots = [to_local_path(rel_path, mappings)] + [
            local for upstream, local in sorted(mappings.items())
            if upstream == rel_path or upstream.startswith(rel_path + '/')]
        local_files = {}
        for local_root_path in local_roots:
            for found in _collect_files(local_root, local_root_path):
                name = to_upstream_path(found, mappings)
                if name == rel_path or name.startswith(rel_path + '/'):
                    local_files[name] = found

        if not upstream_files and (explicit_paths or local_files):
            missing_upstream.append(rel_path)
            continue

        only_upstream.extend(sorted(upstream_files - set(local_files)))
        only_local.extend(sorted(local_files[name] for name in set(local_files) - upstream_files))

        for shared in sorted(upstream_files & set(local_files)):
            upstream_bytes = (upstream_root / shared).read_bytes()
            local_bytes = (local_root / local_files[shared]).read_bytes()
            if (normalise_for_comparison(upstream_bytes, Path(shared))
                    != normalise_for_comparison(local_bytes, Path(shared))):
                modified.append(shared)
                where = '' if local_files[shared] == shared else f" at {local_files[shared]}"
                evidence[shared] = (f"local{where}: {describe_bytes(local_bytes)}; "
                                    f"upstream: {describe_bytes(upstream_bytes)}")

    return PathComparison(
        only_upstream=sorted(set(only_upstream)),
        only_local=sorted(set(only_local)),
        modified=sorted(set(modified)),
        evidence=evidence,
        missing_upstream=tuple(sorted(set(missing_upstream))),
    )


def _print_section(title: str, entries: Sequence[str], empty_note: str = None, limit: int = 40):
    """Print one report section, truncating pathologically long lists."""
    if not entries:
        if empty_note:
            print(f"  {empty_note}")
        return

    print(f"\n{title} ({len(entries)}):")
    for entry in entries[:limit]:
        print(f"  - {entry}")
    if len(entries) > limit:
        print(f"  ... and {len(entries) - limit} more")


def is_shared_file(path: str, mappings: Optional[dict] = None) -> bool:
    """True for a file whose copies must be byte-identical: a shared docs file, or a mapped one.

    Only these get the copy-FROM-upstream direction. Bootstrap customises AGENTS.md, ci.yml and
    the pre-commit config, and TEMPLATE_REF.md says to edit those freely; telling an adopter to
    overwrite them from the template would be wrong.
    """
    return (path in DOCS_SITE_SHARED_PATHS or path in (mappings or {})
            or path.startswith(DOCS_PROFILE_DIR + '/'))


def unmapped_profile_stylesheets(upstream_root: Path, local_root: Path, mappings: dict) -> list:
    """Suggested mapping lines for profile stylesheets present locally but not mapped.

    A TEMPLATE_REF.md written before the Path mapping section existed maps nothing, so its copy
    of a shared stylesheet would never be compared -- and nothing would say so.
    """
    suggestions = []
    for upstream_css in sorted((upstream_root / DOCS_PROFILE_DIR).glob('*/*.css')):
        upstream = upstream_css.relative_to(upstream_root).as_posix()
        local = f'{DOCS_PROFILE_STYLESHEET_DIR}/{upstream_css.name}'
        if upstream not in mappings and (local_root / local).is_file():
            suggestions.append(f"- `{local}` ← `{upstream}`")
    return suggestions


def report(reference: TemplateReference, snapshot: UpstreamSnapshot,
           changes: UpstreamChanges, comparison: PathComparison,
           mappings: Optional[dict] = None) -> None:
    """Print the human-readable drift report."""
    print(f"Upstream : {reference.repo_url}")
    print(f"Recorded : {reference.version} @ {reference.commit} "
          f"({reference.commit_date}), last checked {reference.last_checked}")
    print(f"Upstream HEAD: {snapshot.head_version} @ {snapshot.head_commit} ({snapshot.head_date})")

    if not changes.recorded_commit_found:
        print(
            "\n⚠️ No usable baseline: the recorded commit is unknown or not present upstream.\n"
            "   Skipping the 'what changed upstream' section. Re-run with --update-stamp to\n"
            "   record the current upstream ref as your baseline.")
    elif not changes.commits:
        print("\n✓ The upstream template has not moved since your last check.")
    else:
        _print_section("Upstream commits since your recorded ref", changes.commits)
        _print_section("Governed paths changed upstream since your recorded ref",
                       changes.changed_paths)

    print("\n--- Local governance files vs upstream HEAD "
          "(timestamp footers ignored) ---")
    if comparison.missing_upstream:
        _print_section("Governed paths with nothing upstream to compare against",
                       list(comparison.missing_upstream))
    if not comparison.has_drift:
        if not comparison.missing_upstream:
            print("  ✓ No drift: every governed path matches upstream.")
    else:
        _print_section("Present upstream, absent locally", comparison.only_upstream)
        _print_section("Present locally, absent upstream", comparison.only_local)
        evidence = comparison.evidence or {}
        _print_section("Content differs",
                       [f"{path}\n      {evidence[path]}" if path in evidence else path
                        for path in comparison.modified])
        if any(is_shared_file(path, mappings)
               for path in comparison.modified + comparison.only_upstream):
            print("\n" + DIRECTION_MESSAGE.format(repo_url=reference.repo_url))


def update_stamp_file(reference_path: Path, reference: TemplateReference,
                      snapshot: UpstreamSnapshot, checked_on: str) -> None:
    """Move the recorded ref and 'Last checked' date forward, in place."""
    content = reference_path.read_text(encoding='utf-8')
    reference_path.write_text(
        apply_reference_stamp(
            content,
            version=snapshot.head_version,
            upstream_ref=reference.upstream_ref,
            commit=snapshot.head_commit,
            commit_date=snapshot.head_date,
            checked_on=checked_on,
        ),
        encoding='utf-8')
    print(f"\n✓ Updated {reference_path.name}: now recorded against "
          f"{snapshot.head_version} @ {snapshot.head_commit}, last checked {checked_on}.")


def should_offer_stamp_update(update_stamp: bool) -> bool:
    """Decide whether to rewrite the stamp, prompting only when a human is present.

    Non-interactive by default (see .agents/skills/tool-use-react/SKILL.md): a run with
    no TTY -- CI, a hook, a piped invocation -- never prompts and never writes unless
    --update-stamp was passed explicitly.
    """
    if update_stamp:
        return True
    if not sys.stdin.isatty():
        return False
    try:
        answer = input("\nUpdate the 'Last checked' stamp to the current upstream ref? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in ('y', 'yes')


def check_template_drift(root_dir: Path, repo_url: Optional[str] = None,
                         rel_paths: Sequence[str] = GOVERNED_PATHS,
                         update_stamp: bool = False, fail_on_drift: bool = False,
                         timeout: int = DEFAULT_GIT_TIMEOUT_SECONDS,
                         explicit_paths: bool = False, docs_site: bool = False) -> int:
    """Run the whole check and return the process exit code (see module docstring)."""
    reference_path = root_dir / TEMPLATE_REFERENCE_RELPATH
    if not reference_path.exists():
        print('❌ ' + MISSING_REFERENCE_MESSAGE.format(
            path=TEMPLATE_REFERENCE_RELPATH.as_posix(),
            seed=TEMPLATE_REFERENCE_SEED_RELPATH.as_posix()), file=sys.stderr)
        return 1

    try:
        reference_text = reference_path.read_text(encoding='utf-8')
        reference = parse_template_reference(reference_text)
    except (TemplateReferenceError, OSError) as error:
        print(f"❌ {TEMPLATE_REFERENCE_RELPATH.as_posix()}: {error}", file=sys.stderr)
        return 1
    mappings = parse_path_mappings(reference_text)

    effective_repo_url = repo_url or reference.repo_url

    with tempfile.TemporaryDirectory(prefix='template-drift-') as scratch_dir:
        snapshot = fetch_upstream_snapshot(
            effective_repo_url, Path(scratch_dir) / 'upstream',
            upstream_ref=reference.upstream_ref, timeout=timeout)

        if not snapshot.ok and snapshot.not_found:
            print(
                f"❌ The upstream template at {effective_repo_url} was not found, or refused "
                "access.\n"
                f"   git said: {snapshot.reason}\n"
                "   Nothing was compared. This will not fix itself on a re-run: check the "
                "**Reference repo** line\n"
                f"   in {TEMPLATE_REFERENCE_RELPATH.as_posix()} (or --repo-url) and the "
                "credentials this job runs with.", file=sys.stderr)
            return 3
        if not snapshot.ok:
            print(
                f"⚠️ Could not reach the upstream template at {effective_repo_url} "
                "(offline, or a network failure).\n"
                f"   git said: {snapshot.reason}\n"
                "   Skipping the drift comparison. This is NOT a failure: an offline "
                "checkout is a normal state\n"
                "   and this check must never block work that has nothing to do with it.")
            return 0

        upstream_paths = [to_upstream_path(path.strip('/'), mappings) for path in rel_paths]
        changes = summarise_upstream_changes(
            snapshot.worktree, reference.commit, upstream_paths, timeout=timeout)
        comparison = compare_paths(snapshot.worktree, root_dir, rel_paths,
                                   mappings=mappings, explicit_paths=explicit_paths)
        report(reference, snapshot, changes, comparison, mappings)
        if docs_site:
            suggestions = unmapped_profile_stylesheets(snapshot.worktree, root_dir, mappings)
            if suggestions:
                print(
                    "\n⚠️ Not compared: a shared profile stylesheet with no path mapping. Add to "
                    f"the Path mapping section\n   of {TEMPLATE_REFERENCE_RELPATH.as_posix()}:\n   "
                    + '\n   '.join(suggestions))

        if comparison.missing_upstream:
            print(
                f"\n❌ {len(comparison.missing_upstream)} governed path(s) do not exist upstream, "
                "so nothing was compared for them.\n"
                "   A renamed or removed upstream file will not reappear on a re-run: fix the "
                "--path list, or the\n"
                f"   Path mapping section of {TEMPLATE_REFERENCE_RELPATH.as_posix()}.",
                file=sys.stderr)
            return 3

        # Content only. Upstream commits since the recorded ref are listed above, but a commit
        # that changed nothing this repository carries is not drift, and a docs CI job gating
        # on three shared files must not go red because the template moved elsewhere.
        drift_found = comparison.has_drift

        if should_offer_stamp_update(update_stamp):
            try:
                update_stamp_file(reference_path, reference, snapshot,
                                  datetime.today().strftime('%Y-%m-%d'))
            except (TemplateReferenceError, OSError) as error:
                print(f"⚠️ Could not update the stamp: {error}", file=sys.stderr)
        elif not update_stamp:
            print("\nRe-run with --update-stamp to record this comparison "
                  "(and add any real divergence to the 'Known drift' list first).")

    if drift_found and fail_on_drift:
        print("\n✗ Drift detected and --fail-on-drift was requested.", file=sys.stderr)
        return 2
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Compare this repository against the upstream AI agent template '
                    'recorded in .agents/TEMPLATE_REF.md.')
    parser.add_argument('--root', default=None,
                        help='Repository root to check (default: the repository containing this script)')
    parser.add_argument('--repo-url', default=None,
                        help='Override the upstream URL recorded in the reference file (for forks)')
    parser.add_argument('--path', action='append', dest='paths', default=None,
                        help='Governed path to compare. Repeatable; replaces the default set. '
                             'Upstream or local (mapped) paths; one that does not exist '
                             'upstream exits 3.')
    parser.add_argument('--docs-site', action='store_true',
                        help='Compare only the shared docs-site files (%s), plus every '
                             'mapped path, instead of the default set. For a docs CI job.'
                             % ', '.join(DOCS_SITE_SHARED_PATHS))
    parser.add_argument('--update-stamp', action='store_true',
                        help="Record the current upstream ref as the new baseline without prompting")
    parser.add_argument('--fail-on-drift', action='store_true',
                        help='Exit 2 when governed files differ from upstream (an unreachable '
                             'upstream still exits 0)')
    parser.add_argument('--timeout', type=int, default=DEFAULT_GIT_TIMEOUT_SECONDS,
                        help='Per-git-command timeout in seconds')

    args = parser.parse_args(argv)
    root_dir = Path(args.root).resolve() if args.root else Path(__file__).parent.parent.resolve()

    rel_paths = tuple(args.paths or ())
    if args.docs_site:
        reference_path = root_dir / TEMPLATE_REFERENCE_RELPATH
        mapped = (tuple(parse_path_mappings(reference_path.read_text(encoding='utf-8')))
                  if reference_path.exists() else ())
        rel_paths += DOCS_SITE_SHARED_PATHS + tuple(
            path for path in mapped if path not in DOCS_SITE_SHARED_PATHS)

    return check_template_drift(
        root_dir=root_dir,
        repo_url=args.repo_url,
        rel_paths=rel_paths or GOVERNED_PATHS,
        update_stamp=args.update_stamp,
        fail_on_drift=args.fail_on_drift,
        timeout=args.timeout,
        explicit_paths=bool(rel_paths),
        docs_site=args.docs_site,
    )


if __name__ == '__main__':
    sys.exit(main())
