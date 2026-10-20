# Redesign 1 — "The Trainer": an agent that learns from every match

Status: approved design (2026-10-20). Supersedes `initial_idea/` for implementation.

## 1. Why a redesign

The original agent (`initial_idea/agent`) guessed the wire format. The AWS sample
repo (`aws-samples/sample-ai-possibilities`, folders `agentic-football-sample-agents`
and `agentic-football-coach`) is readable via `gh api` and settles the blockers:

| Blocker in 01-task.md | Resolved value |
|---|---|
| Payload | `payload["prompt"]` = JSON string `{"gameState", "teamId", "myPlayers"}` |
| Players | `agentId: "agentId_N"` + `teamCode: "home"/"away"` (old format: `playerId`/`teamId`) |
| Possession | `ball.isFree`, `ball.possessionAgentId` (NOT unique across teams) |
| Pitch | x in [-55, 55], y in [-35, 35]; team 0 (home) attacks +x, team 1 attacks -x |
| Deadline / length | 500 ms per decision; 5-minute match; invoked every ~2 s |
| Commands | MOVE_TO(target_x,target_y,sprint), PASS(target_player_id,type GROUND/AERIAL/THROUGH), SHOOT(aim_location TL/TR/BL/BR/CENTER,power), GK_DISTRIBUTE(target_player_id,method THROW/KICK), PRESS_BALL(intensity), MARK(target_player_id,tightness), INTERCEPT(aggressive), FOLLOW_PLAYER, SLIDE_TACKLE(target_player_id,sprint,distance), SET_STANCE(stance 0/1/2), CLEAR_OVERRIDE, RESET |
| Invalid in old agent | TACKLE, DRIBBLE, CLEAR, IDLE, `x`/`y` params, LEFT/RIGHT aims |
| Response | `json.dumps([cmd])`, cmd = `{commandType, playerId, teamId, parameters, duration}` |

Bonus signal: every opponent's `lastAction` / `currentAction` / `isSprinting` is
visible. That is our "eagle eye" on their strategy.

Still unknown: whether the payload names the opponent team; the exact `playMode`
values; whether `duration` counts agent ticks. The design does not depend on them.

## 2. Decisions

| Topic | Decision |
|---|---|
| Learning approach | Playbook + Lessons + Champion/Challenger (approach A) |
| Trainer autonomy | Fully automatic, bounded steps, automatic rollback |
| Persistent store | S3 JSON |
| LLM | Never on the tick path. Post-match analyst only (Bedrock) |
| Scope | agent core, learning, trainer, docs, tests (no simulator, no deploy infra) |

## 3. Architecture

```
redesign_1/
  agent/                 deployed as 5 runtimes (MY_PLAYER_ID 0..4)
    wire.py       real payload -> World ; internal cmd -> wire cmd; validation
    world.py      World/Pl model, normalised so WE ALWAYS ATTACK +x
    playbook.py   Playbook: bounded knobs, clamp(), with_nudges()
    tactics.py    geometry + team roles (presser / markers / interceptor)
    policy.py     World + Playbook -> command
    events.py     tick-pair -> events (shared with trainer)
    scout.py      in-match tallies -> nudges (hysteresis) + fingerprint
    recorder.py   compact per-tick log, background flush to S3
    store.py      S3 access (lazy boto3, background thread, failures swallowed)
    main.py       AgentCore entrypoint, budget, layered fallback, DECISION log line
  trainer/                post-match; CLI now, Lambda-handler-ready
    ingest.py     merge player logs -> match timeline
    lessons.py    error catalog: detector -> evidence -> knob delta
    dossier.py    opponent fingerprint, tendencies, nearest-neighbour lookup
    analyst.py    Bedrock LLM -> JSON knob proposals (validated, clamped)
    league.py     champion/challenger ratings, promotion/rollback, writes active.json
    run.py        orchestrates one training pass for one match
  tests/
```

### S3 layout (bucket from env `AFC_BUCKET`, prefix `AFC_PREFIX`, default `afc/`)
```
matches/<match_id>/p<N>.jsonl     per-player tick log (5 copies of the same world; ingest dedups)
matches/<match_id>/report.json    trainer output: KPIs, events summary, lessons fired, analyst notes
dossiers/<key>.json               opponent profile + counter-nudges + results vs them
playbooks/champion.json
playbooks/challenger.json
playbooks/history/<ts>-<name>.json
active.json                       {"playbook": "champion"|"challenger", "version": n}
lessons.json                      persistent error catalog with outcomes
league.json                       results per playbook version
```

### Hot path (per tick, target < 5 ms, hard budget 400 ms)
parse -> update scout -> policy(World, Playbook + nudges) -> validate -> respond.
No network I/O. The recorder only appends to an in-memory buffer.

### Match identity
There is no match id in the payload. A new match starts when `gameTime` goes
backwards (or the first tick ever is seen). `match_id = <teamId>-<UTC YYYYmmddTHHMM
of first tick>`, computed independently by each runtime; ingest groups files whose
ids differ by at most one minute.

