# Plan: StackHandler Modularization

Surgical refactoring of the LDM orchestration engine.

## Phase 1: Extraction & Stubs

- [x] Create `ldm_core/handlers/composer.py`, `ldm_core/handlers/runtime.py`, and `ldm_core/handlers/assets.py`.
- [x] Migrate `write_docker_compose` and dependencies to `ComposerHandler`.
- [x] Migrate `_fetch_seed` and `_ensure_seeded` to `AssetHandler`.
- [x] Update `LiferayManager` to inherit from new specialized handlers.

## Phase 2: Runtime Re-composition

- [x] Refactor `sync_stack` in `ldm_core/handlers/runtime.py` to use specialized handler methods.
- [x] Migrate command methods (`cmd_logs`, `cmd_stop`, etc.).
- [x] Centralize `_pre_flight_checks` for shared use.

## Phase 3: Verification & Hardening

- [x] Run full unit test suite (106 tests).
- [x] Run E2E verification suite.
- [x] Add "Fuzzy Configuration" tests for the new modular logic.

## 🏁 Definition of Done

- [x] `ldm_core/handlers/stack.py` is removed or reduced to a simple bridge.
- [x] All "Redline" contract tests pass.
- [x] E2E suite passes on physical lab hardware.
