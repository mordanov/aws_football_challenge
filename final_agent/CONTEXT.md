# Learning football team

Five coordinated players and their durable offline coaching loop.

**Deployment source**:
The self-contained `final_agent` copy used to package all five players and their
pinned profile. `redesign_4` remains an unchanged reference; existing AWS resource
names identify the same team, not a dependency on that reference directory.

## Language

**Snapshot**:
One normalized observation interpreted from our team's perspective in canonical
sample-pitch coordinates, not necessarily the server's physical units.
Includes a recognized coach preset. A team-less `agentId_N` holder (official
server format) is resolved to the same-index player clearly on the ball
(within 3 m and at least 1 m nearer than the other); otherwise ownership stays
explicitly unknown.
_Avoid_: confirmed match event, complete replay.

**Competition adapter**:
Session-selected conversion between compact live-match observations/commands
and canonical tactical coordinates, separate from the synthetic Fitness Check.
Its geometry calibration is provisional, not a guaranteed platform contract.
The adapter supplies the canonical movement x-limit to planning so goalkeeper
trajectory projection uses the same reachable plane as the wire command.
_Avoid_: automatic per-player unit guessing, proof of improved match results.

**Decision**:
A selected attempted command, not evidence the server executed it.
Carrying shares movement's stamina/distance sprint guard. A clearly stranded
opposing keeper permits bounded longer shots, subject to normal angle and
lane checks; it does not establish an empty net or a guaranteed goal.
Keeper reactions to explicitly free incoming goal threats retain the stamina
guard but permit short nonzero-distance sprints. Threat classification may use
the modeled goal crossing while movement still targets its reachable plane.
An unpressured clear next segment rewards carrying over sideways recycling,
without overriding protecting/slow-tempo posture or safe shots. Against a keeper
projected behind the carrier, an unobstructed center shot is preferred to corner
aiming; this is a targeting policy, not a guarantee of shot accuracy.
Free-ball interception is outfield-only; the keeper retains its bounded goal-line
position. The nearest active defender may press in midfield if the midfielder
takes cover. A committed midfielder can support deeper attacks while retaining
the profile's attack cap and fixed formation.
When a direct carry is blocked, short predicted-clear diagonal or lateral escapes
may replace its target; an escape is not proof the ball will stay with the carrier.
Routine pressing can prefer a materially fresher near-tie arrival, but close duels,
deep defensive threats and free-ball interception retain the quickest response.
Unpressured carriers reserve stamina away from goal instead of always sprinting.
_Avoid_: outcome, accepted action.

**Sudden death**:
A tied match continues after the 120-second regulation period until the first goal
or 240 seconds. `duration=120` remains regulation time, not an inconsistent input;
the current policy keeps tied extra-time posture balanced.
_Avoid_: duration override, guaranteed all-out attack.

**Keeper outlet**:
With known keeper possession, the furthest-forward normally safe distribution
is preferred, with existing outlet scores breaking equal-progress ties.
If all fail the normal filters, rank non-self teammates with the existing outlet
score and attempt an explicitly contested emergency kick. Neither ordinary nor
slow-tempo holding competes with keeper distribution. This is a legal-action
fallback, not proof the receiver is safe or the pass will complete.
_Avoid_: guaranteed clearance, inferred keeper possession.

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
tempo, formation, carry) expressing how the team plays.
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

**Evidence packet**:
Expiry-stamped offline claims with unique IDs and resolvable source digests.
Preparation preserves telemetry/report contradictions; a digest is not a
cryptographic signature or proof the source is true.
_Avoid_: verified match outcome, signed evidence.

**Run artifact**:
Offline proposal, rejection or failure with usage receipts and preserved
evaluation spans. Reevaluation uses that original grounding, not invented
empty evidence. The unapproved `proposed_profile` is retained across outages
and rejections so reevaluation need not repeat paid inference; `candidate_profile`
is populated only for a passing proposal. A passing score never authorizes deployment.
_Avoid_: approved profile, deployed team.

Advisor replies accept plain JSON or a complete outer JSON Markdown fence;
extra prose remains invalid. Response usage survives validation failures.

**Generation diagnostics**:
Bounded sidecar metadata for an analysis-script generation attempt: fixed
validation reason, response size/digest, format, stop reason and provider usage
when available. It contains no prompt, generated code or raw provider exception.
Service failures include allowlisted provider/transport codes and validated
HTTP status/request ID; unrecognized codes remain `UNKNOWN`.
_Avoid_: executable script, raw model transcript.

The model generates one `analyze(evidence)` function, checked structurally with
AST parsing without execution. A deterministic wrapper loads UTF-8
`evidence.json`, calls it and validates/prints its dict result. The model receives
the packet schema, not values; review still checks data dependence and safety.
An analysis result is exactly one finite JSON object covering the whole stdout;
pretty-printing is allowed, but surrounding logs or multiple objects are not.

**Shooting counts**:
Deterministically parsed canonical observed/report count claims, with count,
found/missing status, claim ID, kind and source IDs. Narrative advice is not a
count claim. Missing differs from zero; malformed or duplicate canonical claims
fail closed. Counts describe issued commands/report claims, not executed actions
or goals. The advisor interprets these values; it does not compute them.
