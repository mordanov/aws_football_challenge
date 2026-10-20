# Redesign 2: a team that remembers

Five coordinated, deterministic player controllers and a **durable offline
football trainer**. Standard-library Python 3.12; no LLM, AWS account or
third-party dependency required for local control and training.

The initial implementation and `redesign_1/` are unchanged. Work is on branch
`redesign-2-learning-trainer`; nothing is committed, merged or deployed.

Start with [DESIGN.md](DESIGN.md) and
[PROTOCOL_RESEARCH.md](PROTOCOL_RESEARCH.md). The research found that several
commands and parameter names in the original implementation were incompatible
with AWS's public sample. This version follows the executable sample rather
than conflicting workshop prose.

## What changes on the pitch

| Role | Responsibilities |
|---|---|
| GK, index 0 | Cover the ball-to-goal angle and projected shot crossing; sweep only if safely first; distribute through a clear lane to an unpressured receiver, using THROW/KICK by distance. |
| DEF, index 1 | Retain central goal-side cover; engage only when appropriate, with MID covering; favor a safe wide outlet when possessing. |
| MID, index 2 | Screen and cover when DEF engages; supply diagonal support, switch away from pressure and choose safe progressive/counter passes. |
| FWD1, index 3 | Link diagonally, offer the shorter attacking outlet and occupy the negative-y lane. |
| FWD2, index 4 | Stretch the line with a deeper run in the positive-y lane; remain distinct from the link forward. |

Every player computes the same assignment from canonically ordered state.
There is one primary presser, one free-ball interceptor, distinct reachable
markers and a covering outfielder. When DEF goes for a loose ball, MID also
covers. When our teammate has possession, forwards **support rather than
intercept their teammate's ball**.

Interception considers velocity and approximate arrival time. Shooting considers
angle, range, all opponents including the keeper, and short-horizon lane
occupancy. Pass evaluation considers lane safety, receiver pressure, progress,
role and score. Through passes exploit a currently observed high opposing line.
Press intensity and movement sprinting conserve depleted stamina.

Late leads favor safer outlets; late deficits permit greater attacking risk.
The command set deliberately avoids unsupported dribbling/clearance/tackle
commands, ambiguous aggressive/defensive stance values, reckless slide tackles
with unknown foul costs, and guessed restart scripts.

These are tactical hypotheses, **not evidence of winning matches**. Prediction
uses conservative two-dimensional linear approximations, not engine physics.

## Local commands

Run from the repository root:

```bash
# Verify local behavior; scenarios are synthetic, not real matches.
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s redesign_2/tests -v

# One invocation: command-array JSON on stdout, structured decision JSONL on stderr.
python3 -m redesign_2.agent decide \
  --input /path/to/invocation.json \
  --match-id match-001 --opponent-id opponent-A \
  2>/path/to/decision.jsonl

# After EVERY match: import, write a coaching report, train and export a candidate.
python3 -m redesign_2.agent learn \
  --db /path/to/coach.sqlite \
  --manifest /path/to/match-001.json \
  --records /path/to/match-001.jsonl \
  --output /path/to/candidate.json

# Read remembered mistakes, evidence, remedies and opponent scouting.
python3 -m redesign_2.agent report \
  --db /path/to/coach.sqlite --match-id match-001

# Inspect evidence for changes and results grouped by opponent/profile.
python3 -m redesign_2.agent audit \
  --db /path/to/coach.sqlite --version trained-VERSION_FROM_OUTPUT

# Record explicit operator approval separately from candidate generation/export.
# This records intent, not proof that AWS deployment occurred.
python3 -m redesign_2.agent promote \
  --db /path/to/coach.sqlite --version trained-VERSION_FROM_OUTPUT \
  --note "Approved for controlled practice"

# Explicit rollback: re-export ANY previously trained version.
python3 -m redesign_2.agent export \
  --db /path/to/coach.sqlite --version trained-PREVIOUS_VERSION \
  --output /path/to/rollback.json

# Try a pinned candidate locally before deploying it.
python3 -m redesign_2.agent decide \
  --input /path/to/invocation.json --profile /path/to/candidate.json \
  --match-id match-002 --opponent-id opponent-A
```

