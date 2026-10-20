# Strategy Overview — Redesign 3 ("The Adaptive Football Trainer")

## Core Philosophy

1. **Zero-Latency Deterministic Execution (`<1ms` per tick):**
   - Never block the 500ms AgentCore tick budget on synchronous LLM calls.
   - Physics-aware 2-second horizon geometry (`agent/tactics.py`) and role-specialized state machines (`agent/roles.py`) execute in `<1ms`, guaranteeing 0% timeouts.
2. **Learn From Every Tick & Every Match (`agent/trainer.py`):**
   - Every decision is an experiment: on tick `t`, the `FootballTrainer` inspects what happened to the command issued on tick `t - 1`.
   - When our pass is intercepted, our dribble is dispossessed, our shot is blocked, or we concede a goal, the Trainer diagnoses the root cause and immediately adapts `TacticalWeights` (and persists those lessons across matches).
3. **Eagle-Eye Opponent Scouting (`OpponentScout`):**
   - Continuously monitors opponent positions, velocities, stamina, GK depth, and `lastAction` strings to classify their strategy (`MOB_PRESS`, `ULTRA_HIGH_LINE`, `LOW_BLOCK`, `FROZEN_LLM`, `BALANCED`) and attack their weakest flank and most exhausted or frozen defender.
4. **Deep Role Specialization (`agent/roles.py`):**
   - Every player (`GK`, `DEF`, `MID`, `FWD1`, `FWD2`) has a distinct tactical identity in possession, out of possession, in transition, and on set pieces.
