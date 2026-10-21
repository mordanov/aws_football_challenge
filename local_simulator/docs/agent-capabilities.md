# Agent Capabilities Inventory

Reference reading of the five agents in the order they were written: `initial_idea`,
`redesign_1`, `redesign_2`, `redesign_3`, `redesign_4`. No code was modified.

---

## 1. Observation (what each agent receives)

All agents receive a JSON document that includes at minimum:

| Field | Type | Notes |
|---|---|---|
| `gameState.players` or `myTeam`/`opponents` | list | 10 players (5 per team) |
| `gameState.ball` | object | `position`, `velocity`, `isFree`, `possessionAgentId` |
| `gameState.tick` | int | frame counter |
| `gameState.gameTime` / `clock` | float | seconds elapsed |
| `gameState.score` | object | `{home, away}` or `{me, opp}` |
| `gameState.playMode` | str/int | `OPEN_PLAY`, `KICK_OFF`, `CORNER`, etc. |
| `coachInstruction` | str | optional tactical directive |

Sources: `initial_idea/agent/adapter.py:74-115`, `redesign_1/agent/wire.py:1-90`,
`redesign_2/agent/protocol.py:1-80`, `redesign_3/agent/adapter.py:42-530`,
`redesign_4/agent/protocol.py` (same as redesign_2).

### Stamina scale

The server sends stamina as either 0.0–1.0 or 0–100. Every agent normalises:
- `redesign_2/agent/protocol.py:decode()` — if `stamina > 1.5` divide by 100
- `redesign_3/agent/adapter.py:_normalize_stamina:139-148` — same rule

---

## 2. Identity (how agents know who they are)

| Agent | Topology | Identity mechanism |
|---|---|---|
| `initial_idea` | one OS process per player | `MY_PLAYER_ID` env var (`main.py:12`) |
| `redesign_1` | one OS process per **team** | `myPlayers` list in payload; `_brain` module singleton (`main.py:1-60`) |
| `redesign_2` | one OS process per player | `playerId`/`teamId` in envelope (`runtime.py:31`) |
| `redesign_3` | one OS process per **team** | `myPlayers` / `controlledPlayers` in payload; `FootballTrainer` singleton (`main.py:1-80`) |
| `redesign_4` | one OS process per player | `playerId`/`teamId` in envelope (same as redesign_2) |

`redesign_1` and `redesign_3` carry **module-level singleton state** that persists across
ticks within one process lifetime.

`redesign_3/agent/adapter.py:_infer_controlled_ids:175-197` — falls back to `PLAYER_ID`
env var, then to `(0,1,2,3,4)` if nothing else is present.

---

## 3. Coordinate system

All agents use a **centred pitch**: origin at centre circle, x-axis points right, y-axis points up.

- Pitch bounds: `x ∈ [-55, +55]`, `y ∈ [-35, +35]`
- Team 0 (home) attacks toward `+x` (sign `s = +1`)
- Team 1 (away) attacks toward `−x` (sign `s = −1`)

Sources: `redesign_1/agent/world.py:HALF_L=55, HALF_W=35`,
`redesign_3/agent/model.py:PITCH_HALF_L=55.0, PITCH_HALF_W=35.0`,
`redesign_2/agent/protocol.py:decode()` applies `s` to negate both x and y for away team.

`initial_idea/agent/adapter.py:74-116` shows an older offset-based path (`PITCH_ORIGIN=corner`),
but all redesigns use `center` origin. The local simulator uses centred coordinates.

Attack sign: `redesign_1/agent/wire.py: s = +1 if team_id==0 else -1` (line 6).

---

## 4. Output wire format

Every agent returns a JSON reply containing a `commands` array:

```json
{
  "commandType": "MOVE_TO",
  "playerId": 2,
  "teamId": 0,
  "parameters": {"target_x": 10.0, "target_y": -5.0},
  "duration": 0
}
```

- `initial_idea/agent/commands.py` — uses `commandType` + `playerId` + `parameters` + `duration:0`; no `teamId`
- `redesign_1/agent/commands.py` — adds `teamId`; `duration` may be > 0 for PRESS_BALL/MARK/INTERCEPT
- `redesign_2/agent/protocol.py:validate_command` — `duration` **must** be 0
- `redesign_3/agent/model.py:Command.to_wire:209-216` — all five fields; `duration: 0`
- `redesign_4/agent/protocol.py:validate_command` — identical to redesign_2; `duration` must be 0

One reply per player per tick. `redesign_1`/`redesign_3` emit multiple commands (one per
controlled player) in the same reply.

