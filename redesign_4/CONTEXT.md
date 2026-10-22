# Learning football team

Five coordinated players and their durable offline coaching loop.

## Language

**Snapshot**:
One normalized observation interpreted from our team's perspective.
Includes a recognized coach preset; missing/ambiguous ownership is explicitly
unknown, not inferred from player proximity.
_Avoid_: confirmed match event, complete replay.

**Decision**:
A selected attempted command, not evidence the server executed it.
_Avoid_: outcome, accepted action.

**Lesson**:
An observation or inference with evidence references, confidence and a remedy.
_Avoid_: proven cause, automatic success.

**Opponent scout**:
Recency-weighted observable opposing tendencies, not hidden intentions.
_Avoid_: opponent mind-reading.

**Tactical profile**:
Immutable versioned settings pinned consistently across all five players.
_Avoid_: live mutable shared memory.

**Parameter proposal**:
A stored suggested correction with evidence references and an eligibility status;
weak evidence remains a proposal without changing general settings.
_Avoid_: approved deployment.

**Profile approval**:
An explicit persisted operator decision to accept a candidate for evaluation.
It records intent, not proof of successful runtime deployment.
_Avoid_: export, proven improvement.

**Match manifest**:
Operator-supplied match identity, opponent identity and final-result availability.
Completeness describes result availability, not full replay coverage.
_Avoid_: official platform report, complete observation history.

**Decision telemetry**:
Best-effort queued evidence of attempted decisions with explicit loss/failure
counters. Delivery is independent of command return and is not a durable replay.
_Avoid_: acknowledged execution, complete recording.

**Supporting observation**:
A distinct tick supporting a particular lesson. Multiple players observing one
tick, or unrelated observations, do not multiply its evidence.
_Avoid_: arbitrary sample count, causal proof.

**Coach preset**:
One recognized mid-match instruction (`go_all_out_attack`, `slow_the_tempo`,
`press_high`, `shoot_on_sight`). A platform-sent preset overrides the coach policy.
_Avoid_: free-text coaching, formation change.

**Coach policy**:
The deterministic per-tick choice of a coach preset from score and quantized
match time, identical for all five players. Never changes formation.
_Avoid_: live LLM coach, shared coordinator.

**Formation**:
Pinned role anchors (e.g. `1-1-2`) in the tactical profile; immutable during a
match, changeable only between matches via profile approval.
_Avoid_: dynamic shape, in-match formation switch.

**Style**:
Bounded profile section (block depth, attackers committed, tackle aggression,
tempo, formation) expressing how the team plays.
_Avoid_: mode, strategy flag.

**Archetype**:
A named, pinned style plus settings (e.g. `park_the_bus`) evaluated as a whole.
_Avoid_: personality, dirty mode.

**Stand-in bot**:
A local opponent imitating a reported AWS practice bot; never the real bot.
_Avoid_: AWS bot.

**Advisor**:
Optional off-tick LLM component producing profile or parameter proposals that
still require profile approval.
_Avoid_: live agent brain, auto-deploy.
