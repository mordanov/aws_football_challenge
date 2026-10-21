# Local Simulator Rules v1 — Proposed

These rules are derived exclusively from the five reference agents. Constants are quoted
verbatim with source-line evidence. Nothing here is invented.

**Status: APPROVED 2026-10-21.**

---

## 1. Pitch geometry

| Constant | Value | Source |
|---|---|---|
| Length | 110 m | `redesign_1/agent/world.py:LENGTH=110.0` |
| Width | 70 m | `redesign_1/agent/world.py:WIDTH=70.0` |
| Half-length | ±55 m | `redesign_1/agent/world.py:HALF_L=55.0` |
| Half-width | ±35 m | `redesign_1/agent/world.py:HALF_W=35.0` |
| Origin | centre circle | all redesigns |
| x-axis | points right (home attacks +x) | `redesign_1/agent/wire.py:6` |
| y-axis | points up | convention |

### Goal

| Constant | redesign_1 | redesign_3 |
|---|---|---|
| Goal half-width | 3.66 m | **4.5 m** ← adopted |

Source: `redesign_3/agent/model.py:GOAL_HALF_W=4.5`.

### Penalty area

| Constant | redesign_1 | redesign_3 |
|---|---|---|
| Box depth from goal line | 16.5 m | 16.5 m |
| Inner x boundary (own half) | −38.5 | −38.5 (PENALTY_BOX_X=38.5) |
| Box half-width | 20.0 m | **16.5 m** ← adopted |

Source: `redesign_3/agent/model.py:PENALTY_BOX_HALF_W=16.5`.

### 6-yard box

| Constant | redesign_3 |
|---|---|
| Depth from goal line | 5.5 m |
| Inner x boundary | ±49.5 (GOAL_AREA_X=49.5) |

Source: `redesign_3/agent/model.py:GOAL_AREA_X=49.5`.

---

## 2. Match duration

| Agent | Default match length |
|---|---|
| `initial_idea` | 120 s (`adapter.py:match_len default=120.0`) |
| `redesign_1` | 300 s (`world.py:match_len=300.0`) |
| `redesign_2` | 300 s (`protocol.py:matchDuration default=300`) |
| `redesign_3` | 300 s (`adapter.py:MATCH_DURATION_SECONDS=300.0`) |
| `redesign_4` | 120 s (`protocol.py:matchDuration default=120`) |

`initial_idea` and `redesign_4` agree on 120 s; `redesign_1/2/3` use 300 s.

**Adopted: 120 s** (matches spec §6, `initial_idea`, `redesign_4`).

---

## 3. Roles

Fixed mapping by player index, identical across all agents:

| Player ID | Role |
|---|---|
| 0 | GK (goalkeeper) |
| 1 | DEF (defender) |
| 2 | MID (midfielder) |
| 3 | FWD / FWD1 (forward) |
| 4 | FWD / FWD2 (forward) |

Source: `redesign_1/agent/world.py:ROLE_BY_ID`, `redesign_3/agent/model.py:Role`.

---

## 4. Player physics

### Stamina

- Normalised to [0.0, 1.0].
- Server may send 0–100 range; agents divide by 100 when value > 1.5.
- Source: `redesign_2/agent/protocol.py:decode()`, `redesign_3/agent/adapter.py:139-148`.

### Speed factor

`redesign_3/agent/model.py:Player.speed_factor:130`:

```
speed_factor = 0.6 + 0.4 * stamina   # at stamina=0: 0.6; at stamina=1: 1.0
```

### Base max speed

`redesign_3/agent/model.py:BASE_MAX_SPEED = 5.2` units/tick (at full stamina, no sprint).

### Arrival time (redesign_2/4 model)

`redesign_2/agent/tactics.py:arrival()`:

```
arrival(player, target) = distance / (4.0 + 2.0 * stamina)
```

### Sprint multiplier (redesign_3)

`redesign_3/agent/tactics.py:time_to_point:74`:

```
mult = 1.45 if (sprint and stamina > 0.2) else 1.0
step = max(1.8, BASE_MAX_SPEED * speed_factor * mult)
time = distance / step
```

### Sprint discipline thresholds (redesign_3)

`redesign_3/agent/tactics.py:should_sprint:456-467`:

- `dist < 2.2` → never sprint
- urgent + `stamina > 0.18` → sprint
- `stamina > 0.62` and `dist > 6.5` → sprint
- `stamina > 0.42` and `dist > 14.0` → sprint

---

## 5. Ball physics

### Possession acquisition radius

- `initial_idea/agent/adapter.py:_owner:141` — 2.0 m fallback
- `redesign_3/agent/adapter.py` docstring — `<= 6.5 m` for disambiguation

