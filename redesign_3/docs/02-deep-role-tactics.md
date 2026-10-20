# 02 — Deep Role-Specific Tactics (GK, DEF, MID, FWD1, FWD2)

A core weakness of `initial_idea` was treating all outfield players with nearly identical heuristics (a single static anchor offset plus a generic pass/dribble/shoot branch). In 5v5 football with 2-second decision intervals, **spatial role specialization and asynchronous off-ball coordination** decide matches.

In `redesign_3`, every player role (`0=GK`, `1=DEF`, `2=MID`, `3=FWD1`, `4=FWD2`) has a dedicated tactical state machine built on shared physics and geometry primitives (`agent/roles.py` + `agent/tactics.py`).

---

## 1. Physics & Geometry Primitives (The 2-Second Horizon Engine)

Because AgentCore invokes agents every ~2 seconds (or per tick in fast simulation) with a strict `<500ms` timeout:
1. **Ball Trajectory Projection (`predict_ball_pos(world, dt)`):**
   - Projects `ball.pos + ball.vel * dt` with pitch-boundary clamping and damping so players run to **where the ball will be**, not where it was.
2. **Stamina-Adjusted Arrival Time (`time_to_point(player, pt, sprint)`):**
   - Effective speed scales with stamina (`speed_factor = 0.6 + 0.4 * stamina`) and sprint (`1.45x`), plus a turn-angle penalty based on `player.orientation` vs. target vector.
   - Instead of raw Euclidean distance ("who is closest to the ball?"), we compute **who reaches the ball first** (`earliest_arrival_rank`).
3. **Pass Corridor & Interception Shadow (`evaluate_pass_option`):**
   - Evaluates perpendicular distance of every opponent to the segment `carrier -> receiver_lead_point`, scaled by the pass travel distance and opponent sprint reach (`cone_ratio`).
   - Automatically selects `"GROUND"` (clean short/medium lane), `"THROUGH"` (receiver running into open space behind the opponent defensive line), or `"AERIAL"` (bypassing a congested central press).
4. **Expected Goal (`xG`) & Corner-Targeting (`evaluate_shot`):**
   - Computes goal mouth visible angle from `(me.x, me.y)` to posts `(+55, -4.5)` and `(+55, +4.5)`, checks defender shot-block cone, inspects opponent GK position `(gk.x, gk.y)`, and selects the highest-probability corner (`"TL"`, `"TR"`, `"BL"`, `"BR"`) with calibrated `power`.

---

## 2. Role 0: Goalkeeper (`GK`) — Angle-Bisector & Sweeper-Keeper

### Phase A: Shot-Stopping & Angle Narrowing (Out of Possession)
- **Incoming Shot Detection:** If `ball.vel.x < -3.0` (in normalized frame, heading toward `-55.0`) and the ball ray intersects `x = -55.0` within `y in [-7.0, +7.0]`, GK immediately sprints to the exact intercept point on the goal line (`INTERCEPT(aggressive=True)` or `MOVE_TO(..., sprint=True)`).
- **Angle-Bisector Arc Positioning:** When the ball is in play, GK does not stand glued to `(-52, 0)`. Instead, GK positions along the **bisector of the angle from the ball to the two goalposts** `(-55, -4.5)` and `(-55, +4.5)`:
  - When ball is far (`ball.x > 0`), GK steps up to `x = -46.0` (ready to sweep long balls).
  - When ball is in our defensive third (`ball.x < -20`), GK holds `x = -52.5` to `-50.5`, shifting `y` proportionally (`y = clamp(ball.y * 0.38, -4.2, +4.2)`) to cut the near-post angle.
- **Sweeper-Keeper Rush:** If the ball is loose (`isFree=True`) in our penalty area (`ball.x < -36, |ball.y| < 16`) and GK can reach it before any opponent (`time_to_ball(GK) < min_opp_time`), GK sprints out to claim/clear it.

### Phase B: Smart Distribution (In Possession)
- Never dribbles into opponents in front of our goal.
- **Counter-Attack Launch (`KICK`):** If `FWD1` or `FWD2` is in space near midfield (`x > -10`) and opponent has committed 3+ players into our half (detected via Eagle-Eye Scouting), GK uses `GK_DISTRIBUTE(target_player_id=fwd, method="KICK")`.
- **Press-Safe Outlet (`THROW`):** Otherwise distributes via `GK_DISTRIBUTE(target_player_id=best_open_teammate, method="THROW")` to the widest unmarked `DEF` or `MID` (`|y| > 10`) to avoid central turnovers in front of goal.

