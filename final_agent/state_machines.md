# State machines - learning football team

Deployment-source extraction changes no tactical transitions. `final_agent`
starts with the merged `r4v7_route_stamina` behavior; staging is local-only.
An AWS rollout is a separate explicit operation using the existing resource
identities, not an automatic consequence of copying or merging this directory.

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
Safe keeper distributions are ordered by forward progress before outlet score;
the contested fallback retains score ordering and never outranks a safe option.
Free ball -> nearest active outfield interceptor, never keeper sweep.
Opponent possession -> nearest active outfield presser within the configured
radius, including the defender; defender pressing transfers cover to midfielder.
For routine pressure with attack-direction ball progress above -30 and beyond
a three-unit duel, a teammate with at least 0.2 more stamina may take over when
arrival is within 0.35 seconds and the teammate is inside the press radius.
Choose the freshest qualifying teammate, then arrival/id. Immediate duels,
deep threats and all free-ball interception retain the previous fastest selection.
Own possession -> midfielder support may advance to canonical attack x=40,
still subject to the profile's committed-attacker cap. Defensive anchors remain.
Carry scoring rewards a clear next twelve-unit segment only when the target is
forward, farther than five units, lane clearance is at least six units, tempo
is at least 0.5 and posture is not protect. Sprint stamina guards remain.
Direct carry lane below three -> evaluate four short targets (eight units forward
or lateral, with lateral offsets +/-8); retain only displacements of 5..12 units
after clamping, so a touchline cannot turn an escape into standing still.
Targets remain within x +/-50 and the
shared compact-pitch lateral bound so wire clamping cannot change the planned
route. Replace only with clearance at least three and at least one unit better,
using current and 0.8-second opponent projections. If none qualifies, retain the
existing target; pass and shot candidates keep their guards and score competition.
Carry sprint additionally requires stamina at least 0.55, an opponent within
eight units currently or after 0.8 seconds, or distance to goal at most 29 units. The existing minimum
0.35 stamina and over-five-unit movement guards still apply.
Tied clock >= regulation duration -> posture stays balanced. Regulation remains
120 seconds; sudden death ends on the first goal or at 240 seconds. No automatic
tempo/formation change is introduced for extra time.
Stranded-keeper shots -> CENTER when its lane passes the normal shot guard;
otherwise corner candidates retain their usual checks and score ordering.
**Source:** `agent/runtime.py`, `Runtime.handle`; `agent/tactics.py`, `decide`,
`carry_option`, `shot_option`, `carrier_options`.

## Decision telemetry

**States:** queued, in flight, delivered, failed, dropped; open/closed writer.
**Transitions:** nonblocking enqueue -> background sink; full or closed queue ->
dropped counter; expected sink error -> failed counter and exception-class
diagnostic. No retry. Closing stops admission and attempts a bounded drain/join.
Process freeze/termination can lose pending records without an acknowledgement.
**Source:** `agent/telemetry.py`, `DecisionLog`.

## Match profile selection (opt-in)

**States:** unbound, active_read, content_validated, pinned, cached; failed.
**Transitions:** recognized UUID and matching session team/seat -> consistent
pin read -> existing immutable pin, or active pointer/content validation ->
transactional unchanged-generation check plus conditional pin creation.
Concurrent loser/uncertain acknowledgement -> read winning pin, never a local
fallback. Bounded contention, missing/corrupt/incompatible content or unavailable
registry -> `profile_selection_error` and failed invocation. Cache recovery
reuses the persistent pin; interleaved matches keep request-local settings.
Synthetic fitness retains the static profile. Default startup has no selector.
The separately gated automatic learner may advance ACTIVE for new pins only;
running match bindings remain immutable.
**Source:** `agent/profile_registry.py`, `agent/runtime.py`, `agent/main.py`.

## Provisional Memory delivery (opt-in)

**States:** ignored, validated, PENDING, SENT; failed/retry/dead-letter.
**Transitions:** subscription from one trusted seat/log group -> validate the
whole batch -> content-addressed outbox create/read -> exact Memory event ->
conditional SENT acknowledgement. Duplicate SENT -> no publication. PENDING
retry reuses persisted content, timestamp and token; failed required writes
raise rather than mark SENT. Exhausted Lambda async delivery -> failure queue
and alarm; operator replay preserves outbox identity.
Collection-disabled configuration removes subscriptions, not existing evidence.
No clock, score, idle period, Memory write or new UUID transitions a match to
complete/aborted. All observations remain provisional, including overtime.
**Source:** `agent/learning_observations.py`, `agent/learning_collector.py`,
`deploy/learning.py`.

## Automatic candidate acceptance (opt-in)

**States:** insufficient_evidence, reserved_attempt, evaluated, rejected,
promoted, stale_baseline; failed.
**Transitions:** recent acknowledged outbox sessions after analysis delay ->
exact Memory integrity check -> homogeneous current-profile observations ->
three independently supporting matches -> bounded numeric proposal.
Conditional generation reservation succeeds once -> fixed paired simulator
evaluation -> rejected/failed receipt or conditional ACTIVE generation/digest
update plus promotion receipt. Uncertain promotion response -> read receipt.
Uncertain attempt write -> continue only if its invocation owner matches.
Oversized raw evaluations -> immutable chunks with a digest/reference receipt.
Concurrent baseline change -> stale_baseline, never overwrite. Repeating a
reserved generation -> already_attempted, not another significance test.
Neither analysis delay nor promotion confirms a match end or real efficacy.
The S3 backend conditionally creates a winning match pin from a validated ACTIVE
snapshot; a racing promotion can leave that new match on the preceding generation,
but never splits its five players. ACTIVE compare-and-swap is its promotion commit.
Evaluation budget too short -> continuation before reservation, not failed attempt.
**Source:** `agent/learning_source.py`, `learning_policy.py`, `learning_worker.py`,
`learning_evaluation.py`, `profile_registry.py`.

