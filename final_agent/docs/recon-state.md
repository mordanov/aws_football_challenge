# Phase 0: State Recon — Teammate Intent Analysis

## Questions and findings

| Question | Answer |
|---|---|
| Current player actions (command type, target, age) in state? | **No** — `decode()` in `protocol.py` parses only `position`, `velocity`, `stamina` per player. No `currentAction`, `commandType`, `target`, or action history fields. |
| Are those actions from last tick or current tick? | N/A — not present |
| `target` visible on `MOVE_TO`/`PASS` of teammates? | **No** |
| Player velocity available? | **Yes** — `vx`, `vy` per player |
| Ball velocity available? | **Yes** — `velocity` on snapshot |
| Stamina available? | **Yes** — `stamina` per player, 0–1 |
| Last-pass recipient? | **No** |
| Match ID? | Available at `Runtime` level (injected from environment), not in game-state payload itself |
| Coach instruction? | **Yes** — `coachInstruction` parsed to `Snapshot.coach` |

## Conclusion for `USE_TEAMMATE_INTENT` (§1.4)

The game-state payload does not contain current or previous player actions.
`USE_TEAMMATE_INTENT` is **automatically inactive** — the prerequisite data is absent.

Sections §2–§8 are implemented without §1.4.

## Sample payload structure (agent/protocol.py decode() shows what is parsed)

```
gameState
  players[]:
    position.x, position.y
    velocity.x, velocity.y
    stamina   (0–1 or 0–100 via stamina_scale)
  ball:
    position.x, position.y
    velocity.x, velocity.y
    isFree
    possessionAgentId / possessionTeamId
  score: {home, away}
  gameTime, matchDuration
  coachInstruction  → one of go_all_out_attack / slow_the_tempo / press_high / shoot_on_sight
```