---

## 3. Role 1: Defender (`DEF`) — Shadow-Cone Stopper & Back-Line Marshal

### Phase A: Defensive Line & Danger Priority (Out of Possession)
- **Never Blindly Chases to the Corner:** `initial_idea` allowed DEF to get dragged out of position. In `redesign_3`, DEF protects the **Danger Zone (`x in [-50, -25], |y| < 14`)**.
- **Role-Coordinated Pressing:**
  - DEF only presses the ball carrier if:
    1. DEF is the closest outfield defender **AND** the ball carrier is inside our defensive half (`ball.x < -5`), OR
    2. It is a `1v1` breakaway where no other teammate is goal-side of the carrier.
  - Otherwise, `MID` presses the ball while `DEF` drops into the **Goal-Side Shadow Cone** or marks the most dangerous central striker!
- **Goal-Side Shadow-Cone Blocking:**
  - Positions directly on the line connecting `ball` to `(-55, 0)`, `2.5m..4.5m` off the attacker toward our own goal, physically blocking both the shot lane and the through-pass lane.
- **Surgical `SLIDE_TACKLE` Rules (Foul/Penalty Prevention):**
  - Official rules state fouls inside the penalty box award a **Penalty Kick**, and reckless slide tackles risk yellow/red cards.
  - Therefore, DEF **never** uses `SLIDE_TACKLE` inside our penalty box (`x < -38.5, |y| < 16.5`) unless the opponent has an open shot within `12m` of goal and no GK cover. Outside the box, `SLIDE_TACKLE` is triggered only when `dist <= 2.3m` and approaching from the front/side.

### Phase B: Buildup Anchor & Rest-Defense (In Possession)
- **When Carrying the Ball:** Immediately looks for a progressive `GROUND` pass to `MID` or diagonal `AERIAL`/`GROUND` pass to the weak-side forward (`FWD1`/`FWD2`). If pressed within `5m` with no safe short lane, plays a long `AERIAL` pass into the attacking channel rather than risking a dribble turnover as the last defender.
- **Off-Ball Rest-Defense:** Holds a supportive pivot angle `12m..16m` behind the ball (`x` capped at `+5m` modulated by Trainer `defensive_line_offset`), shadowing the opponent's highest counter-attacking forward (`MARK(tightness="LOOSE")` or positioning `2.5m` goal-side of their cherry-picker).

---

## 4. Role 2: Midfielder (`MID`) — Box-to-Box Engine, Regista & Gegenpresser

### Phase A: The Playmaking Pivot (In Possession)
- **Triangle Creation (Off-Ball):**
  - Continuously computes the midpoint between the ball carrier and the primary forward target, then offsets laterally by `8m..12m` into open space so the carrier **always** has a clean `GROUND` pass escape valve.
- **On-Ball Decision Hierarchy:**
  1. **Killer Through-Ball (`PASS` type `"THROUGH"`):** Scans `FWD1` and `FWD2`. If either is within `3m` of the opponent's last defender and has an open corridor toward goal, fires a `"THROUGH"` pass immediately.
  2. **Flank Switch (`PASS` type `"AERIAL"` or `"GROUND"`):** Uses the Trainer's `preferred_flank` and Eagle-Eye opponent congestion map. If 2+ opponents are on the ball side (`sign(opp.y) == sign(ball.y)`), switches play to the weak-side forward.
  3. **Progressive Carry (`MOVE_TO`):** Advances into the open half-space (`y = ±10`) until an opponent steps up within `5.5m`, drawing the defender out of the back line before releasing the pass.
  4. **Late Box Arrival (`SHOOT`):** If within `22m` of goal with a clear shooting lane, strikes at the far corner (`power=0.82`).

### Phase B: Gegenpress & Central Screen (Out of Possession)
- **3-Second Gegenpress Trigger:** If possession was just lost (within 2 ticks) and `MID` is within `13m` of the ball with `stamina > 0.35`, `MID` triggers immediate high-intensity `PRESS_BALL(intensity=0.85)` to win the ball back before the opponent can transition.
- **Passing Lane Interception:** When `DEF` is engaging the ball carrier, `MID` drops into the passing lane between the ball carrier and the closest central opponent receiver (`INTERCEPT(aggressive=True)` or cutting the lane vector).
- **Stamina Pacing:** Because `MID` covers the most ground, sprint is disabled on routine repositioning when `stamina < 0.48`, preserving burst speed for transitions.

