# Local Simulator Rules v2 — Foul Model

## 1. Status

Rules v2 is a **local assumption**, not the AWS challenge rules. The real platform's
foul, tackle and penalty mechanics are unknown; v2 exists so that aggressive and
conservative strategies have a cost/benefit trade-off locally. Every constant below is
a guess, which is why campaigns can rescale foul probability (`--foul-scale`) and
check how sensitive conclusions are.

- **v1 stays the default** and is byte-identical to before v2 existed. A golden replay
  hash test (`tests/test_rules_v1_golden.py`) guards it.
- A campaign pins `rules_version` (and, for v2, `rules_params`) in its manifest. v1 and
  v2 results are never mixed.
- Implementation: `src/football_lab/rules_v2.py` (`RulesV2Config`, `contest`), wired
  through `MatchEngine(..., rules_version="v2", rules_config=..., coaches=...)`.
- Randomness: one `random.Random(seed)` per match, so results are deterministic per seed.

## 2. Constants (`RulesV2Config`)

| Field | Default | Meaning |
|---|---|---|
| `foul_scale` | 1.0 | Multiplier on foul probability (sensitivity knob, must be ≥ 0) |
| `p_foul` | 0.25 | Base probability that a slide tackle is a foul |
| `behind_multiplier` | 2.0 | Foul probability multiplier for a tackle from behind |
| `behind_angle_deg` | 60.0 | Half-angle of the "from behind" cone |
| `p_win_slide` | 0.55 | Probability that a non-foul slide tackle wins the ball |
| `p_win_press_step` | 0.01 | Per-step probability that a standing challenge (`PRESS_BALL`) wins the ball |
| `stun_steps` | 40 | Steps a fouler is inactive (one decision window) |
| `send_off_fouls` | 3 | Fouls at which a player is sent off |
| `box_depth` | 16.5 | Penalty-area depth from the goal line (m) |
| `box_half_width` | 20.0 | Penalty-area half-width, `|y| ≤ 20` (m) |
| `p_penalty_goal` | 0.75 | Probability that a penalty is scored |

The effective foul probability of a slide tackle is
`min(1, p_foul × foul_scale × (behind_multiplier if from behind else 1))`.
`TACKLE_RADIUS` (2.4 m) is shared with v1 (`physics.py`).

## 3. Resolution order per physics step

1. **Players move** (v1 movement; inactive players do not move).
2. **`contest`** — rules v2 resolves at most **one** contest against the ball carrier.
3. **Ball follows owner** (or flies/rolls if free).
4. **One-shot commands** (`PASS`, `SHOOT`, `GK_DISTRIBUTE`).
5. **Possession pickup** (inactive players cannot pick up the ball).
6. **Boundaries / goal detection.**

Under v1 there is no contest hook and step 2 is skipped.

## 4. Contests and sanctions

Only opponents of the carrier who are active and within `TACKLE_RADIUS` of the carrier
are considered; each player's first intent this step counts.

| Situation | Outcome |
|---|---|
| **Slide tackle** (`SLIDE_TACKLE` targeting the carrier, one-shot) | Resolves once, the first step the tackler is within range. Foul with the effective foul probability; otherwise win with `p_win_slide`; otherwise nothing. |
| **Standing challenge** (`PRESS_BALL`) | Wins the ball with `p_win_press_step` per step; never a foul. |
| **Win** | Tackler takes possession immediately (`TACKLE_SUCCESS` event), so the carrier cannot instantly re-pick the ball. |
| **From behind** | The tackler lies within 60° of the carrier's backward attack direction (the carrier's own goal side). Foul probability × `behind_multiplier`. |
| **Foul outside the box** | Simplified free kick: the carrier keeps the ball; the fouler is stunned (inactive) for `stun_steps` = 40 steps. |
| **Foul inside the defender's penalty area** | `PENALTY` event (team = awarded team), resolved instantly: goal with `p_penalty_goal`; the ball restarts at the centre, unowned. |
| **Send-off** | The `send_off_fouls`-th (3rd) foul: `SEND_OFF` event, the player moves to the nearest touchline and is inactive for the rest of the match. They remain in observations, so the team plays a man down. |

Events: `FOUL`, `PENALTY`, `SEND_OFF`, `TACKLE_SUCCESS` (also for standing-challenge
wins). Campaign fixture results record per-team counts in `discipline`.

