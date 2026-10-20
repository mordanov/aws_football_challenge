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
