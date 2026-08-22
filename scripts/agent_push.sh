#!/usr/bin/env bash
set -e

if [ -z "$1" ]; then
  echo "Error: Commit message required."
  echo "Usage: ./scripts/agent_push.sh \"commit message\""
  exit 1
fi

COMMIT_MSG="$1"

# Ensure we are in the workspace root
cd "$(dirname "$0")/.."

# Prints each line of a newline-separated list, indented, skipping blanks.
indent_list() {
  while IFS= read -r _line; do
    [ -n "$_line" ] && printf '      %s\n' "$_line"
  done <<EOF
$1
EOF
}

# ---------------------------------------------------------------------------
# Pre-flight staging guard (LDM-#1280)
#
# This wrapper does NOT stage for you. Historically `git add .` ran only inside
# the hook-failure branch below, the commit was guarded on staged changes, and
# the push ran unconditionally -- so a run where every gate passed first time
# committed nothing, pushed an empty branch, printed "Push completed
# successfully!" and exited 0.
#
# The incentive was inverted: the cleaner the work, the more likely it silently
# committed nothing. Work that tripped a hook was rescued by that `git add .`;
# work that passed every gate first time fell straight through.
#
# These checks run BEFORE the ~10-minute quality gates, so a staging mistake
# costs a second rather than ten minutes followed by an empty push.
# ---------------------------------------------------------------------------
STAGED_FILES="$(git diff --cached --name-only)"
UNSTAGED_FILES="$(git diff --name-only)"
UNTRACKED_FILES="$(git ls-files --others --exclude-standard)"

PUSH_ONLY=false

if [ -z "$STAGED_FILES" ]; then
  if [ -n "$UNSTAGED_FILES" ] || [ -n "$UNTRACKED_FILES" ]; then
    echo "=> [ERROR] Nothing is staged, but the working tree has changes."
    echo ""
    echo "    This wrapper commits only what YOU have staged. Left to run, it"
    echo "    would commit nothing and push an empty branch while reporting"
    echo "    success. Stage the files you intend to commit, then re-run:"
    echo ""
    echo "        git add <paths>            # prefer explicit paths over -A"
    echo "        ./scripts/agent_push.sh \"<commit message>\""
    echo ""
    if [ -n "$UNSTAGED_FILES" ]; then
      echo "    Modified but unstaged:"
      indent_list "$UNSTAGED_FILES"
    fi
    if [ -n "$UNTRACKED_FILES" ]; then
      echo "    Untracked:"
      indent_list "$UNTRACKED_FILES"
    fi
    echo ""
    echo "    If you genuinely meant to push existing commits only, commit or"
    echo "    stash these changes first so the intent is explicit."
    exit 1
  fi

  # Clean tree and nothing staged. This is only legitimate when there are
  # already-made commits that the remote has not seen -- e.g. re-running after
  # a network failure during the push. Anything else is a no-op that must not
  # masquerade as a successful push.
  UNPUSHED="unknown"
  if git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    UNPUSHED="$(git rev-list --count '@{u}..HEAD')"
  fi

  if [ "$UNPUSHED" = "0" ]; then
    echo "=> [ERROR] Nothing staged, working tree clean, and no unpushed commits."
    echo "    There is nothing to commit and nothing to push. Refusing to report"
    echo "    a successful push for a no-op."
    exit 1
  fi

  PUSH_ONLY=true
  echo "=> [WARN] Nothing staged; the working tree is clean."
  if [ "$UNPUSHED" = "unknown" ]; then
    echo "    This branch has no upstream yet, so it will be published as-is."
  else
    echo "    Proceeding in PUSH-ONLY mode: $UNPUSHED existing commit(s) to push."
  fi
  echo "    No new commit will be created."
fi

echo "=> Activating virtual environment..."
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "Error: .venv not found. Please ensure virtual environment is set up."
  exit 1
fi

echo "=> Running pre-commit hooks (Quality Gate)..."
if ! SKIP=bump-docs-timestamps .venv/bin/python3 -m pre_commit run --all-files; then
  echo "=> [WARN] Pre-commit hooks failed or auto-formatted files."
  # Re-stage ONLY the paths that were already staged. The previous `git add .`
  # here swept the entire working tree into the commit, so unrelated edits and
  # scratch debris rode along -- a plausible contributor to LDM-#1233.
  #
  # Hooks run against the working tree (`--all-files`), not the index, so a
  # file the hook reformatted but that was never staged still passes the retry
  # below without being added to this commit.
  if [ -n "$STAGED_FILES" ]; then
    echo "=> Re-staging hook modifications to the originally staged paths..."
    echo "$STAGED_FILES" | tr '\n' '\0' | xargs -0 -r git add --
  else
    echo "=> Nothing was staged; not staging hook modifications."
  fi
  if ! SKIP=bump-docs-timestamps .venv/bin/python3 -m pre_commit run --all-files; then
    echo "=> [ERROR] Pre-commit hooks failed again. Manual intervention required."
    exit 1
  fi

  # Narrowing the re-stage above means hook edits to files you did NOT stage are
  # no longer swept in silently -- but they must not be silently DROPPED either.
  # `detect-secrets` rewriting `.secrets.baseline` is the case that matters: left
  # uncommitted it fails CI later (see the Secrets Baseline note in
  # .agents/skills/testing-and-ci/SKILL.md). Surface them and let the caller
  # decide, rather than choosing for them in either direction.
  HOOK_TOUCHED_UNSTAGED="$(git diff --name-only)"
  if [ -n "$HOOK_TOUCHED_UNSTAGED" ]; then
    echo "=> [WARN] Hooks modified files that are NOT staged and will NOT be committed:"
    indent_list "$HOOK_TOUCHED_UNSTAGED"
    echo "    If any belong in this commit (e.g. .secrets.baseline), abort now,"
    echo "    'git add' them, and re-run."
  fi
fi

echo "=> Running PyTest suite (Testing Gate)..."
if ! .venv/bin/python3 -m pytest; then
  echo "=> [ERROR] PyTest suite failed. Fix the failing tests before pushing."
  exit 1
fi

echo "=> [SUCCESS] All Quality Gates Passed!"

if [ "$PUSH_ONLY" = true ]; then
  echo "=> Push-only mode: skipping commit as announced above."
else
  if git diff --cached --quiet; then
    # Staged content vanished between the pre-flight check and here -- e.g. a
    # hook reverted it. Fail rather than push an empty branch.
    echo "=> [ERROR] Staged changes disappeared during the quality gates."
    echo "    Nothing would be committed. Inspect 'git status' before retrying."
    exit 1
  fi

  HEAD_BEFORE="$(git rev-parse HEAD)"
  echo "=> Committing changes..."
  SKIP=bump-docs-timestamps git commit -m "$COMMIT_MSG"

  # Verify the commit actually landed. `git commit` can exit 0 without creating
  # a commit in some hook configurations, and a silent no-op here is exactly the
  # failure this guard exists to prevent.
  if [ "$(git rev-parse HEAD)" = "$HEAD_BEFORE" ]; then
    echo "=> [ERROR] 'git commit' reported success but HEAD did not move."
    echo "    No commit was created; refusing to push."
    exit 1
  fi
  echo "=> Committed $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)"
fi

echo "=> Pushing to remote..."
git push origin HEAD

echo "=> ✅ Push completed successfully!"
if [ "$PUSH_ONLY" != true ]; then
  echo "=>    $(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"
  git show --stat --oneline HEAD | tail -n +2
fi
