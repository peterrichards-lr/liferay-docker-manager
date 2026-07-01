### Implementation Plan

**Objective:** Enforce a minimum of 60% code coverage across the LDM codebase. 
Since our current test coverage sits at ~63%, we can safely enforce this new baseline without needing to write new tests immediately.

**Steps:**
1. Create a new branch `feat/288-increase-coverage-threshold`.
2. Open `pyproject.toml` and locate the `[tool.coverage.report]` section.
3. Update the `fail_under = 40` property to `fail_under = 60`.
4. Run the test suite locally (`.venv/bin/pytest`) to verify that the coverage report succeeds against the new threshold.
5. Commit the changes and open a Pull Request.

This will ensure that all future PRs maintain at least 60% coverage, preventing coverage regressions going forward.
