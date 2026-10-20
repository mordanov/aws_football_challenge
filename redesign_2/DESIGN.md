# Redesign 2: evidence-driven football trainer

Approved design direction: 2026-10-20. All deliverables live in `redesign_2/`.
The original implementation is in `initial_idea/`. `redesign_1/` is excluded
from investigation and implementation.

## Objective

Build fast, coordinated five-player control and a durable coaching loop that
learns from every available match without manufacturing evidence of success,
failure, or opponent intent. Improvements must survive process restarts.
No real matches or captured platform reports are currently available.

## Evidence and integration boundary

See `PROTOCOL_RESEARCH.md` for the public-source investigation and pinned
sample repository revision. Executable sample code takes precedence over
contradictory workshop prose, but is not proof of server behavior.

- Accept the sample's `prompt` JSON envelope containing `gameState`, `teamId`
  and `myPlayers`; return a JSON-encoded command array at the runtime boundary.
- Support new `agentId`/`teamCode` and legacy `playerId`/`teamId` players.
- Player identity is the pair (team, index), not the index alone.
- Normalize case in team codes. Respect explicitly free versus unresolved
  possession; do not assign ownership solely because somebody is nearby.
- Use the sample's centered 110-by-70 pitch and team-based attack direction.
- Emit only supported sample commands, with `teamId` and exact parameters.
  Movement uses `target_x`, `target_y`, `sprint`; shots use
  `TL`, `TR`, `BL`, `BR`, or `CENTER`.
- Do not issue the original implementation's unsupported `DRIBBLE`, `CLEAR`,
  or `TACKLE`. Moving while possessing is not assumed to implement dribbling.
- Use balanced stance only for emergency fallback: the meaning of stance
  values 1 and 2 conflicts between sources.
- Make stamina scale an explicit setting; do not guess a different scale for
  each player or reinterpret a depleted percentage as a full fractional value.
- Match length and timing are settings where not supplied. No promise about
  a one-second server deadline: public prose also mentions 500 milliseconds.
- Do not guess restart semantics from undocumented `playMode` values.
- There is no verified football report/replay/webhook API. The trainer's
  interchange format is our own, not a claim about the platform.

## Architecture

Keep four boundaries independently usable:

1. **Protocol adapter:** validate and normalize a public-sample payload into
   an immutable snapshot; validate commands on the way out.
2. **Team tactics:** pure deterministic snapshot-to-action functions. Every
   player computes assignments from a canonical ordering of the same players.
3. **Match recorder:** structured stdout records with snapshots, actions,
   alternatives, explanations, context and profile version. No storage or
   network access is required during live control.
4. **Offline trainer:** ingest records and a manifest into SQLite, analyze
   evidence, produce coaching reports and export versioned tactical profiles.

Use the Python standard library for local functionality and tests. AgentCore
is optional for local use. No LLM is needed for routing, decisions, validation,
outcome attribution, or parameter updates.

## Match and profile identity

The match manifest supplies explicit participant-owned `match_id`,
`opponent_id`, `team_id`, result and completeness information. Since public
fixtures do not establish stable platform match/opponent identifiers, do not
derive them from positions, clock resets, or process lifetime.

The runtime receives explicit deployment/invocation context for recording.
Missing learning context must be visible in diagnostics; action selection
can still run, but those records cannot silently become an identified match.

A profile is immutable for a match. All five runtimes must deploy or load
the same version before play, not refresh independently during ticks.
The profile includes general settings and optional opponent-specific
adjustments. Runtime selection uses the explicit opponent identity.

## Tactical plan

All geometry uses a team-relative attack frame. Clamp movement targets to
the pitch. Velocity prediction is a short-horizon approximation, not a
claim to reproduce the engine's physics.

### Whole team

- Exactly one primary outfield presser when opponents possess, with
  deterministic tie-breaking and stamina-aware eligibility.
- Exactly one primary interceptor when the ball is explicitly free; use
  approximate arrival time rather than current distance alone.
- Distinct markers selected for goal danger and reachable responsibility.
- Preserve a covering outfielder behind the press. If the defender engages,
  the midfielder covers instead of joining the chase.
- Coordinate useful attacking support when a teammate owns the ball.
  Do not mistake teammate possession for a loose-ball interception situation.
- Space forwards into different lanes and retain a safe outlet.
- Adjust risk for score and remaining time without assuming ranking rules.
- Use short-horizon player motion in lane and receiver evaluation.
- Select commands for tactical value, not command-diversity quotas.

### Goalkeeper

Position relative to the ball-to-goal angle, bounded near the goal. For a
goal-bound ball, use its velocity to anticipate the crossing point. Sweep
only an explicitly free ball when the keeper is safely first and within
a conservative operating area. Distribute to a safe receiver with THROW
or KICK based on distance and pressure. Do not invent dive/catch commands,
carry mechanics, or an uncontrolled keeper-up strategy.

### Defender

