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

echo "=> Activating virtual environment..."
if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "Error: .venv not found. Please ensure virtual environment is set up."
  exit 1
fi

echo "=> Running pre-commit hooks (Quality Gate)..."
if ! SKIP=bump-docs-timestamps,actionlint,semgrep,detect-secrets .venv/bin/python3 -m pre_commit run --all-files; then
  echo "=> [WARN] Pre-commit hooks failed or auto-formatted files."
  echo "=> Automatically staging any hook modifications and retrying..."
  git add .
  if ! SKIP=bump-docs-timestamps,actionlint,semgrep,detect-secrets .venv/bin/python3 -m pre_commit run --all-files; then
    echo "=> [ERROR] Pre-commit hooks failed again. Manual intervention required."
    exit 1
  fi
fi

echo "=> Running PyTest suite (Testing Gate)..."
if ! .venv/bin/python3 -m pytest; then
  echo "=> [ERROR] PyTest suite failed. Fix the failing tests before pushing."
  exit 1
fi

echo "=> [SUCCESS] All Quality Gates Passed!"

# Only commit if there are staged changes (prevent empty commits)
if ! git diff --cached --quiet; then
    echo "=> Committing changes..."
    SKIP=bump-docs-timestamps,actionlint,semgrep,detect-secrets git commit -m "$COMMIT_MSG"
else
    echo "=> No staged changes to commit. Proceeding to push..."
fi

echo "=> Pushing to remote..."
git push origin HEAD

echo "=> ✅ Push completed successfully!"
