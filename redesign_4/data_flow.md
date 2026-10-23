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
The deployment sets `AFC_STAMINA_SCALE=100` for synthetic Fitness Check stamina
and enables `AFC_COMPETITION_ADAPTER=1`. The SDK request context supplies the
session ID: `<UUID>-team[01]-pos[0-4]` selects the compact live-match contract,
while fitness sessions retain sample geometry and percentage stamina. Unknown
non-fitness session formats retain the sample contract with an explicit warning.
Live fractional stamina remains unchanged; live player position/velocity x/y
is multiplied by 8 into canonical coordinates. A 3D live ball uses x/z (y is
height); 2D balls retain x/y. MOVE_TO output is divided by 8 and bounded to the
observed player pitch limits, x +/-6.4 and y +/-3.5. Scale 8 is a provisional
first-match calibration, not a verified exact field dimension.
The same x-limit is passed to `decide(max_move_x=...)` in canonical units:
51.2 for compact matches, 54 for sample inputs. Keeper projection intersects
that bounded command plane; recorded canonical snapshot replay must supply
the original input-format limit explicitly rather than applying scale twice.
Telemetry includes `input_format`/`input_contract`, canonical snapshots and
wire-format commands/alternatives. A recognized match session supplies its UUID
as `match_id` when no operator binding is configured; opponent identity remains
unknown. Local CLI/simulator inputs retain the original contract.
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
