# 04 - Crazy Ideas for Winning

Everything here is **speculative and untested**. Each idea has a risk and a
cheap way to test it. Rules and fair-play: only use ideas that work within the
game's intended interface. Anything that attacks the platform or another
participant's agents is out of bounds and would likely get you disqualified.

## A. Exploit the tick structure

### A1. Tempo trap
Decisions land every ~2 s. A slow team often idles on timeouts. Keep a
compact shape and a designated presser; punish teams whose agents answer
late. *Test:* compare opponent idle share in match reports.

### A2. Latency as a weapon
Rules-only players answer in <1 ms. If opponents average ~1 s per command,
we effectively react more often within each window. *Test:* log per-position
latency for both sides in the "Behind the Scenes" report.

### A3. Anticipate the 2 s horizon
Choose commands that stay good for the whole tick (positions, not
micro-actions). A MOVE_TO to where the ball will be in 2 s beats chasing where it is.
*Test:* use ball velocity to predict, compare interceptions.

## B. Opponent modelling

### B1. First-30-seconds scouting
Track the opponent's average line height and pass direction in the first
ticks, then switch posture (high line -> counter; deep block -> patient
build). Partly implemented in `opp_line`. Warm runtimes only.

### B2. Cross-match memory
AgentCore Memory (or a small store) keeps "this team always shoots left /
leaves the keeper high / presses with two". Next match, pre-position for it.
*Risk:* teams change between matches; stale memory hurts.

### B3. Detect an LLM opponent
LLM teams are noisy: erratic MOVE_TO, late ticks, ignored instructions.
Detect high command variance and lean on tight marking and counters.

## C. Set plays and structure

### C1. Scripted kick-off routine
If the kickoff state is identifiable, run a pre-computed 2-tick combo into
the nearest forward. A free rehearsed attack every restart.

### C2. Keeper as sweeper
Keeper steps up to the edge of the box when the ball is upfield, then
launches a long GK_DISTRIBUTE to a forward for instant counters. *Risk:* lobs over the keeper.

### C3. Wall-pass triangles
Because commands are per player per tick, plan two-player combos: A passes to
B and simultaneously moves into the space; B returns on the next tick.

## D. Game-theory ideas

### D1. Mixed strategy to avoid being read
If opponents model you, randomise among near-equal options (seeded by
tick and player id so all five stay consistent). Our tie-break could use a
seed instead of an LLM.

### D2. Score-aware risk
Leading by 1 with 30 s left: keep the ball in the corner and shield
(low-risk dribble). Trailing: send the keeper up. Maximise win probability,
not goal difference, if the scoring rewards wins.

### D3. Referee/rule loopholes
Read the rules for fouls, offside, out-of-play handling. If tackles are
cheap and the opponent's ball carrier is predictable, a sacrificial presser
may be worth it. *Only if the rules explicitly allow it.*

## E. LLM-centric ideas

### E1. Two-speed brain
Fast rules every tick; a slow "coach" LLM running in the background between
ticks, updating a posture string that the fast layer reads next tick.
Needs shared state (Memory or a warm in-process variable).

### E2. LLM as post-match analyst
After each match, feed the DECISION log plus match report to a strong model
(Claude) and have it propose concrete constant changes. Then a human approves.
The AWS Builder write-up reports a Bedrock "tuning advice" button doing this.

### E3. Self-play at home
Build a tiny local simulator (the flexiloans repo shows the shape) and run
thousands of matches to tune constants with a simple search (random search /
CMA-ES). Big payoff **if** your simulator resembles the real one; otherwise you overfit to your toy.

### E4. Evolve the prompt
Keep a pool of prompts, run them against the practice bots, keep the winners.
Cheap, but practice bots are weak/different; validate on fresh opponents.

## F. Operational edge

### F1. Time zones
Matches autoplay any time and each tournament week ends Sunday 23:59 UTC+8.
Redeploy improvements before the deadline, not after; keep a stable "known good"
version to roll back to.

### F2. Canary deployment
Deploy a candidate to a second team slot if allowed; compare results
before promoting.

### F3. Kill switch
A flag that forces rules-only mode instantly if latency or parse errors spike.

## G. Ideas I would NOT do
- Probing or interfering with the platform or other teams' endpoints.
- Anything that depends on undocumented behaviour that could change mid-event.
- Fully LLM-driven control with no deterministic fallback.

## Ranking by expected value / effort
| Idea | Value | Effort | Do when |
|---|---|---|---|
| Match logs + report loop (P10) | High | Low | Day 1 |
| Exact adapter/commands | High | Low | Day 1 |
| Score-aware risk (D2) | Medium | Low | Week 1 |
| Keeper as sweeper (C2) | Medium | Medium | After baseline wins |
| Opponent scouting (B1) | Medium | Medium | Week 2 |
| Post-match LLM analyst (E2) | Medium | Low | Week 2 |
| Local simulator + search (E3) | High if faithful | High | Only if time allows |
