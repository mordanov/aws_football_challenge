# 03 — "Learn From Every Match" Football Trainer & Eagle-Eye Scouting System

The centerpiece requirement of `redesign_3` is transforming the agent from a static rule-set into an **adaptive Football Trainer ("Learn From Every Match")** that:
1. **Remembers our own errors and resolves them** (both tick-by-tick during a match and persistently across matches).
2. **Monitors opponents' errors and keeps an Eagle Eye on their strategy** to exploit their weaknesses automatically.

Because the match server requires `<500ms` responses every tick, our learning architecture operates on **three timescales**:
- **Tier 1 — Intra-Match Tick-to-Tick Micro-Trainer (`<0.2ms` per tick, pure Python):** Tracks the causal outcome of every command we issue and updates live tactical weights immediately.
- **Tier 2 — Eagle-Eye Opponent Scouting Engine (`<0.2ms` per tick, pure Python):** Builds a live behavioral profile of all 5 opponent players (including reading their `lastAction`, stamina, positioning, and responsiveness) and activates targeted counter-tactics.
- **Tier 3 — Cross-Match Persistent Memory & Post-Match Coach Review (`agent/trainer.py` + `agent/coach_review.py`):** Saves learned opponent dossiers, self-error ledgers, and evolved `TacticalWeights` to disk (`/tmp/afc_trainer_memory.json` and `tuning.json`) so every match starts smarter than the last.

---

## 1. Self-Error Detection & Automatic Resolution (Learning From Our Mistakes)

On every tick $t$, `FootballTrainer.observe_tick(prev_snapshot, prev_commands, current_world)` compares what we *intended* on tick $t-1$ against what *actually happened* on tick $t$:

| Error Code | How It Is Detected | Root Cause Diagnosis | Automatic Trainer Resolution (Live + Persistent) |
|---|---|---|---|
| `ERR_PASS_INTERCEPTED` | We issued `PASS(target_player_id=k, type=...)` on tick $t-1$; on tick $t$ possession belongs to `opp` (or ball was intercepted along the corridor). | Our pass lane tolerance was too optimistic against fast/aggressive interceptors, or ground passes are getting cut in midfield. | 1. Increases `pass_safety_margin` (`+0.08`, widening required cone clearance).<br>2. If `type == "GROUND"` and distance `> 18m`, shifts preference toward `"AERIAL"` or shorter triangle passes.<br>3. Records turnover zone `(x, y)` in the danger-zone ledger. |
| `ERR_BUILDUP_TURNOVER_DANGER` | Possession lost in our defensive third (`ball.x < -18`) after a `PASS`, `GK_DISTRIBUTE`, or `MOVE_TO` dribble. | Playing too cute out of the back under heavy opponent press. | 1. Switches GK distribution preference from `"THROW"` to long `"KICK"` to forwards.<br>2. Increases `def_clearance_bias` so `DEF` bypasses congested midfield when pressed within `7m`. |
| `ERR_DRIBBLE_DISPOSSESSED` | Our player had the ball, issued `MOVE_TO` (dribble), and lost possession to an opponent tackle within 1–2 ticks. | Holding the ball too long against high-aggression tacklers. | 1. Increases `dribble_danger_radius` (`+0.6m`), forcing earlier pass release before tacklers enter tackle range (`2.4m`).<br>2. Boosts `one_touch_pass_bias`. |
| `ERR_SHOT_LOW_XG_WASTED` | We issued `SHOOT` from `dist > 22m` or tight angle (`|y| > 13`), and on tick $t$ opponent GK holds the ball (easy save/miss) while a teammate was open inside `16m`. | Selfish/premature shooting instead of making the extra pass / cutback. | 1. Tightens `max_shoot_distance` (`-1.5m`, down to floor `17m`).<br>2. Increases `cutback_pass_bonus` so forwards look for the tap-in pass. |
| `ERR_SHOT_BLOCKED` | We issued `SHOOT` and ball deflected/stopped within `6m` of shooter in possession of an opponent defender. | Shooting into a defender's shadow cone. | Increases `shot_block_clearance_threshold` and encourages lateral step-aside dribble or layoff pass before shooting. |
| `ERR_PRESS_BYPASSED` | Our player issued `PRESS_BALL` or `SLIDE_TACKLE`, and on tick $t$ the opponent ball carrier's `x` advanced closer to our goal past our presser. | Over-committing / diving in against an agile dribbler or one-two passer. | 1. Reduces `slide_tackle_aggression` (`-0.10`).<br>2. Increases `contain_shadow_preference` (using `MARK`/shadow-cone `MOVE_TO` instead of lunging). |
| `ERR_CONCEDED_COUNTER` | `score.opp` increased by `+1`, and 3–5 ticks prior our `DEF`/`MID` had average `x > +5.0`. | Caught with too high a defensive line on transition. | Drops `defensive_line_offset` by `-4.0m` and caps `DEF` max forward `x`. |
| `ERR_CONCEDED_CUTBACK_OR_CENTRAL` | `score.opp` increased by `+1` from a shot inside `15m` while our `DEF` and `MID` were both pulled wide (`|y| > 12`). | Both defenders chased the wide ball carrier, leaving the penalty spot unguarded. | Enforces strict `box_protector_lock`: when ball is wide in our third, `DEF` is locked to the central danger zone `(|y| <= 6)`. |