---

## 5. Commands and parameters

### MOVE_TO

Present in all five agents.

| Parameter | initial_idea | redesign_1 | redesign_2/4 | redesign_3 |
|---|---|---|---|---|
| `x`, `y` | ✓ (offset coord) | — | — | ✓ (legacy alias) |
| `target_x`, `target_y` | — | ✓ | ✓ | ✓ (canonical) |
| `sprint` | — | bool | — | — |

Sources: `initial_idea/agent/commands.py:MOVE_TO`,
`redesign_1/agent/commands.py:SPEC[MOVE_TO]`,
`redesign_3/agent/commands.py:move_to()` (also exposes legacy `x`/`y` keys).

`redesign_3` `dribble_to()` is an alias for `move_to()` — no separate DRIBBLE command
(`redesign_3/agent/commands.py`).

### PASS

Present in all five agents.

| Parameter | Values |
|---|---|
| `target_player_id` | 0–4 |
| `type` / `pass_type` | `GROUND` \| `AERIAL` \| `THROUGH` |

Sources: `initial_idea/agent/commands.py:PASS`,
`redesign_1/agent/commands.py:SPEC[PASS]`,
`redesign_2/agent/protocol.py:validate_command`,
`redesign_3/agent/commands.py:pass_to()`,
`redesign_4/agent/protocol.py:validate_command`.

### SHOOT

Present in all five agents.

| Parameter | Values |
|---|---|
| `aim_location` | `TL` \| `TR` \| `BL` \| `BR` \| `CENTER` |
| `power` | float 0.0–1.0 |

`redesign_2` tactics only emits `BL`/`BR` in normal play; `redesign_3` uses all five.
Source: `redesign_2/agent/tactics.py:shot_option()`,
`redesign_3/agent/tactics.py:evaluate_shot:119-211`.

Power calibration (redesign_3): ≤11m → 0.76, ≤18m → 0.86, >18m → 0.94
(`redesign_3/agent/tactics.py:195-200`).

### PRESS_BALL

Present in all five agents.

| Parameter | Values |
|---|---|
| `intensity` | float (redesign_1/2/3/4); absent in initial_idea |

Source: `redesign_1/agent/commands.py:SPEC[PRESS_BALL]`,
`redesign_2/agent/protocol.py:validate_command`.

### MARK

Present in all five agents.

| Parameter | initial_idea / redesign_1 | redesign_2/3/4 |
|---|---|---|
| `target_player_id` | ✓ | ✓ |
| `tightness` | `TIGHT` \| `LOOSE` \| `NORMAL` | `TIGHT` \| `LOOSE` (no NORMAL) |

Sources: `redesign_1/agent/commands.py:SPEC[MARK]`,
`redesign_2/agent/protocol.py:validate_command` (NORMAL rejected).

### INTERCEPT

Present in all five agents.

| Parameter | redesign_1 | redesign_3 |
|---|---|---|
| `aggressive` | bool | bool |
| `target_player_id` | — | int |

Source: `redesign_1/agent/commands.py:SPEC[INTERCEPT]`,
`redesign_3/agent/commands.py:intercept()`.

### GK_DISTRIBUTE

Present in `initial_idea`, `redesign_2`, `redesign_3`, `redesign_4`. Absent in `redesign_1`.

| Parameter | Values |
|---|---|
| `type` | `THROW` \| `KICK` |
| `target_x`, `target_y` | float |

Threshold: distance to target < 25 → THROW, ≥ 25 → KICK
(`redesign_2/agent/tactics.py:gk_distribute()`).

### SET_STANCE

Present in all five agents.

| Parameter | initial_idea / redesign_1 | redesign_2/4 | redesign_3 |
|---|---|---|---|
| `stance` | any int | 0 only (validated) | any int |

Sources: `redesign_2/agent/protocol.py:validate_command`,
`redesign_1/agent/commands.py:SPEC[SET_STANCE]`.

### DRIBBLE

**Only `initial_idea`**. Listed in `COMMAND_TYPES` and emitted by `policy.py`.
All redesigns drop it — `redesign_3` maps `dribble_to()` → `MOVE_TO`.

Source: `initial_idea/agent/commands.py:COMMAND_TYPES`.

### TACKLE

`initial_idea` only (emitted by `policy.py`).

### SLIDE_TACKLE

`redesign_1` and `redesign_3`.

| Parameter | Values |
|---|---|
| `target_player_id` | int |
| `sprint` | bool (redesign_1 only) |
| `distance` | float (redesign_1 only) |

