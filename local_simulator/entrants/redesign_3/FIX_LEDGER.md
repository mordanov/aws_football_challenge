# Fix Ledger: redesign_3

## Patch reset_state — FootballTrainer per-match state reset

**Classification:** Compatibility
**Changed behavior:** Shim calls `reset_match_state(preserve_cross_match_learning=False)` on startup and on each `reset` command, clearing per-match history, scout, and ledger from the singleton `_TRAINER`.
**Regression evidence:** `tests/test_team_runner.py::test_redesign_3_one_tick`
**Source diff:** `shim.py` — reset call added; agent source files unchanged
**Revision:** v1-2026-10-22
