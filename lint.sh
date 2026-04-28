#!/bin/bash

# Liferay Docker Scripts - Linting & Formatting Utility

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$SCRIPT_DIR/.venv"
CHECK_ONLY=0

if [[ "$1" == "--check" ]]; then
	CHECK_ONLY=1
fi

info() { echo -e "\033[0;33mℹ $1\033[0m"; }
success() { echo -e "\033[0;32m✅ $1\033[0m"; }
error() { echo -e "\033[1;31m❌ $1\033[0m"; }

EXIT_CODE=0
export LDM_TEST_MODE=true

# 1. Python (Ruff)
info "Linting Python files (Ruff)..."
if [ -d "$VENV_PATH" ]; then
	if [[ $CHECK_ONLY -eq 1 ]]; then
		"$VENV_PATH/bin/ruff" check "$SCRIPT_DIR" || EXIT_CODE=1
		"$VENV_PATH/bin/ruff" format --check "$SCRIPT_DIR" || EXIT_CODE=1
	else
		"$VENV_PATH/bin/ruff" check "$SCRIPT_DIR" --fix || EXIT_CODE=1
		"$VENV_PATH/bin/ruff" format "$SCRIPT_DIR" || EXIT_CODE=1
	fi
	[[ $EXIT_CODE -eq 0 ]] && success "Python linting complete."
else
	error "Virtual environment not found. Skip Python linting."
fi

# 2. Markdown (markdownlint-cli2)
info "Linting Markdown files..."
if command -v markdownlint-cli2 &>/dev/null; then
	if [[ $CHECK_ONLY -eq 1 ]]; then
		markdownlint-cli2 "**/*.md" "!node_modules" "!.venv" "!.smoke_venv" "!.github" || EXIT_CODE=1
	else
		markdownlint-cli2 "**/*.md" "!node_modules" "!.venv" "!.smoke_venv" "!.github" --fix || EXIT_CODE=1
	fi
	[[ $EXIT_CODE -eq 0 ]] && success "Markdown linting complete."
else
	error "markdownlint-cli2 not found. Install with: npm install -g markdownlint-cli2"
fi

# 3. Shell (shfmt)
info "Formatting Shell scripts (shfmt)..."
if command -v shfmt &>/dev/null; then
	if [[ $CHECK_ONLY -eq 1 ]]; then
		shfmt -l -d -ln=zsh "$SCRIPT_DIR"/*.sh || EXIT_CODE=1
	else
		shfmt -l -w -ln=zsh "$SCRIPT_DIR"/*.sh || EXIT_CODE=1
	fi
	[[ $EXIT_CODE -eq 0 ]] && success "Shell formatting complete."
else
	error "shfmt not found. Install with: brew install shfmt"
fi

# 3.1 PowerShell (PSScriptAnalyzer)
if command -v pwsh &>/dev/null; then
	info "Linting PowerShell scripts (PSScriptAnalyzer)..."
	pwsh -Command "if (Get-Module -ListAvailable PSScriptAnalyzer) { Invoke-ScriptAnalyzer -Path '$SCRIPT_DIR/scripts' -Recurse | Where-Object Severity -eq 'Error' } else { Write-Warning 'PSScriptAnalyzer module not found.' }" || EXIT_CODE=1
fi

# 4. Security (Bandit)
info "Running Security Scan (Bandit)..."
if [ -d "$VENV_PATH" ]; then
	"$VENV_PATH/bin/bandit" -r "$SCRIPT_DIR/ldm_core/" -x "$SCRIPT_DIR/ldm_core/tests/" -s B101,B103,B108,B110,B404,B603,B607 || EXIT_CODE=1
	[[ $EXIT_CODE -eq 0 ]] && success "Security scan complete."
else
	error "Virtual environment not found. Skip Security scanning."
fi

# 5. Unit Tests (Pytest)
info "Running Unit Tests (Pytest)..."
if [ -d "$VENV_PATH" ]; then
	export PYTHONPATH="$SCRIPT_DIR"
	"$VENV_PATH/bin/pytest" "$SCRIPT_DIR/ldm_core/tests/" || EXIT_CODE=1
	[[ $EXIT_CODE -eq 0 ]] && success "Unit tests passed."
else
	error "Virtual environment not found. Skip Unit tests."
fi

# 6. Doctor Pipeline Self-Test
info "Testing Doctor Exit Codes..."
if [ -d "$VENV_PATH" ]; then
	# We expect this to either be 0 (Healthy) or 1 (Critical)
	# It must NOT be 127 (Not found) or anything else
	"$VENV_PATH/bin/python3" "$SCRIPT_DIR/liferay_docker.py" doctor --skip-project >/dev/null 2>&1
	DOC_CODE=$?
	if [[ $DOC_CODE -eq 0 ]] || [[ $DOC_CODE -eq 1 ]]; then
		success "Doctor pipeline integrity verified (Code: $DOC_CODE)."
	else
		error "Doctor returned unexpected exit code: $DOC_CODE"
		EXIT_CODE=1
	fi
fi

# 7. Documentation Sync
info "Synchronizing Documentation..."
export PYTHONPATH="$SCRIPT_DIR"
python3 "$SCRIPT_DIR/scripts/sync_docs.py" || EXIT_CODE=1

if [[ $EXIT_CODE -eq 0 ]]; then
	success "All linting tasks completed successfully."
else
	error "Linting failed. Please review the errors above."
fi

exit $EXIT_CODE
