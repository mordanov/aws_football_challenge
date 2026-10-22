# Fix Ledger: redesign_1

## Patch no_background — Brain background threads disabled

**Classification:** Compatibility
**Changed behavior:** `Brain(background=False)` — no playbook refresh thread spawned at start; `MemoryStore` already used when `AFC_BUCKET` unset.
**Regression evidence:** `tests/test_team_runner.py::test_redesign_1_no_background_threads`
**Source diff:** `agent/main.py:186` — `Brain()` → `Brain(background=False)`
**Revision:** v1-2026-10-22