Sources: `redesign_1/agent/commands.py:SPEC[SLIDE_TACKLE]`,
`redesign_3/agent/commands.py:slide_tackle()`.

### FOLLOW_PLAYER

`redesign_1` and `redesign_3`.

| Parameter | Values |
|---|---|
| `target_player_id` | int |
| `target_team` | int |
| `distance` | float |

Source: `redesign_1/agent/commands.py:SPEC[FOLLOW_PLAYER]`.

### CLEAR / CLEAR_OVERRIDE

`initial_idea`: `CLEAR` command (emitted by `policy.py`).
`redesign_1`, `redesign_3`: `CLEAR_OVERRIDE {}` (no parameters).

### RESET

`redesign_1` and `redesign_3` only. No parameters. Resets internal state.

Source: `redesign_1/agent/commands.py:SPEC[RESET]`,
`redesign_3/agent/commands.py`.

### IDLE

`initial_idea` only (`COMMAND_TYPES`). Not emitted by redesigns.

---

## 6. Duration semantics

| Agent | Duration |
|---|---|
| `initial_idea` | always 0 |
| `redesign_1` | may be > 0 for PRESS_BALL / MARK / INTERCEPT |
| `redesign_2` | must be 0 (validated, raises ValueError) |
| `redesign_3` | 0 in `Command.to_wire()` default |
| `redesign_4` | must be 0 (validated, identical to redesign_2) |

The local simulator will require `duration: 0` for all commands (redesign_2/4 model).

---

## 7. Command summary table

| Command | initial_idea | redesign_1 | redesign_2 | redesign_3 | redesign_4 |
|---|---|---|---|---|---|
| MOVE_TO | ✓ | ✓ | ✓ | ✓ | ✓ |
| PASS | ✓ | ✓ | ✓ | ✓ | ✓ |
| SHOOT | ✓ | ✓ | ✓ | ✓ | ✓ |
| PRESS_BALL | ✓ | ✓ | ✓ | ✓ | ✓ |
| MARK | ✓ | ✓ | ✓ | ✓ | ✓ |
| INTERCEPT | ✓ | ✓ | ✓ | ✓ | ✓ |
| GK_DISTRIBUTE | ✓ | — | ✓ | ✓ | ✓ |
| SET_STANCE | ✓ | ✓ | ✓ | ✓ | ✓ |
| DRIBBLE | ✓ | — | — | — | — |
| TACKLE | ✓ | — | — | — | — |
| SLIDE_TACKLE | ✓ | ✓ | — | ✓ | — |
| FOLLOW_PLAYER | — | ✓ | — | ✓ | — |
| CLEAR / CLEAR_OVERRIDE | ✓ | ✓ | — | ✓ | — |
| RESET | — | ✓ | — | ✓ | — |
| IDLE | ✓ | — | — | — | — |

---

## 8. Persistence and learning

| Agent | Inter-tick state |
|---|---|
| `initial_idea` | None (stateless per tick; falls back to rules then LLM) |
| `redesign_1` | `_brain` singleton; background playbook + dossier refresh threads (`main.py`) |
| `redesign_2` | `Runtime` object carries profile/settings; no match learning |
| `redesign_3` | `FootballTrainer` singleton with `SelfErrorLedger`, `OpponentPlayerProfile`, `DecisionLog`; `analyze_match_data()` runs between ticks |
| `redesign_4` | Same `Runtime` pattern as redesign_2; adds `DecisionLog`; no in-match learning |

`redesign_1/trainer/` contains a full offline trainer (`train_one`, `record_and_plan`,
`evaluate`) that nudges playbook parameters between matches. Not active during play.

---

## 9. Coach instruction handling

All redesigns after `initial_idea` recognise `coachInstruction` (or `teamChat`).

Recognised values (redesign_4/3): `go_all_out_attack`, `slow_the_tempo`, `press_high`,
`shoot_on_sight` (`redesign_4/agent/tactics.py:posture()`).

`redesign_1` uses fuzzy keyword matching on free-text coach strings
(`redesign_1/agent/tactics.py:coach_flags:64-71`).

---

## 10. Play-mode handling

`redesign_3/agent/adapter.py:_PLAY_MODE_MAP:31-39`:

| int | string |
|---|---|
| 0 | OPEN_PLAY |
| 1 | KICK_OFF |
| 2 | THROW_IN |
| 3 | CORNER |
| 4 | GOAL_KICK |
| 5 | FREE_KICK |
| 6 | PENALTY |
