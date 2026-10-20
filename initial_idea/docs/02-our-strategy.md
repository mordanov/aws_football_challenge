# 02 - Our Strategy: "Coordinated Code, Advisory LLM"

Implemented in `agent/`. Status: unit-tested only, never played a real match.

## Thesis
Five stateless agents cannot talk, and an LLM is slow and unreliable under a
1 s deadline. So put everything that must be right in deterministic code, make
all five players compute the same team plan from the same snapshot, and let an
LLM (optional) break only genuine ties.

## Pillars

### 1. Implicit coordination (no messaging)
Every agent runs the same functions on the same state:
- `presser_id`: the nearest eligible outfielder presses; nobody else does.
- `marker_assignments`: DEF and MID mark the two most advanced off-ball
  opponents, never the same one.
- `interceptor_id`: one player (or the keeper in our box) takes loose balls.
Result: no five-man mobs, no unmarked runners, no gaps.

### 2. Code pulls the trigger
Tackles, shots, keeper actions and off-ball shape are rules (<1 ms). Zero
timeout risk for 90% of ticks.

### 3. Shot discipline
Shoot only inside ~33 m (0.30 x pitch length) with a clear lane, at the freest
corner (slight bias to centre). Lanes shut: cut back or pass. Never hoof.

### 4. Counter-attack trigger
If 3+ opponent outfielders sit in our half and a forward has nobody goal-side,
prefer the through ball (+30 score).

### 5. Posture by game state
`posture()` returns one of build / counter / protect / chase, identically for
all five agents, from score, clock and coach order.
- Late deficit: wider shoot range, shape pushed up.
- Late lead: drop shape, slow tempo.

### 6. LLM as pick-one-of-N tie-breaker (off by default)
Only when the ball carrier's top two options are within 8 points. The model
returns an index over already-valid options, 0.55 s cutoff, any failure falls
back to the rules. It cannot produce an illegal command.

### 7. Four-layer degradation
LLM -> rules -> error fallback -> SET_STANCE. A player never freezes.

## Known weaknesses (be honest)
- Never tested against a real opponent. Constants (33 m, 3.2 m lane, 8 pt
  margin, anchors) are reasoned guesses, not tuned.
- Adapter and command schema are guessed.
- Memory (`opp_line` smoothing) only helps on warm runtimes.
- No set-piece logic (corners, free kicks, penalties) since the rules are unknown.
- Keeper logic is simple; no shot-direction prediction.

## Iteration loop to adopt
1. Play practice matches vs the 3 bots.
2. Read the match report and per-command breakdown.
3. Change ONE constant or rule; replay several matches.
4. Keep a log of `DECISION` lines (source, command, latency) and check the
   share decided by rules vs LLM.
5. Only then A/B the LLM tie-break.

## Where to tune first
| Knob | File | Why |
|---|---|---|
| `shoot_range` | tactics.py | Too far donates possession, too near wastes chances |
| lane `need` (3.2 m) | tactics.py | Pass safety vs forward progress |
| `DECISIVE_MARGIN` | policy.py | How often the LLM is consulted |
| `_BASE` anchors | tactics.py | Formation: 1-1-1-2 as written |
| presser eligibility | tactics.py | Press intensity |