---

## 5. Role 3: Forward 1 (`FWD1`) — Spearhead Striker, Blind-Side Runner & Press Leader

### Phase A: Clinical Finishing & Line-Breaking Runs (In Possession)
- **Dynamic Finishing Matrix (`SHOOT`):**
  - Inside `18m`: One-touch finish if any opponent is within `3.5m` (preventing tackle dispossession before shot release).
  - Corner Selection: Compares opponent GK `y` against goal center. Aims `"TL"`/`"BL"` if GK is shifted negative-`y`, `"TR"`/`"BR"` if GK is shifted positive-`y`.
  - **Sweeper-Keeper Punisher:** If Eagle-Eye Scouting detects opponent GK is `> 12m` off their goal line (`opp_gk.x < 43.0`), `FWD1` (and `MID`/`FWD2`) unlocks long-range shots from up to `34m` (`aim_location="CENTER"` or open corner, `power=0.95`)!
- **Cutback Option Inside the Box:** If inside the box (`x > 38`) at a tight angle (`|y| > 11`) with a defender blocking the near post and `FWD2` or `MID` arriving centrally (`|teammate.y| < 8`), `FWD1` executes a high-xG **cutback pass** (`PASS(type="GROUND")`) for an easy tap-in!
- **Off-Ball Shoulder Pinning:**
  - Identifies the opponent's deepest outfield defender (`last_def_x`).
  - Positions `1.5m` onside/ahead of `last_def_x` in the left/central-left channel (`y in [-14, -3]`, or swaps dynamically if `preferred_flank > 0`), sprinting diagonally behind the defense the instant `MID` or `DEF` wins possession.

### Phase B: Pressing Trap Leader (Out of Possession)
- When opponent `GK` or `DEF` has the ball in buildup (`ball.x > 15`), `FWD1` curves their pressing run to **force the carrier toward the touchline** while cutting off the central pass to the opponent midfielder.
- When opponent has established possession in our half (`ball.x < -5`), `FWD1` does **not** waste stamina chasing into our own penalty box; instead, `FWD1` holds near `(0, -8)` as the **Counter-Attack Spearhead**, forcing at least one opponent defender to stay back.

---

## 6. Role 4: Forward 2 (`FWD2`) — Shadow Creator, Weak-Side Overload & Far-Post Poacher

### Phase A: Asymmetric Partnership with `FWD1` (In Possession)
- **Anti-Clustering Guarantee:** `initial_idea` often had `FWD1` and `FWD2` collapse onto the same ball or run into the same lane. In `redesign_3`, `FWD2` is explicitly coupled in **anti-phase** with `FWD1`:
  - Minimum lateral separation of `12m` in `y` and staggered depth (`Δx = 6m..10m`).
  - **When Ball is on Left Flank (`ball.y < -5`):** `FWD1` attacks the near post `(44, -5)`, while `FWD2` sprints to the **far-post / penalty-spot cutback zone** `(41, +6)`.
  - **When Ball is on Right Flank (`ball.y > +5`):** `FWD2` stretches wide `(36, +18)` to receive the switch or drive the byline, while `FWD1` pins the center-back at `(45, 0)`.
- **On-Ball Half-Space Dribble & Cutback:**
  - When `FWD2` receives the ball wide (`|y| > 12`), `FWD2` drives diagonally at `45°` toward the edge of the penalty box (`MOVE_TO(target_x=42, target_y=±8, sprint=True)`), pulling the lone defender away from `FWD1`, then either shoots far-post or slips a `THROUGH`/`GROUND` pass to `FWD1`.

### Phase B: High-Turnover Hunter & Half-Space Cover (Out of Possession)
- Closes down the opponent's weak-side outlet or secondary buildup player (`MARK(tightness="TIGHT")` during opponent goal kicks/throw-ins in their half).
- Drops to the right half-space (`x = -8, y = +10`) when defending deep, forming a compact 1-2-1 diamond with `DEF`, `MID`, and `FWD1` that funnels opponents into the crowded center where `DEF` + `MID` win the tackle and immediately launch the counter.
