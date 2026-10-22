# Fix Ledger: redesign_2

# Fix Ledger: redesign_2

## Patch silent_emit — Runtime emit disabled for JSONL protocol compatibility

**Classification:** Compatibility
**Changed behavior:** `Runtime(emit=lambda _: None)` — `emit_json` would print decision logs to stdout, contaminating the JSONL protocol. Silent emit suppresses all log output.
**Regression evidence:** `tests/test_team_runner.py::test_redesign_2_one_tick`
**Source diff:** `shim.py` — `Runtime()` → `Runtime(emit=lambda _: None)`
**Revision:** v1-2026-10-22