**Proposal:** pickup radius = **2.1 m** (`redesign_3/agent/model.py:PICKUP_RADIUS=2.1`).

### Ball velocity damping

`redesign_3/agent/tactics.py:predict_ball_pos:63`:

```
dx = vel.x * horizon_ticks * 0.82
dy = vel.y * horizon_ticks * 0.82
```

Damping factor per tick: **0.82**.

### Tackle radius

`redesign_3/agent/model.py:TACKLE_RADIUS = 2.4` m.

---

## 6. Command constraints (local simulator)

Derived from the strictest validated agents (redesign_2/4):

1. `duration` must be **0** — any other value is rejected.
2. Exactly **one command per player per tick**.
3. `teamId` must match the agent's assigned team.
4. `playerId` must be in [0, 4].
5. `commandType` must be one of the recognised set (§ 7 below).
6. `parameters` must contain exactly the required keys for that command.

Source: `redesign_2/agent/protocol.py:validate_command`,
`redesign_4/agent/protocol.py:validate_command`.

---

## 7. Recognised command types for local simulator

The intersection of commands validated by redesign_2/4 plus SLIDE_TACKLE (present in
redesign_1/3):

| Command | Parameters |
|---|---|
| `MOVE_TO` | `target_x: float, target_y: float` |
| `PASS` | `target_player_id: int, type: GROUND\|AERIAL\|THROUGH` |
| `SHOOT` | `aim_location: TL\|TR\|BL\|BR\|CENTER, power: float [0,1]` |
| `PRESS_BALL` | `intensity: float` |
| `MARK` | `target_player_id: int, tightness: TIGHT\|LOOSE` |
| `INTERCEPT` | *(no required params)* |
| `GK_DISTRIBUTE` | `type: THROW\|KICK, target_x: float, target_y: float` |
| `SET_STANCE` | `stance: int ≥ 0` |
| `SLIDE_TACKLE` | `target_player_id: int` |

Commands present only in older agents (DRIBBLE, TACKLE, IDLE, CLEAR, RESET,
FOLLOW_PLAYER, CLEAR_OVERRIDE) are **not** recognised. An unrecognised command falls
back to `SET_STANCE` (same fallback used by redesign_2/4 runtime).

---

## 8. Shot model

### Distance gates (redesign_3)

`redesign_3/agent/tactics.py:evaluate_shot:152`:

```
beyond_max_shoot_distance if dist_to_goal > w.max_shoot_distance
```

Default effective max distance: `shot_range` setting default = **29.0 m** (redesign_2/4
`model.py:Settings.shot_range=29.0`); bounds [20.0, 34.0].

Sweeper-GK punisher (redesign_3): if opponent GK is > 12m off line and shooter is within
36m, shoot even outside normal range (`tactics.py:128-148`).

### xG formula (redesign_3)

`redesign_3/agent/tactics.py:174-175`:

```
dist_factor  = max(0, 1 − (dist_to_goal / 28.0)^1.35)
angle_factor = min(1, visible_angle / 0.42)
xg           = 0.65 * dist_factor + 0.35 * angle_factor − blocker_penalty
```

Blocker penalty per blocker:

```
block_radius = 1.1 (frozen opponent) or 1.85 (normal)
penalty      = 0.32 * (1 − perp_dist / block_radius)   if perp_dist < block_radius
```

### Shot power (redesign_3 calibration)

`redesign_3/agent/tactics.py:195-200`:

| Distance to goal | Power |
|---|---|
| ≤ 11 m | 0.76 |
| ≤ 18 m | 0.86 |
| > 18 m | 0.94 |

### Aim selection

Redesign_2/4 tactics emit only `BL`/`BR`. Redesign_3 uses full `TL/TR/BL/BR/CENTER`.
All five aim values accepted.

---

## 9. Pass model

### Pass safety margin (redesign_2/4 settings)

Default **3.0** (pass_margin), bounds [2.5, 4.5].

### Pass distance limits (redesign_3)

`redesign_3/agent/tactics.py:evaluate_pass_options:238`:

```
skip if raw_dist < 3.0 or raw_dist > 52.0
skip if pass_dist < 3.5 or pass_dist > 50.0
```

### Through-ball lead distance

`redesign_3/agent/tactics.py:259-263`:

```
lead_dx = min(10.0, max(5.0, 55.0 − 6.0 − mate.pos.x))
```

### Aerial pass bypass

Aerial passes are not blocked by midfield interceptors for `proj_t in (0.15, 0.78)`.
Source: `redesign_3/agent/tactics.py:279-281`.

---

## 10. GK distribution

`redesign_2/agent/tactics.py:gk_distribute()`:

```
type = THROW if distance_to_target < 25 else KICK
```

GK outlet threshold: **25 m**.

---

## 11. Tactical settings (profile-tuneable)