## 5. Ball physics (`v2_ball`)

### Shot aim

v2 honours `SHOOT`'s `aim_location` (`physics.step(..., v2_ball=True)`); the shot
travels from the ball toward `(±PITCH_HALF_L, target_y)`:

| `aim_location` | `target_y` |
|---|---|
| `TL`, `BL` | `-AIM_Y` = −3.5 |
| `TR`, `BR` | `+AIM_Y` = +3.5 |
| `CENTER` / anything else | 0.0 |

`AIM_Y = GOAL_HALF_W - 1.0` (3.5). The y values are **world coordinates for both
teams** (no mirroring for team 1), matching redesign_4's convention.

Why: v1 ignores `aim_location` and aims every shot at the goal centre, and any player
within `PICKUP_RADIUS` (2.1) of the in-flight ball picks it up, so a keeper standing at
its goal centre catches every shot (1 goal in 96 local v1 matches). **v1 is unchanged**.

### In-flight pickup

Also under `v2_ball=True`: a player cannot collect an in-flight ball that is moving
away from them (`(ball − player) · velocity > 0`). Players the ball is travelling
toward, such as the receiver or an opponent in the lane, still collect it within
`PICKUP_RADIUS`.

Why: v1 excludes the releasing player only on the release step. A 1.5 m/step pass is
still inside the passer's 2.1 m radius one step later, so in v1 every ground or
aerial pass is recollected by the passer. **v1 is unchanged**.

## 6. Known simplifications

- No free-kick restart: after a foul outside the box, play simply continues.
- No cards UI; fouls and send-offs are only visible as events.
- Slide movement continues after resolution (v1 physics is unchanged).
- The carrier is never fouled twice in one step (at most one contest per step).
- A penalty is resolved instantly; there is no penalty-kick set piece.

## 7. Coach hook

`MatchEngine(coaches=(coach0, coach1))` takes one optional coach per team with the
signature `coach(state, team_id) -> str`. Its output is delivered to that team's
agents through the `coachInstruction` observation field (default `""`). Registry:
`football_lab.coaches.COACHES` (`{"scripted": scripted_coach}`).

`scripted_coach` models a human coach shouting presets. With `diff` = own − opponent
goals and `r` = remaining match fraction:

| Condition | Instruction |
|---|---|
| `diff < 0` and `r ≤ 0.35` | `go_all_out_attack` |
| `diff < 0` | `press_high` |
| `diff > 0` and `r ≤ 0.40` | `slow_the_tempo` |
| `diff = 0` and `r ≤ 0.20` | `shoot_on_sight` |
| otherwise | `""` |

The first matching row wins.

## 8. Campaign CLI

| Option | Command | Meaning |
|---|---|---|
| `--rules v1\|v2` | `init` | Rules version pinned in the manifest (default `v1`) |
| `--foul-scale X` | `init` | `RulesV2Config.foul_scale`; requires `--rules v2` |
| `--entrants a,b,…` | `init` | Round robin among the entrants |
| `--challengers a,b --pool c,d` | `init` | Cross mode: each challenger plays each pool member (two legs per block, challenger home in leg 0); no games within a group; no overlap allowed |
| `--discard-replays` | `run` | Don't write replay files (`replay_path` is `null`); discipline counts are still recorded |
| `--json PATH` | `report` | Write a machine-readable report (standings, pooled goal-difference CI, pairwise CI, discipline, winner claim) |
| `--baseline ID` | `report` | Add paired goal-difference deltas vs that entrant (shared opponent × block units) |

`init` takes either `--entrants` or `--challengers` with `--pool`. Cross campaigns make
no winner claim; compare pooled goal difference instead.

### Profile variants (`variants.json`)

An optional `variants.json` beside an `entrant.json` declares profile variants of the
same entrant code. A variant id can be used anywhere an entrant id is accepted:

```json
{"base_id": "redesign_4_v2",
 "variants": [{"id": "r4v2_balanced_none", "profile": "profiles/balanced_none.json", "coach": null}]}
```

- `profile` is relative to the entrant directory; it is passed to the entrant process
  as `AFC_PROFILE=<absolute path>`.
- `coach` is `null` or a key of `COACHES` (e.g. `"scripted"`); unknown keys are
  rejected at `init`.