Preserve central, goal-side cover. Mark the most dangerous reachable
off-ball threat. Prefer delaying and covering to reckless slide tackling;
slide tackling is not assumed free of foul costs. Under possession, favor
a safe wide or progressive outlet rather than forcing a central turnover.

### Midfielder

Screen the central lane and supply a diagonal passing outlet. Switch play
away from pressure; protect the gap if the defender presses. Balance
progression against receiver pressure and transition exposure.

### Forwards

Use an asymmetric partnership: one stretches the defensive line while the
other links diagonally or occupies a distinct far-post lane. Use existing
PASS types for supported through-ball and aerial options, not new command
names. Evaluate shot angle, keeper coverage, range and predicted obstruction.
When defending, one can press while the other screens an outlet.

## Learn from every match

### Inputs and durable storage

Import a manifest plus structured runtime JSONL. A documented normalizer
can later translate actual portal exports once their schema is known.
Store matches, normalized observations, our decisions, evidence, opponent
statistics, parameter proposals and promotion history in SQLite.

Deduplicate observations across the five agents by match/tick/team and
decisions by match/tick/player. Detect conflicting duplicates instead of
silently overwriting evidence. Re-importing a match is idempotent.
Analyze observations in tick order, not file order. Reject incompatible
identity or profile context. Persist transactionally.

An explicitly incomplete match can supply limited observations but must
not count as a complete win/loss evaluation. A claimed final result does
not by itself establish a complete replay.

### Own mistakes and remedies

Record a lesson with evidence references, confidence, observed count,
affected role, remedy and follow-up measure. Candidate lessons include:

- A pass attempt followed by observed opponent possession: a possible
  failed outlet, not automatically proof that the pass caused the turnover.
- Repeated dangerous central exposure or uncovered runners.
- Repeated blocked/low-quality shot choices when safer alternatives existed.
- Coordinated-assignment violations or invalid-command fallbacks.
- Repeated safe progression opportunities left unused.

Outcome classification uses bounded observation gaps. Do not label a shot
as scored because any later score changed. Do not label every pass as
completed, every possession change as an interception, or missing records
as player idling. Explicit outcome evidence, inferred evidence and unknown
outcomes remain distinguishable.

Each imported match produces a readable coaching report, even when the
correct lesson is that evidence is insufficient to change a parameter.

### Opponent scouting

Track observable line height, pressure density, width/flank occupancy,
available counter space, recurring possession locations and observed
possession losses. Identify exploitable patterns without asserting hidden
commands or psychological intent.

Use per-opponent counts, uncertainty and recency weighting. Sparse or old
evidence shrinks toward general defaults. Do not count five agents viewing
the same tick as five independent samples. Keep our shortcomings distinct
from opponent tendencies.

### Parameter changes and experiments

Use a small, bounded set of meaningful settings, including pass safety,
shooting selectivity, press radius and support width. Sufficient evidence
can produce a bounded correction; weak evidence produces a proposal only.
Always retain the previous version and the evidence explaining a change.

Export profiles atomically. Keep observation, proposal and promotion
separate in history. Continue learning after wins. Do not introduce random
changes solely because a match was lost.

Record before/after results by opponent and profile. Describe improvement
as unproven until comparable results support it; ordinary score changes
are not causal proof. Provide rollback to an exported known version.

## Error handling

Malformed state, ambiguous identity, invalid command, conflicting imported
data, corrupt profiles and database errors must be explicit. At the live
boundary, log the failure and return a conservative validated command when
team/player identity is recoverable. Do not return a plausible successful
training result after an ingestion failure.

Keep training out of the live path. Local disk failures must not delay the
player because the player only emits stdout records. Production log delivery
and exporting manifests are operational integration responsibilities.

## Verification

Use focused standard-library tests:

- Real sample-shaped envelopes and command parameter contracts.
- Legacy/new state formats, case normalization, duplicate player indices,
  explicit free/unresolved possession, home/away symmetry.
- Identical assignments despite player list order, no duplicate marking,
  defensive cover, possession support and goalkeeper interception safety.
- Role-specific shot, pass, support and pressure scenarios.
- No unsupported commands or out-of-pitch coordinates.
- Duplicate/conflicting and shuffled imports, partial evidence and
  no false pass/shot completion claims.
- SQLite persistence across fresh trainer instances.
- Opponent-specific memory, recency, minimum evidence and bounded updates.
- Atomic versioned profile export, loading, rollback and invalid profiles.
- End-to-end record -> ingest -> report -> export -> changed targeted
  decision, without changing unrelated role behavior.

Measure local decision latency as a diagnostic, not proof of deployed
end-to-end deadline compliance. Do not build a toy simulator and present
its results as real match superiority.

## AWS deployment notes

Document five AgentCore runtimes with explicit role/team context, the same
packaged profile, and structured CloudWatch logs. Collect logs after play,
associate a match manifest, run the offline trainer, review/publish the next
profile and redeploy consistently. No infrastructure provisioning or paid
AWS calls are part of this implementation.

Live payload capture remains necessary to validate event-specific
differences, stamina scale, timing and real report availability.