These four floats are tunable per-opponent via the profile file (redesign_2/4):

| Setting | Default | Bounds | Meaning |
|---|---|---|---|
| `pass_margin` | 3.0 | [2.5, 4.5] | Minimum cone ratio for safe pass |
| `shot_range` | 29.0 m | [20.0, 34.0] | Max distance to attempt a shot |
| `press_radius` | 22.0 m | [12.0, 30.0] | Radius to trigger pressing |
| `support_width` | 15.0 m | [10.0, 22.0] | Width of off-ball support runs |

Source: `redesign_4/agent/profile.py:BOUNDS`,
`redesign_2/agent/model.py:Settings`.

---

## 12. Defensive assignments (redesign_1 model)

`redesign_1/agent/tactics.py`:

- **Presser**: outfield player nearest the ball, unless stamina < 0.12
  (`presser_id:93-105`). DEF (id=1) held back if ball is beyond `press_height * HALF_L`
  upfield.
- **Interceptor**: outfield player nearest predicted ball position 1 s ahead
  (`interceptor_id:108-116`). GK included if ball is in own box.
- **Markers**: DEF and MID mark the two most advanced off-ball opponents
  (`marker_assignments:119-125`).

---

## 13. Posture / game phase

`redesign_1/agent/tactics.py:posture:78-89`:

```
if late and losing → chase
if late and winning → protect
if opps_in_our_half >= counter_trigger → counter
else → build
```

`late` = last 20% of match (`redesign_1/agent/tactics.py:81`).

Coach instruction overrides: `go_all_out_attack`/`all_out` → chase; `slow_the_tempo` →
protect; `press_high` → press; `shoot_on_sight` → shoot.

---

## 14. Stamina-gated pressing (redesign_1)

`redesign_1/agent/tactics.py:presser_id:97-98`:

```python
if p.stamina < 0.12:
    continue   # too exhausted to press
```

---

## 15. Play modes

Recognised by all agents. Local simulator must set `playMode` on each tick.

| Value | Meaning |
|---|---|
| `OPEN_PLAY` / `0` | Normal play |
| `KICK_OFF` / `1` | Kick-off (start or after goal) |
| `THROW_IN` / `2` | Ball left pitch on touchline |
| `CORNER` / `3` | Ball left over goal line (defending team last touch) |
| `GOAL_KICK` / `4` | Ball left over goal line (attacking team last touch) |
| `FREE_KICK` / `5` | Foul |
| `PENALTY` / `6` | Penalty kick |

Source: `redesign_3/agent/adapter.py:_PLAY_MODE_MAP:31-39`.

---

## 16. Boundary conditions (proposals — no agent source)

The agents do not implement boundary or restart logic — they only read it from the server.
These rules are proposals for the local simulator, consistent with references above:

- Ball outside touchline (`|y| > 35`) → `THROW_IN` for team last **not** touching the ball.
- Ball over goal line outside goal (`|y| > 3.66`), defending team last touch → `CORNER`.
- Ball over goal line outside goal, attacking team last touch → `GOAL_KICK`.
- Ball entirely inside goal (`|y| ≤ 4.5` and `x > 55` or `x < −55`) → **GOAL**.
- After a goal: `KICK_OFF` from centre circle.
- Out-of-bounds restart position: for throw-in, ball placed at the crossing point on the
  sideline; for corner, at the corner arc; for goal kick, at the six-yard box edge.

**These boundary rules are approximate proposals pending approval.**

---

## 17. Final-time condition (proposal)

Match ends when `clock >= match_duration` (120 s proposed).
Any in-flight action that started before `clock = 120` is resolved; no new actions start.

---

## 18. Random distributions

The reference agents contain **no random number generation** except:

- `redesign_1/agent/tactics.py:best_aim:173-184` — deterministic mix using `tick_seed % 3`
  (not truly random).
- `redesign_3/agent/tactics.py:evaluate_shot:192` — `world.tick % 2` for centred GK aim
  selection (deterministic).

**Proposal:** the local simulator uses the `seed` field from the wiring-check format as
the random seed. The engine is fully deterministic given the same seed and agent outputs.

---

## Decisions (approved 2026-10-21)

| # | Question | Decision |
|---|---|---|
| 1 | Goal half-width | **4.5 m** (redesign_3) |
| 2 | Penalty box half-width | **16.5 m** (redesign_3) |
| 3 | Match duration | **120 s** (spec §6, redesign_4) |
| 4 | DRIBBLE command | **dropped** |
| 5 | SET_STANCE stance | **any non-negative int** |
| 6 | MARK NORMAL tightness | **rejected** (TIGHT/LOOSE only) |
| 7 | Ball velocity damping | **0.82/tick** (redesign_3) |