### Positive Reinforcement (Learning What Works!)
Whenever we **complete a progressive pass into the box**, **win a tackle in the opponent's half**, or **score a goal**, the Trainer records a `SUCCESS_*` event and reinforces the active flank (`preferred_flank`), pass type, and pressing height that produced the breakthrough.

---

## 2. Eagle-Eye Opponent Scouting Engine (Monitoring Opponent Strategy & Errors)

Every tick, `OpponentScout` updates rolling exponentially-weighted metrics on all 5 opponent players (`0..4`):

### 2.1 Real-Time Telemetry Collected on Opponents
1. **Action-Telemetry Inspection (`opp.last_action`):**
   - The official server exposes `lastAction` on every player (e.g., `"RoamRandomly"`, `"StayNearGoal"`, `"InterceptBallCarrier"`, `"CoachMoveTo"`, `"DribbleTo"`, `"FindOpenSpace"`).
   - We track the distribution of opponent actions to know immediately whether they are running default fallback AI, aggressive ball-chasing, or structured zonal play.
2. **LLM Timeout / Freeze Detector (`opp.frozen_ticks`):**
   - In the AWS tournament, many teams invoke slow LLMs synchronous to the tick loop and hit the 500ms timeout or stall (`speed == 0.0` for multiple ticks while ball is nearby).
   - When Eagle-Eye detects an opponent defender or midfielder is frozen/stalling (`speed < 0.05` for `>= 2` ticks), we tag them as **`EXPLOIT_FROZEN_AGENT`**: our ball carriers dribble directly past them without fear of tackle, and our forwards make runs straight through their zone!
3. **Opponent Team Archetype Classifier:**
   - **`MOB_PRESS` (Swarm / Ball-Chasers):** Average distance of 3+ opponents to the ball is `< 13m`.
     - *Counter-Strategy:* Disable risky dribbles; activate **Wide Switch & Aerial Over-the-Top** (`PASS` type `"AERIAL"` or `"THROUGH"` to the weak-side forward who is left completely `1v0`!).
   - **`ULTRA_HIGH_LINE` / `SWEEPER_GK` (e.g. `ai-team-strands-extremely-aggressive`):** Opponent deepest defender `x < 18m` or Opponent GK `x < 42m`.
     - *Counter-Strategy:* Activate **Direct Through-Ball Protocol** (`FWD1`/`FWD2` pin the high line and sprint in behind) + **Long-Range Punish** (shoot immediately from `25m..35m` whenever opponent GK wanders off their line!).
   - **`LOW_BLOCK_BUS` (e.g. `ai-team-strands-extremely-defensive`):** 4+ opponents stay deeper than `x = 18m` in their own half.
     - *Counter-Strategy:* Push our `DEF` and `MID` up (`defensive_line_offset = +8m`) to compress the pitch, circulate the ball side-to-side to drag a blocker out, and use **Mid-Range Power Shots (`18m..22m`)** aimed at top corners (`"TL"`/`"TR"`).
   - **`BALANCED_STANDARD`:** Balanced spacing (`15m..35m`).
     - *Counter-Strategy:* Triangle progression through `MID` + weak-side half-space overload via `FWD2`.
4. **Flank Imbalance & Exhausted Defender Targeting (`preferred_flank`):**
   - Computes opponent defensive density and average stamina on the Left (`y < 0`) vs. Right (`y > 0`) half of the pitch.
   - If the opponent's right-side defender is exhausted (`stamina < 0.35`) or pulls centrally, `preferred_flank` shifts automatically to attack that weak side!
5. **Opponent Error Exploitation Triggers:**
   - **Loose-Touch / Bad-Pass Pounce:** When an opponent pass fails (`ball.isFree` after opponent possession), the nearest two players with stamina `> 0.3` immediately trigger `INTERCEPT(aggressive=True)` + counter-attack sprint.
   - **Out-of-Position GK Punish:** Tracks `opp_gk_offset_y` and `opp_gk_depth`. Aims every shot to the exact corner farthest from the GK's current position + velocity vector.

---

## 3. Cross-Match Persistence & Post-Match Coach Review (`agent/coach_review.py`)

### 3.1 Automatic Cross-Match Persistence
- During live play, `FootballTrainer` saves checkpoints to `/tmp/afc_trainer_memory.json` (or `AFC_TRAINER_MEMORY_PATH` env var) whenever:
  - A goal is scored or conceded,
  - Match clock crosses `60s`, `150s`, `240s`, `290s`, or `playMode` transitions to end-of-period.
- When a new match starts (`tick <= 5` or `gameTime < 5.0`), the Trainer loads the historical cross-match baseline (cumulative error priors and learned tactical weights) while resetting intra-match transient counters, and if the same opponent profile is recognized within the first 10 ticks, immediately restores the counter-strategy that beat them previously!

### 3.2 Offline / Post-Match Coach CLI (`python -m agent.coach_review`)
- Can be run on CloudWatch `DECISION` / `TRAINER` logs or trainer memory snapshots after any match:
  ```bash
  python3 -m agent.coach_review --memory /tmp/afc_trainer_memory.json --write-tuning agent/tuning.json
  ```
- Outputs a human-readable **Football Trainer Match Report** detailing:
  1. Our error breakdown (pass turnovers by zone/type, low-xG shots, defensive lapses, goals conceded root causes).
  2. Eagle-Eye Opponent Scouting Report (detected archetype, weak flank, tired/frozen players, GK habits).
  3. Concrete `tuning.json` parameter updates written for the next match.
