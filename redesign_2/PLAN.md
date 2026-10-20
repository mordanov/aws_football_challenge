# Learning football agent implementation plan

**Goal:** implement the approved `DESIGN.md` with a usable local training loop.
**Architecture:** pure coordinated tactics; sample-protocol runtime; SQLite
trainer and immutable JSON profiles, with no training I/O during decisions.
**Stack:** Python 3.12 standard library; optional Bedrock AgentCore runtime.

## Assumptions and evidence

- Scope is only `redesign_2/`: explicit user instruction and design approval.
- Local SQLite, exported profiles and AWS documentation: explicit user choice.
- No real match data: user confirmed; generated scenarios cannot prove wins.
- Protocol contract: `PROTOCOL_RESEARCH.md` sections 2, 4–8 and pinned source.
- Report API remains unknown: section 9; use our own explicit manifest.
- Python 3.12.3 available: observed `python3 --version`.
- No existing build configuration or root contributor instructions: initial
  root file listing and allowed-path instruction search returned none.
- Existing tests use standard-library unittest: `initial_idea/tests/test_agent.py`.
- User approved new branch in the current checkout rather than a worktree.
- Timing, stamina and restart uncertainty accepted in approved `DESIGN.md`;
  expose stamina scale and avoid undocumented restarts/ambiguous stances.

Gate passed. Proceeding with implementation.

## File responsibilities and execution

1. **Contracts and boundary tests**
   - `agent/model.py`: immutable Player, Snapshot, Settings, Decision contracts.
   - `agent/protocol.py`, `agent/tactics.py`, `agent/runtime.py`,
     `agent/profile.py`, `agent/trainer.py`: public signatures.
   - Verify contracts compile using Python's in-memory `compile()`.
   - `tests/test_runtime.py`: sample envelopes, team ownership, movement,
     keeper/defender/midfielder/forward scenarios and shared assignments.
   - `tests/test_learning.py`: transactional ingestion, deduplication,
     attribution, persistence, scouting, bounded promotion and export/reload.
   - Run `python3 -m unittest discover -s redesign_2/tests -v`; observe the
     unimplemented boundary failures before implementing.
2. **Protocol, profiles and coordinated tactics**
   - Implement adapters with explicit free/unknown ownership and finite-value
     validation; canonical player ordering; supported command schemas.
   - Implement match-frozen profile loading and role tactics.
   - Verify with `python3 -m unittest discover -s redesign_2/tests -p test_runtime.py -v`.
3. **Offline learning**
   - Implement SQLite ingestion in a transaction; reject conflicting duplicate
     records, invalid identity and inconsistent profile contexts.
   - Analyze sorted deduplicated observations with bounded transition gaps.
   - Store evidence, coaching reports, opponent history and profile versions.
   - Export atomically; reproduce prior exports for rollback.
   - Verify with `python3 -m unittest discover -s redesign_2/tests -p test_learning.py -v`.
4. **CLI and end-to-end behavior**
   - `agent/__main__.py`: decide, ingest, report, train, export commands.
   - Exercise record -> ingest -> train -> export -> reload -> tactical effect.
   - Add malformed-input and revision regression cases when needed.
5. **Documentation and completion**
   - `README.md`: commands, manifest/record schemas, evidence limits,
     tactical rationale, learning recipes and AWS operations.
   - `CONTEXT.md`, `state_machines.md`, `data_flow.md`: local vocabulary and
     load-bearing contracts for future maintenance.
   - Full redesigned and original allowed tests, compile checks, whitespace
     checks, CLI smoke and latency diagnostic. Do not inspect `redesign_1/`.

No dependency installation, paid service calls or infrastructure provisioning.
Do not commit or merge implementation automatically.

## Completion checkpoints

- Contracts: seven initial modules compiled; original baseline had 10 passing
  tests. New boundaries initially failed with `NotImplementedError`.
- Runtime: protocol and role tests passed after correcting keeper exclusion
  from shot-lane obstruction. Defender free-ball engagement now retains MID
  cover; unknown ownership retains conservative shape.
- Trainer: SQLite ingestion, deduplication, cold-start persistence, partial
  finalization, evidence confidence and learned decision effects are covered.
- Review: reproduced and corrected score-crossing attribution, impossible
  final results, stale opponent-error revival and missing durable
  proposal/approval history.
- CLI/docs: record -> import -> learn -> export -> reload -> audit -> explicit
  approval runs across separate CLI processes. All code/docs remain here.
- Final verification: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover
  -s redesign_2/tests -v` passed all 42 tests after the final code changes.
  The unchanged original suite passed all 10 tests. Compile/whitespace checks
  passed across 19 files before the final import/type-guard cleanup.
- A 1,500-decision randomized local diagnostic produced validated commands
  throughout; this does not establish remote transport deadline compliance.
- Ruff's initial run found import, exception-type and immutable-default
  style issues. These were corrected manually. Final Ruff/format execution
  was blocked by the noninteractive command-approval environment, so no
  clean final lint/format claim is made.

Context maintenance gate passed. Updated: `CONTEXT.md`,
`state_machines.md`, `data_flow.md`. Exempted: none.

Branch retained as requested: `redesign-2-learning-trainer`. No automatic
commit, merge, push, infrastructure changes or deployment.
