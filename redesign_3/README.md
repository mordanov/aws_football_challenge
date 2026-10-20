# Redesign 3 — Adaptive Football Trainer & Deep Role-Tactical 5v5 Agent

This directory (`redesign_3/`) contains the complete third-generation redesign of the AWS Agentic Football Cup 5v5 agent, featuring:

1. **All Protocol Blockers Resolved (`docs/01-resolved-blockers-and-protocol.md`):**
   - Verified against the official AWS sample repositories (`aws-samples/sample-ai-possibilities/agentic-football-sample-agents`, `agentic-football-coach`).
   - Fixes critical wire-format bugs in `initial_idea` (`payload["prompt"]` unwrapping, colliding `agentId_0..4` across teams, `ball.isFree` + `possessionAgentId` disambiguation, `MOVE_TO` `target_x`/`target_y`/`sprint` parameters, `SHOOT` `TL`/`TR`/`BL`/`BR`/`CENTER` aim locations, and strict adherence to the 12 official `VALID_COMMANDS`).
2. **Deep Role-Specific Tactics (`docs/02-deep-role-tactics.md` & `agent/roles.py`):**
   - **GK (Role 0):** Angle-bisector arc positioning, incoming shot ray-cast interception, sweeper-keeper loose-ball claims, and adaptive `GK_DISTRIBUTE` (`THROW` vs `KICK` counter-launch).
   - **DEF (Role 1):** Goal-side shadow-cone blocking, penalty-box foul prevention (jockeying inside the box, surgical `SLIDE_TACKLE` outside), cutback `box_protector_lock`, and rest-defense counter-attack screening.
   - **MID (Role 2):** Box-to-box pivot, dynamic triangle support, 3-second Gegenpress trigger, passing-lane `INTERCEPT` screening, and `THROUGH`/`AERIAL` pass orchestration.
   - **FWD1 (Role 3):** Spearhead striker, blind-side shoulder pinning on the opponent's last defender, xG corner-targeted finishing, sweeper-GK 35m punisher, and curved pressing traps.
   - **FWD2 (Role 4):** Anti-clustered weak-side overload partner (`>= 12m` lateral separation from `FWD1`), far-post/penalty-spot cutback poacher, and diagonal half-space dribbler.
3. **"Learn From Every Match" Football Trainer & Eagle-Eye Opponent Scouting (`docs/03-learn-from-every-match-trainer.md` & `agent/trainer.py`):**
   - **Self-Error Ledger (Intra-Match & Cross-Match):** Evaluates the outcome of every command issued on tick `t - 1` against tick `t` (detecting pass interceptions, buildup turnovers, dribble dispossessions, wasted low-xG shots, blocked shots, bypassed tackles, and root causes of conceded goals) and immediately adjusts `TacticalWeights`.
   - **Eagle-Eye Opponent Scouting:** Tracks opponent `lastAction` telemetry, frozen/stalling LLM agents, GK sweeping depth & lateral bias, pressing mob size, flank density, and exhausted defenders (`stamina < 0.36`), classifying the opponent into archetypes (`MOB_PRESS`, `ULTRA_HIGH_LINE`, `LOW_BLOCK`, `FROZEN_LLM`, `BALANCED`) to trigger automatic counter-strategies.
   - **Post-Match Coach CLI (`agent/coach_review.py`):** Generates human-readable coaching reports and updates `agent/tuning.json` across matches.

---

## Directory Layout

```text
redesign_3/
├── README.md
├── STRATEGY.md
├── docs/
│   ├── 01-resolved-blockers-and-protocol.md
│   ├── 02-deep-role-tactics.md
│   ├── 03-learn-from-every-match-trainer.md
│   └── 04-questions-and-concerns.md
├── agent/
│   ├── __init__.py
│   ├── model.py          # Canonical normalized World, Player, Ball, Role, Phase types
│   ├── adapter.py        # Multi-format payload & colliding-agentId parser
│   ├── commands.py       # Official 12 VALID_COMMANDS wire builders
│   ├── tactics.py        # 2s physics projection, xG shot model, pass cones, defensive assignments
│   ├── roles.py          # Deep role engines for GK, DEF, MID, FWD1, FWD2 + set pieces
│   ├── trainer.py        # "Learn From Every Match" Trainer + Eagle-Eye Opponent Scout
│   ├── coach_review.py   # Post-match log/memory analyzer & tuning.json evolver
│   ├── tuning.json       # Persistent tactical weights
│   └── main.py           # <1ms AgentCore entrypoint with never-crash fallback
└── tests/
    └── test_redesign_3.py
```

---

## Quick Start & Verification

```bash
# Run the full Redesign 3 test suite (protocol, role tactics, intra-match learning, cross-match persistence)
PYTHONPATH=redesign_3 python3 -m unittest discover -s redesign_3/tests -v

# Run the Post-Match Football Trainer Coach Review CLI
PYTHONPATH=redesign_3 python3 -m agent.coach_review --memory /tmp/afc_trainer_memory.json
```
