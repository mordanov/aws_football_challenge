# Data flow - learning football team

## Live decisions

**Source:** public sample envelope, `prompt -> {gameState, teamId, myPlayers}`.
**Sink:** command-array JSON string and structured JSONL record.
**Transformations:** validate identity -> normalize state -> compute canonical
shared assignments -> role-specific choice -> validate command -> serialize ->
bounded best-effort telemetry enqueue and independent command return.
**Shape at each step:** envelope -> immutable `Snapshot`/player index ->
`Decision(command,reason,alternatives,assignments)` -> wire command with
`commandType,teamId,playerId,parameters,duration` and snapshot/context/warnings.
Recognized `coachInstruction` survives normalized snapshot serialization.
The coach preset comes from the platform, or from the in-agent rules coach when
`coach_policy` is `rules` and no platform preset is present. It is derived from
the snapshot alone, so all five players agree. The profile `style` (block depth,
attackers committed, tackle aggression, tempo, fixed formation, carry) shapes
anchors and option scores inside `decide`.
The actual pinned AgentCore adapter wraps the array string in one SSE data event.
On AgentCore each of the five runtimes runs the same staged code and profile;
its `AFC_PLAYER_ID` seat only fills a missing `myPlayers` (explicit routing wins).
**Source:** `agent/protocol.py`, `agent/coach.py`, `agent/tactics.py`,
`agent/runtime.py`, `agent/telemetry.py`, `agent/main.py`.

## Durable coaching

**Source:** exactly-one-match JSONL export plus explicit six-field manifest.
**Sink:** SQLite matches, observations, decisions, errors and coaching reports.
**Transformations:** optional explicit binding of missing match/opponent IDs ->
identity/profile validation -> sorted deduplicated observations -> bounded
elapsed-time transition inference (including explicit free-ball flight) ->
evidence/role/remedy lessons and opponent scouting. Log drop/failure counters
remain in raw exports; absence of records never proves absence of errors.
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

Archetype choice runs alongside this. Local campaign evidence
(`docs/strategy_report.md`) selects an archetype profile, which is committed as
`profiles/recommended.json` and carried through retraining via `--base-profile`.
The optional advisor reads a campaign or match report and writes a validated
`status: "proposal"` JSON file. An operator decides whether to deploy it with
`AFC_PROFILE`; nothing deploys automatically.
**Source:** `agent/archetypes.py`, `agent/advisor/`, `profiles/recommended.json`.

There is no live database, LLM or network dependency in decision logic; the
advisor runs only off-tick.
Actual platform result/report delivery and trusted match-boundary collection
remain unverified integration responsibilities.
