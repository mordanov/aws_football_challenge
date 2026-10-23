# State machines - learning football team

## Possession

**States:** free, known holder (team/index), unknown.
**Initial:** decoded from each authoritative observation.
**Transitions:** any state to another on the next snapshot; explicit free wins
over a stale holder, ambiguous reused IDs remain unknown.
**Source:** `agent/protocol.py`, `decode`.

## Runtime decision

**States:** received, normalized, planned, validated, serialized, returned; error.
**Initial:** received.
**Transitions:** SDK session contract selection -> normalize -> coach preset (platform preset kept; otherwise
`choose_preset` when `coach_policy` is `rules`) -> plan -> convert compact-match
MOVE_TO commands back to bounded wire coordinates when selected -> validate -> serialize
-> enqueue telemetry without waiting -> return. Recoverable errors with known team/player identity
enqueue a sanitized diagnostic and return validated balanced stance.
Unrecoverable identity errors fail rather than invent IDs. A team-less holder
is resolved by ball proximity when unambiguous; remaining unknown ownership
keeps defensive shape and adds an explicit warning.
With the competition adapter enabled, a recognized match-session ID selects
compact geometry/fractional stamina. Fitness sessions retain the sample contract;
unrecognized non-fitness IDs retain it with an explicit diagnostic warning.
The keeper projects only explicit free-ball flight onto its reachable movement
plane, not the deeper goal line or a dribbler's velocity. Threatening reachable
intersections can request sprint with the normal stamina guard, even for a
short nonzero move. A trajectory outside the lateral movement clamp but crossing
the existing modeled goal mouth is also urgent; the target remains clamped.
The keeper-plane crossing requires a positive time within three seconds;
the additional goal-mouth test independently applies the same time bound.
Carried/unknown or already-past trajectories do not enter this exception.
Carrying retains the ordinary stamina and over-five-unit distance guards,
including non-sprint movement when depleted.
The shot candidate's range can extend by 18 canonical units (capped at 50)
when the opposing keeper remains over 8 units behind the carrier after a
0.8-second projection. Existing angle and lane guards still apply.
Known keeper possession -> best normally safe distribution, or, if none passes
the filters, the highest-scoring contested emergency KICK to a non-self
teammate. Keeper holding is excluded, including at slow tempo. Uncertain
possession never enters this branch; outfield holding is unchanged.
**Source:** `agent/runtime.py`, `Runtime.handle`; `agent/tactics.py`, `decide`,
`carry_option`, `shot_option`, `carrier_options`.

## Decision telemetry

**States:** queued, in flight, delivered, failed, dropped; open/closed writer.
**Transitions:** nonblocking enqueue -> background sink; full or closed queue ->
dropped counter; expected sink error -> failed counter and exception-class
diagnostic. No retry. Closing stops admission and attempts a bounded drain/join.
Process freeze/termination can lose pending records without an acknowledgement.
**Source:** `agent/telemetry.py`, `DecisionLog`.

## Pass inference

**States:** attempted, in flight on explicit free snapshots, possible completion,
possible loss, unknown.
**Transitions:** increasing ticks with unchanged score within 4.1 elapsed game
seconds may resolve at the target receiver or opponent. Unknown ownership,
original-holder retention, score changes or timeout -> unknown. No transition
proves the command was accepted or caused the later possession.
**Source:** `agent/trainer.py`, `analyze`/`adjacent`.

## Imported match

**States:** absent, partial, complete.
**Initial:** absent.
**Transitions:** absent -> partial or complete on valid transactional import;
partial -> complete only with unchanged identity/time/profile. Re-import adds
nonconflicting records or is idempotent. Invalid/conflicting input rolls back.
Complete results cannot be silently revised or downgraded.
**Source:** `agent/trainer.py`, `Trainer.ingest`.

## Tactical learning

**States:** observations, proposals, bounded candidate, approved, exported, deployed.
**Initial:** observations.
**Transitions:** import -> evidence-qualified proposals; training -> bounded
candidate when three distinct supporting ticks in each of three recent complete
matches support a lesson, otherwise baseline general settings;
explicit `promote` -> durable operator approval event; explicit export ->
atomic JSON (export also permits unapproved candidates for evaluation);
operator deployment -> pinned runtime.
Rollback exports an older stored candidate. Deployment is an operational step,
not implemented infrastructure. No transition proves a tactical improvement.
**Source:** `agent/trainer.py`, `Trainer.train`/`export`; `agent/profile.py`.

## Strategy profile

**States:** archetype, campaign evidence, recommended, deployed; advisor proposal,
operator-reviewed.
**Initial:** archetype (named in `agent/archetypes.py`).
**Transitions:** local v2 campaigns -> evidence (pooled GD CIs, sensitivity,
formation follow-up) -> S2 decision rule -> `profiles/recommended.json` ->
operator deployment via `AFC_PROFILE` to all five runtimes. Advisor `pick`/`review`
-> bounded proposal file -> operator review -> deployment or discard. Formation and
style are fixed for the duration of a match; they change only between matches.
No transition proves the result transfers to the AWS platform.
**Source:** `agent/archetypes.py`, `agent/advisor/`, `docs/strategy_report.md`.
