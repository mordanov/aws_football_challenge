# Data flow - learning football team

## Live decisions

**Source:** public sample envelope, `prompt -> {gameState, teamId, myPlayers}`.
**Sink:** command-array JSON string and structured JSONL record.
**Transformations:** validate identity -> normalize state -> compute canonical
shared assignments -> role-specific choice -> validate command -> record/return.
**Shape at each step:** envelope -> immutable `Snapshot`/player index ->
`Decision(command,reason,alternatives,assignments)` -> wire command with
`commandType,teamId,playerId,parameters,duration` and recorded snapshot/context.
**Source:** `agent/protocol.py`, `agent/tactics.py`, `agent/runtime.py`.

## Durable coaching

**Source:** exactly-one-match JSONL export plus explicit six-field manifest.
**Sink:** SQLite matches, observations, decisions, errors and coaching reports.
**Transformations:** optional explicit binding of missing match/opponent IDs ->
identity/profile validation -> sorted deduplicated observations -> bounded
transition inference -> evidence/role/remedy lessons and opponent scouting.
**Shape at each step:** manifest + record list -> canonical snapshot/decision
rows -> JSON report with outcomes, lessons, confidence and limitations.
**Source:** `agent/__main__.py`, `agent/trainer.py`.

## Next-match tactics

**Source:** stored matches, evidence and final-result availability.
**Sink:** SQLite profile/audit/proposals and separate approval events ->
atomically exported JSON -> five runtime boots.
**Transformations:** threshold general corrections -> recency-weight opponent
scouts -> bounded candidate -> content-derived version -> operator-selected
export/deployment -> startup profile/opponent selection.
**Shape at each step:** stored reports -> `Profile(version,settings,opponents)`
and audit -> JSON file -> immutable effective `Settings`.
**Source:** `agent/trainer.py`, `agent/profile.py`, `agent/main.py`.

There is no live database, LLM or network dependency in decision logic.
Actual platform result/report delivery and trusted match-boundary collection
remain unverified integration responsibilities.