`ingest` imports without training. `train --db ... --output ...` recomputes a
candidate from stored evidence without re-importing. All CLI failures return
nonzero with an explicit diagnostic; invalid imports roll back transactionally.
Export requires the destination parent directory to exist.

### Invocation contract

The public sample's envelope is:

```json
{
  "prompt": "{\"gameState\": {\"...\": \"actual state\"}, \"teamId\": 0, \"myPlayers\": [2]}"
}
```

`prompt` may also be an object. `gameState` must contain `tick`, `gameTime`,
`score.home/away`, all ten players and the ball. See
`tests/test_runtime.py::payload` for a complete **synthetic** sample-shaped
payload. New player fields are `agentId`, `teamCode`, `position`, `velocity`,
`stamina`; legacy fields are `playerId`, `teamId`. Team codes are
case-insensitive. Player IDs are 0..4 **within** each team.

The sample pitch is centered, x=-55..55, y=-35..35. Team 0 attacks +x; team 1
attacks -x. Match duration defaults provisionally to 300 seconds, not the
original agent's 120; an explicit `gameState.matchDuration` overrides it.
Capture a live payload to validate these assumptions for your actual event.

Stamina defaults to an explicitly fractional 0..1 scale. Use
`--stamina-scale 100` for percentage payloads. This is **not** guessed from
individual values: a depleted value of 1 percent must not become full stamina.

Only this conservative subset of public commands is emitted:

| Command | Exact parameters |
|---|---|
| MOVE_TO | `target_x`, `target_y`, `sprint` |
| PASS | `target_player_id`, `type`: GROUND/AERIAL/THROUGH |
| SHOOT | `aim_location`: BL/BR, `power`: 0..1 |
| GK_DISTRIBUTE | `target_player_id`, `method`: THROW/KICK |
| PRESS_BALL | `intensity`: 0..1 |
| MARK | `target_player_id`, `tightness`: LOOSE/TIGHT |
| INTERCEPT | `aggressive`: boolean |
| SET_STANCE | `stance`: 0, the only unambiguously balanced value |

Every command has `teamId`, `playerId`, `parameters`, `duration: 0`.
The runtime returns a **JSON array string**, matching the sample.

### Critical unresolved possession limitation

The public new-format fixture reuses `agentId_N` on both teams but can omit a
team discriminator next to `possessionAgentId`. The reference implementation
then chooses the first matching player in array order; its own source
acknowledges the ambiguity. That is not reliable ownership evidence.

This agent uses `possessionTeamId` when supplied and otherwise preserves
unknown ownership. Explicit `isFree: true` overrides a stale holder. It never
guesses possession from player proximity or array order. Unknown possession
keeps conservative shape: no false passes, shots or free-ball interceptions.

If your live payload has this ambiguity, correct ownership needs additional
authoritative platform information. **This is a real integration blocker for
effective attacking play, not something a clever tactic can safely infer.**
`PROTOCOL_RESEARCH.md` section 14 documents the source-level finding.

## Learning interchange format

There is **no verified public football report/replay API**. The following
manifest and records are this project's own explicit interchange format.
It does not pretend the platform emits these fields.

### Manifest

```json
{
  "match_id": "match-001",
  "opponent_id": "opponent-A",
  "team_id": 0,
  "complete": true,
  "result": [1, 2],
  "played_at": "2026-10-20T10:00:00+00:00"
}
```

`result` is `[our_goals, opponent_goals]`, independent of home/away. A manifest
must contain exactly these six fields. `played_at` requires a timezone.
`complete` means the final result is available, **not** that every tick was
recorded. If the result is unavailable, use `complete: false, result: null`.
Later re-import the same identity/time with `complete: true` and a result to
finalize it. A finalized result cannot be silently changed or downgraded.

### Records

One JSON object per line, produced directly by `Runtime.handle`:

