# Fix Ledger: redesign_4

## Patch sync_emit — DecisionLog background thread disabled

**Classification:** Compatibility
**Changed behavior:** `Runtime(emit=None)` — `DecisionLog` uses a no-op emitter; background log thread writes nothing but still terminates cleanly via `close()`.
**Regression evidence:** `tests/test_team_runner.py::test_redesign_4_one_tick`
**Source diff:** `shim.py` — `Runtime(emit=lambda _: None)` + `rt.close()` calls; agent source files unchanged
**Revision:** v1-2026-10-22
