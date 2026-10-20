# 01 — Resolved Blockers & Official Protocol Specification

The initial task document (`initial_idea/docs/01-task.md`) listed several critical "hard blockers" because the author did not have access to the official AWS sample repositories at the time of writing.

By inspecting the official AWS sample repositories (`aws-samples/sample-ai-possibilities/agentic-football-sample-agents`, `agentic-football-coach`, `snehasrikanth/Agentic-AI-Football-Cup`, and `fl-prasad-alai/flexiloans-agentic-football`), we resolved **every single protocol blocker** and uncovered several critical wire-format bugs in `initial_idea/agent/` that would have caused commands to be dropped or misparsed in a live match.

---

## 1. Critical Bugs Found in `initial_idea` vs. Official AWS Contract

| Area | What `initial_idea` Assumed | Official AWS Server Contract (`lib/state.py`, `lib/parsing.py`) | Impact & Fix in `redesign_3` |
|---|---|---|---|
| **AgentCore Payload Envelope** | Expected `payload["gameState"]` directly as a dict | `payload` has `{"prompt": "<JSON string or dict>"}` where `prompt` decodes to `{"gameState": ..., "teamId": 0, "myPlayers": [id]}` | `initial_idea` would fail to find `gameState` when `prompt` is a JSON string and fall back to `SET_STANCE` on every tick. `redesign_3` unwraps both `payload["prompt"]` (string or dict) and direct `payload["gameState"]`. |
| **Player IDs in `gameState.players`** | Looked for `id`, `playerId`, `unum` | New server format uses `"agentId": "agentId_0" .. "agentId_4"` and `"teamCode": "home" \| "away"` (case-insensitive) | **Fatal in `initial_idea`**: `"agentId"` was not in `_ID_KEYS`, causing `ValueError("player N not found")`. Also, `agentId_0..4` is **duplicated across both teams**! `redesign_3` parses `"agentId"` + `"teamCode"` case-insensitively. |
| **Ball Possession Resolution** | Matched `ball.owner` by player index alone | `ball` has `"isFree": bool` and `"possessionAgentId": "agentId_3"` (not unique across teams!) | Matching by index alone makes **both** home player 3 and away player 3 believe they hold the ball (`test_state.py`). `redesign_3` honours `isFree` first, then disambiguates `possessionAgentId` by checking which matching player is within pickup proximity of `ball.position`. |
| **`MOVE_TO` Parameters** | `{"x": float, "y": float}` | `{"target_x": float, "target_y": float, "sprint": bool}` | `initial_idea` sent `x`/`y`, which `lib/parsing.py` ignores (defaulting `target_x=0, target_y=0`, sending every player to the center circle!). `redesign_3` sends `target_x`, `target_y`, `sprint` (and optionally `x`/`y` only when legacy mode is enabled). |
| **Dribbling / Carrying the Ball** | Sent `commandType: "DRIBBLE"` | `"DRIBBLE"` is **not** in `VALID_COMMANDS`! Ball carrier moves via `"MOVE_TO"` | `lib/parsing.py` drops `"DRIBBLE"` as an unknown command! In the official engine (`lib/fallback.py`), carrying the ball is executed via `MOVE_TO` while in possession. |
| **`SHOOT` Aim Locations** | Sent `"LEFT"`, `"CENTER"`, `"RIGHT"` | Valid set is `{"TL", "TR", "BL", "BR", "CENTER"}` with `power: 0.0..1.0` | `"LEFT"` and `"RIGHT"` are invalid in `lib/parsing.py` and get overwritten to `"TR"`. `redesign_3` targets exact corners (`"TL"`, `"TR"`, `"BL"`, `"BR"`, `"CENTER"`). |
| **`PASS` Parameters** | Sent `{"target_player_id": int}` | `{"target_player_id": int, "type": "GROUND" \| "AERIAL" \| "THROUGH"}` | `redesign_3` explicitly chooses `"GROUND"`, `"THROUGH"`, or `"AERIAL"` based on lane geometry and opponent pressing density. |
| **Tackling & Clearing** | Sent `"TACKLE"`, `"CLEAR"`, `"IDLE"` | None of those 3 exist in `VALID_COMMANDS`! Valid commands are `"SLIDE_TACKLE"`, `"PRESS_BALL"`, `"INTERCEPT"`, `"MARK"`, `"FOLLOW_PLAYER"`, `"GK_DISTRIBUTE"`, `"SET_STANCE"`, `"CLEAR_OVERRIDE"`, `"RESET"` | `redesign_3` strictly emits only the 12 official `VALID_COMMANDS` from `lib/parsing.py`. |
| **Opponent Action Visibility** | Assumed opponent actions are hidden | Every player object in `gameState.players` includes `"lastAction": str`, `"speed": float`, `"isSprinting": bool`, `"stamina": float` | **Huge tactical unlock**: Our Eagle-Eye Scouting Engine inspects opponent `lastAction` (e.g., `"InterceptBallCarrier"`, `"RoamRandomly"`, `"StayNearGoal"`, `"FindOpenSpace"`) and `speed`/`isSprinting` every tick! |