- `type`: `decision` or `error`;
- `match_id`, `opponent_id`, `team_id`, `player_id`, `profile_version`;
- decision: `snapshot`, `command`, `reason`, `alternatives`, `assignments`,
  `latency_ms`, `learning_context`;
- error: `tick` if recoverable, `error`, conservative fallback `command`.

Snapshots contain `team`, `tick`, `clock`, `duration`, sorted `players`
(`team,id,x,y,vx,vy,stamina`), `ball`, `velocity`, `owner` as `[team,index]`
or null, `free`, and home/away `score`. Do not edit snapshot coordinates or
guess missing possession when preparing logs.

Merge all five players' decision lines into the same match-scoped JSONL
export. The trainer sorts ticks, deduplicates identical views and repeated
imports, and rejects conflicting snapshots/actions rather than treating them
as five independent observations. Reordered imports are safe. Append missing
records by importing again; identity and the pinned profile must agree.
Observed scores cannot exceed the declared final totals, for either team.

### Associating real logs with a match

The public sample does not establish stable match/opponent IDs. A long-lived
runtime therefore need not set `AFC_MATCH_ID`/`AFC_OPPONENT_ID`; its records
will visibly say `learning_context: missing`. After **explicitly isolating
exactly one match's logs**, attach your manifest with:

```bash
python3 -m redesign_2.agent learn \
  --db /path/to/coach.sqlite --manifest /path/to/match.json \
  --records /path/to/exactly-this-match.jsonl --bind-context \
  --output /path/to/candidate.json
```

`--bind-context` fills missing match/opponent IDs only. It does not overwrite
existing IDs, team identity or profile version. Without the flag unidentified
records are rejected. Never combine multiple matches and label them one match.
Automatic discovery of match boundaries/results still requires a genuine
platform export/API or a trusted collector that knows those identities.

## How the trainer improves

**Every import produces a persistent coaching report**, including after wins:

1. Validate identity and profile, canonicalize snapshots, ingest transactionally.
2. Analyze our attempted actions and bounded next observations.
3. Remember evidence-qualified lessons with role, tick references, confidence
   and a concrete remedy.
4. Scout opposing line height, width, pressing density, flank occupancy,
   recurring possession players and possible possession losses.
5. Record proposals; after enough distinct recent matches, publish a small
   bounded candidate adjustment with a preserved audit.
6. Compare future results by opponent and profile; keep prior versions available.

Examples of remembered lessons:

| Evidence | Lesson and response |
|---|---|
| Our pass attempt, then opposing possession within at most two ticks and 4.1 seconds | **Possible**, not confirmed, failed outlet; tighten passing safety and offer wider support. |
| No safe supported carrier action available | Widen diagonal support instead of forcing a pass. |
| A shot outside baseline range/angle/lane checks | Recycle more patiently; reduce shooting range. |
| Opponent in the central danger zone without a goal-side central outfielder | Preserve DEF/MID cover when engaging. |
| Dangerous unmarked off-ball runner | Prioritize goal-danger marking over high pressing. |
| Recoverable runtime failure | Fix the integration before tuning football parameters. |
| Persistent opposing high line | Stretch a forward and favor safe counter space. |
| Repeated observed opposing losses | Modestly expand pressing reach against that opponent. |

Score changes are attribution boundaries: neither our failed-pass inference
nor opposing possession-loss inference crosses a scoring interval.
Snapshot transitions do **not** confirm an executed pass, interception,
accepted command, goal scorer, timeout or hidden opponent strategy.
`confirmed_passes` and `confirmed_goals` deliberately remain zero without
authoritative events. Gaps and ambiguous ownership leave attempts unknown.
An observed opponent possession change is not proof our press caused it.

General candidate updates require at least **three distinct complete-result
matches within 30 days**, each with at least three observations supporting
the same lesson. General settings are recomputed from baseline, not compounded
each time the command runs:

- passing safety +0.25 for recurring risky outlets;
- shot range -1 metre for recurring poor shots;
- support width +1 metre for recurring absent outlets.

