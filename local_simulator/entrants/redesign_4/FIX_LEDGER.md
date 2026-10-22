# Fix Ledger: redesign_4

## Addition profile_shim — profile injection for M5 (not a behavior patch)

**Classification:** Harness addition (new file; `shim.py` and `redesign_4_v1` unchanged)
**Changed behavior:** none by default; `$AFC_PROFILE` loads a profile validated by the frozen
`agent/profile.py`. Used only by Stage 2 evaluation.
**Regression evidence:** `tests/test_evo_profile_shim.py::test_default_profile_equals_frozen_shim`
**Revision:** v1-2026-10-22

---

## Patch sync_emit — DecisionLog background thread disabled

**Classification:** Compatibility
**Changed behavior:** `Runtime(emit=None)` — `DecisionLog` uses a no-op emitter; background log thread writes nothing but still terminates cleanly via `close()`.
**Regression evidence:** `tests/test_team_runner.py::test_redesign_4_one_tick`
**Source diff:** `shim.py` — `Runtime(emit=lambda _: None)` + `rt.close()` calls; agent source files unchanged
**Revision:** v1-2026-10-22
