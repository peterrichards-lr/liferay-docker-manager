# LDM Quality & Adoption Hardening Plan

## Objective

Transition LDM from a permissive development environment to a strict, enforced quality gate to ensure stability, prevent regressions (specifically `TypeError` and `AttributeError` issues), and increase field adoption by providing a seamless contributor onboarding experience.

## Key Files & Context

- `pyproject.toml`: Current Ruff configuration lacks strict enforcement.
- `.pre-commit-config.yaml`: Missing static typing (`mypy`) and consolidated checks.
- `.github/workflows/ci.yml`: Current CI runs redundant linting steps outside of `pre-commit` and does not enforce test coverage.
- `ldm_core/cli.py` & `ldm_core/handlers/dev.py`: Entry points for adding the new `dev-setup` and `doctor` reporting features.
- `docs/TESTING.md`: The matrix of tests we aim to satisfy through this hardening process.

## Implementation Steps

### Phase 1: Linting & Static Analysis Hardening

1. **Aggressive Ruff Configuration**: Update `pyproject.toml` to enforce strict Ruff rules:
    - Enable `E` (pycodestyle), `F` (Pyflakes), `I` (isort), `UP` (pyupgrade), `PL` (Pylint), and `N` (pep8-naming).
    - Enforce line-length and docstring conventions.
2. **Introduce `mypy`**: Add `mypy` to `.pre-commit-config.yaml` to enforce static typing across the `ldm_core/` directory. Resolve existing type ambiguities.
3. **Consolidate `pre-commit`**: Move redundant bash scripts from `lint.sh` and `ci.yml` directly into `.pre-commit-config.yaml` (e.g., standardizing `shellcheck` and `markdownlint`).

### Phase 2: Test Coverage & CI Enforcement

1. **Coverage Threshold**: Update `pyproject.toml` (or `pytest.ini` config) to mandate a minimum test coverage threshold (starting at 55%, failing the build if it drops below).
2. **CI Optimization**: Refactor `.github/workflows/ci.yml` to rely entirely on `pre-commit run --all-files` for the linting phase, ensuring local and CI checks are identical.

### Phase 3: Developer Onboarding (`dev-setup`)

1. **New CLI Command**: Implement `ldm dev-setup` (likely in `ldm_core/handlers/dev.py`).
    - Automatically initializes a Python virtual environment (`.venv`).
    - Installs `requirements-dev.txt`.
    - Automatically installs and registers `pre-commit` hooks (`pre-commit install`).
2. **Documentation**: Update `CONTRIBUTING.md` to point new developers to `ldm dev-setup` as the single required onboarding step.

### Phase 4: Field Adoption (Telemetry & Reporting)

1. **Sanitized Crash Reports**: Enhance `ldm doctor` or the global exception handler to generate a sanitized `ldm-debug-bundle.zip` (stripping secrets, passwords, and `.env` files) when a critical failure occurs, making it easy for users to submit high-quality GitHub Issues.

## Verification & Testing

- Run `ldm dev-setup` in a clean environment to verify it correctly scaffolds the dev stack.
- Run `pre-commit run --all-files` to ensure the new Ruff and Mypy rules catch intentional violations.
- Verify the CI workflow fails if coverage drops below 55%.
- Ensure all existing automated tests in `TESTING.md` continue to pass.