### Playbook consistency across 5 runtimes
Coordination functions (presser, markers, interceptor) depend only on the World and
on playbook knobs that are swapped **only at kickoff**. Each runtime refreshes
`active.json` + playbooks in the background (on start and every 60 s); a newly
loaded version is applied at the next detected kickoff. In-match nudges change only
individual behaviour knobs (shooting, passing, lateral shift, line), so a runtime
that missed ticks cannot break team coordination.

## 4. The Playbook (bounded knobs)

| Knob | Default | Bounds | Step | Meaning |
|---|---|---|---|---|
| shoot_range | 24 | 14..34 | 2 | max distance to goal to shoot (m) |
| shot_clear_min | 2.0 | 1.0..4.0 | 0.3 | min lane clearance to shoot (m) |
| shot_power | 0.9 | 0.6..1.0 | 0.05 | |
| pass_lane_min | 3.0 | 1.5..5.0 | 0.3 | min lane clearance for passes (m) |
| pass_max_len | 35 | 15..55 | 3 | longest pass considered (m) |
| through_bonus | 20 | 0..40 | 5 | preference for THROUGH to an open runner |
| aerial_bonus | 0 | -10..20 | 5 | preference for AERIAL over blocked lanes |
| press_height | 0.15 | -0.4..0.6 | 0.1 | ball progress (fraction of half) above which DEF stops pressing |
| press_intensity | 0.8 | 0.4..1.0 | 0.1 | |
| def_line | -0.55 | -0.8..-0.2 | 0.05 | DEF anchor (fraction of half-length) |
| shape_shift | 0.0 | -0.25..0.25 | 0.05 | whole team forward/back |
| lateral_bias | 0.0 | -0.3..0.3 | 0.05 | shift DEF/MID toward a flank (fraction of half-width) |
| mark_tight | 1 | 0..1 | 1 | 1 = TIGHT, 0 = LOOSE |
| tackle_range | 2.0 | 0..3.5 | 0.5 | SLIDE_TACKLE trigger distance (0 = never) |
| gk_kick_min | 25 | 10..45 | 5 | GK KICKs beyond this target distance, THROWs below |
| sprint_stamina_min | 0.35 | 0.1..0.7 | 0.05 | below this, sprint only when critical |
| counter_trigger | 3 | 2..4 | 1 | opp outfielders in our half to trigger counter mode |

`Playbook.clamp()` enforces bounds; every learner output passes through it.

## 5. Events (`events.py`, shared by scout and trainer)

Detected from consecutive ticks `(prev, cur)` plus the commands issued on `prev`.
Ticks are ~2 s apart, so detection is approximate; each event carries context.

| Event | Detected when | Payload |
|---|---|---|
| PASS_OK / PASS_LOST | a player issued PASS on prev; ball now ours / theirs | from, to, length, clearance, zone, type |
| SHOT | a player issued SHOOT on prev | dist, aim, power; outcome goal / saved (opp GK has ball) / miss |
| TURNOVER | possession us -> them (not via PASS_LOST) | zone, carrier, pressed? |
| WON_BALL | possession them -> us | zone (opp half counts as press win) |
| GOAL_FOR / GOAL_AGAINST | score delta | flank, counter? (>= counter_trigger of ours in opp half on prev), zone of last possession |
| OPP_SNAPSHOT | every tick | line height, presser count, GK offset, carrier flank, lastAction list |

## 6. In-match scouting nudges (`scout.py`)

Rolling evidence with hysteresis (a nudge switches on after N confirmations, off after
N contrary observations). Nudges are additive deltas applied via `Playbook.with_nudges()`
(clamped).