## S3 learning wake-up

**States:** idle, dispatch_pending, dispatched, waiting_for_quiet, analyzing,
continued, settled; expired.
**Transitions:** observation delivery -> conditional work reservation and latest
activity revision; Lambda acceptance -> dispatch acknowledgement. Further logs ->
same owner, increased revision. Quiet activity delay -> exact evidence analysis.
Insufficient invocation budget -> same-owner continuation without consuming an
evaluation attempt. Settlement checks the observed revision; concurrent activity
keeps work pending. Expired lease -> new owner on subsequent collection activity.
Async errors exhausted -> retained S3 invocation record and CloudWatch metrics.
No transition claims official match completion or unlimited automatic retry.
**Source:** `agent/s3_trigger.py`, `agent/s3_worker.py`, `deploy/s3_learning.py`.

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

## Offline run

### Scouting review

**States:** telemetry_saved, locally_prepared, published, insufficient_evidence,
review_required, advisor_proposal.
**Transitions:** validated complete manifest plus nonempty canonical decisions ->
locally_prepared; explicit `publish` with matching note/packet -> published.
Invalid/conflicting identity, geometry, clocks or expiry -> explicit failure,
not a default scouting observation. Publication is not implied by preparation.

`prematch` with confirmed opponent and timezone-aware kickoff -> read only exact,
unexpired notes played before kickoff, excluding the upcoming match itself.
Duplicate notes do not add independent matches; conflicting versions for the
same match fail. For each pattern: positives in >=2 distinct matches, >=3
distinct-clock positive samples and sample fraction >=0.2 -> review_required;
otherwise -> insufficient_evidence. This threshold is only a review heuristic,
not tactical validation, approval or an automatic profile transition.

Explicit existing `run` using the pre-match evidence -> evaluated proposal or
rejection/failure under existing guards. No scouting command transitions to
approved or deployed, and no live decision depends on Memory availability.
**Source:** `agent/advisor/offline/scouting.py`, `memory.py`, `__main__.py`.

**States:** evidence_prepared, advice_retrieved, advice_evaluated, proposal, rejected, failed; published.
**Initial:** evidence_prepared (local `prepare` command; no AWS calls).
**Transitions:**
- evidence_prepared → advice_retrieved: `run_offline()` calls Memory.retrieve; `OfflineServiceError` → failed.
- advice_retrieved → advice_evaluated: advisor calls Bedrock model; partial evaluation → failed; `OfflineServiceError` → failed.
- advice_evaluated → proposal: faithfulness ≥ `judge_min_score`.
- advice_evaluated → rejected: faithfulness < `judge_min_score` (successful evaluation, not a service error).
- proposal → published: operator calls `publish` explicitly (separate step; `run_offline()` never publishes).
- Any required-stage `OfflineServiceError` → failed.
Advisor format guard accepts complete outer JSON fences, not extra prose.
Post-response validation failure retains usage without exposing a candidate.
**Constraints:** proposal is never approved, deployed or promoted automatically. A separate operator
decision and `AFC_PROFILE` change are required. Evaluation score 0..4 is advisory; no score
gates deployment. `candidate_profile` in the artifact is a proposal, not the live profile.
**Source:** `agent/advisor/offline/workflow.py`, `agent/advisor/offline/__main__.py`, `docs/offline_coaching.md`.

Reevaluation requires persisted grounded spans. Successful numeric scoring at or
above the configured threshold creates a proposal only when its validated
unapproved profile is preserved; lower scores clear the candidate and reject.
The separate unapproved profile remains available for recovery without repeating
inference. Partial/failed service
results, missing spans, or a missing candidate cannot become a proposal.
Generated code moves from generated artifact to reviewed script to sandbox
execution only through the explicit execution flag and matching evidence digest.
Generation validation accepts raw JSON or a complete outer JSON fence, then
requires exactly one nonempty string `code` field. Truncation, output overflow,
malformed JSON or additional fields -> failed diagnostics, with no new script.
Neither generating nor unwrapping a script executes it.
An invalid AST function contract -> failed diagnostics. Valid generated
`analyze(evidence)` -> fixed file-loader/invocation/JSON-output wrapper appended.
Passing this guard is not approval: reviewed code must derive its result from
the loaded packet, not substitute literal counts.
Sandbox output -> analysis result only if complete stdout is a single finite
JSON object; multiline whitespace is accepted. Extra text or multiple results
-> failed execution with explicit cleanup outcome.
Trusted count preparation: canonical claims -> found(count, provenance) or
missing(null); malformed counts or multiple canonical claims of one kind ->
explicit failure, never a successful result carrying an errors list. Local
preparation failure writes no new script and blocks model advice. Successful
`count-analysis` creates a trusted artifact without model generation or execution.

## Offline resource provisioning

**States:** planned, create-returned, receipt-recorded, ready, stopped.
**Transitions:** approved apply -> SDK-validated control-plane create ->
immediate identity-bound receipt -> bounded readiness polling -> ready.
The first AWS/create/readiness failure stops the sequence before later
resources. A recorded ID remains recoverable when readiness times out; timeout
does not imply deletion. Foreign/corrupt receipts fail closed.
**Source:** `deploy/offline_coach.py`.
