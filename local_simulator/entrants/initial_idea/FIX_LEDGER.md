# Fix Ledger: initial_idea

## Patch llm_disable — LLM tier disabled

**Classification:** Compatibility
**Changed behavior:** `choose()` always returns `None`; the rules-only decision path runs for all players.
**Regression evidence:** `tests/test_team_runner.py::test_initial_idea_one_tick`
**Source diff:** `agent/llm_layer.py` — entire file replaced with single-function stub
**Revision:** v1-2026-10-22

## Patch stdout_to_stderr — DECISION log lines redirected from stdout to stderr

**Classification:** Compatibility
**Changed behavior:** All `print(f"DECISION ...")` calls in `agent/main.py` now write to `sys.stderr` so JSONL protocol stdout stays clean.
**Regression evidence:** `tests/test_team_runner.py::test_initial_idea_one_tick`
**Source diff:** `agent/main.py` — added `import sys`, changed 3 print() calls to `file=sys.stderr`
**Revision:** v1-2026-10-22