| Observation | Nudge |
|---|---|
| Opp GK more than 8 m off its line for 5 ticks | shoot_range +6, shot_power 1.0 |
| >= 2 PASS_LOST this match | pass_lane_min +0.6 |
| Opp carrier flank share >= 65 % (>= 10 samples) | lateral_bias 0.15 toward it |
| Opp line high (their outfielders' mean progress in our frame < -5 m) | through_bonus +15, aerial_bonus +5 |
| Opp deep block (mean progress > 15 m) | shot_clear_min +0.5, shape_shift +0.05 |
| >= 2 GOAL_AGAINST on counter | def_line -0.1, press_height -0.1 |

## 7. Lessons (cross-match error catalog, `trainer/lessons.py`)

Each lesson: `{id, title, fired_matches, evidence, delta, status, applied_versions,
hurt_until}`. A lesson fires when its detector passes in a match. It is proposed for
the challenger once it fired in >= 2 matches (or once if `strong`). If a challenger
that contained it is discarded, it becomes `hurt` and is suppressed for 5 matches.

| id | Detector | Delta |
|---|---|---|
| long_shots_waste | >= 3 shots from > shoot_range-6 m, 0 goals from them | shoot_range -2 |
| shots_blocked | >= 50 % of >= 3 shots saved/missed with clearance < 2.5 | shot_clear_min +0.3 |
| passes_intercepted | PASS_LOST rate > 30 % over >= 6 passes | pass_lane_min +0.3, pass_max_len -3 |
| too_timid | < 2 shots and >= 6 ticks with our carrier within shoot_range+6 | shoot_range +2, shot_clear_min -0.3 |
| counter_conceded (strong) | >= 1 GOAL_AGAINST on counter | def_line -0.05, press_height -0.1 |
| own_third_turnovers | >= 3 turnovers in own third | pass_lane_min +0.3, gk_kick_min -5 |
| gk_dist_lost | >= 2 GK distributions lost | gk_kick_min +5 |
| stamina_collapse | mean outfield stamina < 0.25 in final 60 s | sprint_stamina_min +0.05 |
| press_futile | 0 press wins in opp half and >= 8 opp-half presser ticks | press_height -0.1 |

## 8. Opponent dossiers (`trainer/dossier.py`)

Fingerprint (fixed order, each scaled to ~0..1): mean line height, press rate,
GK offset, carrier left-flank share, mean shot distance, sprint share, and the share
of the 6 most common `lastAction` families (Press/Intercept, Mark/Follow, Move,
Dribble, Pass/Shoot, Stay/Idle). Key = opponent name if the payload ever has one,
else the dossier id of the nearest neighbour (Euclidean distance < 0.25), else a new
id. A dossier stores the mean fingerprint, tendencies, results vs them, and
counter-nudges (§6 table) that were active when we did best.

In-match: at `gameTime >= 30` each runtime computes the fingerprint from its scout,
finds the nearest cached dossier, and pre-applies its counter-nudges.

## 9. Champion / Challenger (`trainer/league.py`)

- `champion.json` = known-good playbook. `challenger.json` = champion + lesson deltas
  + analyst proposals, clamped, at most 3 knobs changed, each by at most one step.
- While a challenger exists, `active.json` alternates champion/challenger per match.
- After the challenger played 3 matches: promote if its mean goal difference >= the
  champion's mean over the same window (tie -> promote only if goals for is higher).
  Otherwise discard and mark contributing lessons `hurt`.
- Every change is written to `playbooks/history/`; manual rollback = copy a history file.
- Circuit breaker: a challenger that concedes >= 4 in a match is discarded at once.

## 10. LLM analyst (`trainer/analyst.py`)

Off the tick path. Input: KPIs, fired lessons, opponent tendencies, current knobs and
bounds. Output: strict JSON `{"proposals":[{"knob","delta","reason"}],"summary"}`.
Proposals are validated (known knob, numeric, |delta| <= step), clamped, and merged into
the challenger with lower priority than lessons. Any failure -> no proposals; the loop
works without the LLM. Model via `ANALYST_MODEL_ID`. The summary is stored in
`report.json` as human-readable coach's notes.

## 11. Tactics (policy)

- Coordination: one presser (nearest eligible outfielder, id tiebreak), distinct
  markers (DEF, MID) for the most advanced off-ball threats, one interceptor for
  loose balls (GK when the ball is in our box).
- Carrier: SHOOT if within shoot_range and the best aim lane is clear (TL/TR/BL/BR:
  near/far post chosen by lane clearance, low aims when GK is off the line); else best
  PASS (GROUND; THROUGH to an open runner on counter or vs high line; AERIAL when the
  ground lane is blocked but the receiver is open); else carry via MOVE_TO into space;
  in own third under pressure with no outlet, AERIAL to the furthest forward.
- GK: with ball -> GK_DISTRIBUTE (THROW if target closer than gk_kick_min, else KICK);
  loose ball in box -> INTERCEPT; otherwise MOVE_TO on the ball-goal line.
- Defend: presser -> SLIDE_TACKLE within tackle_range and outside our box, else
  PRESS_BALL; markers -> MARK; others -> MOVE_TO anchor.
- Sprint only if stamina >= sprint_stamina_min or the situation is critical.
- Posture (build / counter / protect / chase) from score, clock and coach text shifts anchors.

## 12. Error handling

Layers: rules -> minimal fallback (MOVE_TO anchor) -> `SET_STANCE{stance:0}`.
Every command is validated (type, required params, ranges). S3/boto3 failures are
logged once and never raised. One `DECISION` JSON line per tick: pid, tick,
gameTime, cmd, why, ms, nudges, playbook version.

## 13. Testing

- wire: AWS sample state (new format), old format, team 1 mirroring, prompt as JSON string.
- policy: open shot -> SHOOT with valid aim; blocked -> no SHOOT; one presser and
  distinct markers across all 5 players; all commands valid; fast.
- events/scout: hand-made tick pairs; hysteresis on/off.
- trainer: lessons fire on synthetic timelines; challenger respects bounds and max 3 knobs;
  promotion / discard / circuit breaker; dossier nearest-neighbour; analyst with stubbed
  LLM (bad JSON, unknown knob, out-of-bounds delta).
- store: in-memory fake.

## 14. Success criteria

1. Every response is a valid command within 500 ms (locally p99 < 10 ms).
2. `trainer.run` on a match produces report.json, updates lessons/dossiers/league,
   and writes a challenger differing from the champion in <= 3 knobs, all in bounds.
3. A losing challenger is discarded automatically; a winning one is promoted.
4. Unit tests pass without AWS.