Opponent-specific adaptations require at least two matches and six unique
snapshots. Evidence has a 30-day half-life and shrinks toward general defaults
with `weight = effective_samples / (effective_samples + 12)`. Pressure adjusts
pass safety; a high line adjusts support width; repeated possible possession
losses adjust press radius. Old or sparse observations have little influence.
Loss-based pressure changes use separately decayed loss evidence and require
at least 1.5 effective losses; unrelated recent clean play cannot revive old
opponent mistakes.

Hard parameter bounds are pass margin 2.5..4.5 m, shot range 20..34 m, press
radius 12..30 m and support width 10..22 m. Versions are content-addressed over
the training evidence and UTC evaluation date. Repeating unchanged training
on the same date is idempotent. Old profiles remain stored for exact export.

These are conservative heuristics, not reinforcement learning or causal
optimization. The audit compares results but does not automatically claim a
candidate is better or promote it into deployed agents. **Review and deploy
the same chosen version for all five players before the next match.** Weak and
qualified proposals are persisted separately from operator approval events.
`audit` retrieves both after restart. `promote` records an approval note and
timestamp, not a successful deployment; exporting alone never implies approval.

## AWS operations

`agent/main.py` is an optional AgentCore entrypoint. It imports
`bedrock_agentcore` only when started; local commands do not need it.
For deployment, package this folder as the application root and install
`bedrock-agentcore` in that deployment environment, following the official
sample's IAM/AgentCore registration process. The local repository installs
and provisions nothing.

Start with `python3 -m agent.main` from inside `redesign_2/`. Register five
runtimes; each invocation selects its one index via `myPlayers`, rather than
guessing a role from global player IDs.

| Setting | Meaning |
|---|---|
| `AFC_PROFILE` | Packaged JSON profile; absent means explicit baseline. Loaded once at process startup. |
| `AFC_STAMINA_SCALE` | `1` or `100`; must match actual event payloads. |
| `AFC_MATCH_ID` | Optional trusted match identifier for a match-scoped runtime. Do not leave one stale value across autoplay matches. |
| `AFC_OPPONENT_ID` | Optional trusted opponent identity; enables that opponent's learned settings at startup. |

Without trusted opponent identity, only general learned settings apply.
Opponent-specific automatic selection during a long-lived autoplay deployment
needs a trusted integration that supplies identity before constructing the
runtime; no such official API was verified. Do not invent it.

Collect structured decision lines from CloudWatch, associate a manifest with
one actual match, run `learn`, review its audit, package the chosen profile
into **all five** runtime deployments and verify equal versions in their logs.
No per-tick storage lookup, inter-agent message, LLM call or hot reload occurs.
Export uses a temporary file, fsync and atomic replacement; never edit profiles
in place during a match.

Console output and the AgentCore transport can still block or cold-start.
`latency_ms` measures local planning/record construction before log emission,
**not** total invocation latency. Measure real end-to-end latency at deployment.
Public sources disagree about the response budget and match length. No local
microbenchmark can prove compliance with a remote deadline.

## Layout and remaining checks

`agent/model.py` defines the immutable normalized contracts; `protocol.py`
owns wire validation; `tactics.py` owns shared role decisions; `runtime.py`
records decisions; `profile.py` loads and exports frozen settings; `trainer.py`
owns SQLite evidence/coaching; `__main__.py` provides the offline CLI.

`CONTEXT.md`, `state_machines.md`, `data_flow.md` describe canonical terms and
boundaries. Tests include persistence after reopening SQLite, shuffled and
duplicate imports, partial-to-final transition, stale scouting, unconfirmed
outcomes, all player roles and a learned correction that changes a risky
midfield outlet without changing keeper shape.

Before competition: capture an actual invocation, resolve ownership ambiguity,
confirm stamina scale, match duration, command behavior, timing and true
result/replay delivery. Then evaluate over multiple real matches against the
balanced, attacking and defensive practice opponents and held-out teams.
There is no real-match win-rate claim and no invented simulator benchmark.