---

## 2. Official Wire Schema Reference

### 2.1 Incoming Payload (`invoke(payload, context)`)

```json
{
  "prompt": "{\"gameState\": {...}, \"teamId\": 0, \"myPlayers\": [2]}"
}
```
*(Note: `prompt` may be a JSON string or an already-parsed dictionary, or `gameState` may be at the top level in local test harnesses. `redesign_3/agent/adapter.py` supports all three seamlessly.)*

### 2.2 `gameState` Structure

```json
{
  "tick": 150,
  "gameTime": 120.5,
  "playMode": "OPEN_PLAY",
  "modeTeamId": null,
  "score": { "home": 1, "away": 0 },
  "ball": {
    "position": { "x": 15.0, "y": -5.0, "z": 0.0 },
    "velocity": { "x": 2.0, "y": 1.0, "z": 0.0 },
    "isFree": false,
    "possessionAgentId": "agentId_3"
  },
  "players": [
    {
      "agentId": "agentId_0",
      "teamCode": "home",
      "position": { "x": -50.0, "y": 0.0 },
      "velocity": { "x": 0.0, "y": 0.0 },
      "orientation": 0.0,
      "stamina": 0.95,
      "currentAction": 10,
      "lastAction": "StayNearGoal",
      "speed": 0.0,
      "isSprinting": false
    }
  ],
  "teamChat": []
}
```

### 2.3 Coordinate System & Pitch Geometry
- **Pitch Dimensions:** `110m x 70m` centered at `(0.0, 0.0)`
  - `x in [-55.0, +55.0]`
  - `y in [-35.0, +35.0]`
  - Goal mouth at `x = ±55.0`, `y in [-5.0, +5.0]` (posts at `y = ±3.66..5.0`)
- **Team Orientation:**
  - `teamId == 0` (`"home"`): defends `my_goal_x = -55.0`, attacks `opp_goal_x = +55.0` (attack sign `s = +1`)
  - `teamId == 1` (`"away"`): defends `my_goal_x = +55.0`, attacks `opp_goal_x = -55.0` (attack sign `s = -1`)
- **Normalized Coordinates (Internal Tactical Frame):**
  - Every incoming state is projected into our attack-normalized frame where **we always attack toward `x = +55.0`** and defend `x = -55.0`.
  - Outgoing `MOVE_TO` coordinates (`target_x`, `target_y`) are automatically un-mirrored back into the server's raw coordinate frame before emission.

### 2.4 Official 12 Valid Commands (`VALID_COMMANDS`)

Every command returned in the array must match:
```json
{
  "commandType": "MOVE_TO",
  "playerId": 2,
  "teamId": 0,
  "parameters": { "target_x": 18.5, "target_y": -6.0, "sprint": true },
  "duration": 0
}
```

| `commandType` | `parameters` | `duration` | Tactical Purpose |
|---|---|---|---|
| `MOVE_TO` | `{"target_x": float, "target_y": float, "sprint": bool}` | `0` | Off-ball movement AND on-ball dribbling/carrying |
| `PASS` | `{"target_player_id": int, "type": "GROUND" \| "AERIAL" \| "THROUGH"}` | `0` | Pass to teammate `0..4` |
| `SHOOT` | `{"aim_location": "TL" \| "TR" \| "BL" \| "BR" \| "CENTER", "power": float}` | `0` | Shot on goal (`power` in `0.0..1.0`) |
| `GK_DISTRIBUTE` | `{"target_player_id": int, "method": "THROW" \| "KICK"}` | `0` | Goalkeeper distribution after save/claim |
| `SLIDE_TACKLE` | `{"target_player_id": int, "sprint": bool, "distance": float}` | `0` | High-commitment dispossession tackle |
| `PRESS_BALL` | `{"intensity": float}` (`0.0..1.0`) | `1..3` | Close down ball carrier |
| `INTERCEPT` | `{"aggressive": bool}` | `1..3` | Cut passing lane / loose ball |
| `MARK` | `{"target_player_id": int, "tightness": "LOOSE" \| "TIGHT"}` | `1..5` | Shadow opponent off-ball |
| `FOLLOW_PLAYER` | `{"target_player_id": int, "target_team": "HOME" \| "AWAY", "distance": float}` | `1..3` | Maintain fixed distance to target player |
| `SET_STANCE` | `{"stance": 0 \| 1 \| 2}` (`0`=Balanced, `1`=Attack, `2`=Defend) | `0` | Fallback stance setting |
| `CLEAR_OVERRIDE` | `{}` | `0` | Clear sticky duration command |
| `RESET` | `{}` | `0` | Full state reset |
