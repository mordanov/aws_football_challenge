# 03 - Common Patterns From Found Repositories and Write-ups

Evidence level: **A** = stated in a published write-up with numbers,
**B** = visible in a repo README/structure, **C** = my inference.
I could not read the AWS sample repo's source (GitHub blocked automated
access), so patterns marked A/B come from the READMEs and articles about it.

## Sources
- Strands blog "Inside Agentic Football Cup" (AWS) 
- AWS Builder Center "72 Hours of Agentic Football" (peterpanstechland)
- github.com/aws-samples/sample-ai-possibilities (folder `agentic-football-sample-agents`, plus a `football-strategy` skill)
- github.com/snehasrikanth/Agentic-AI-Football-Cup
- github.com/fl-prasad-alai/flexiloans-agentic-football (browser re-implementation of the protocol)
- github.com/google-research/football, github.com/helios-base/helios-base (non-LLM soccer)

## Pattern catalogue

### P1. One agent per player, thin entry file (A, B)
Each position is a ~80-line `main.py`: system prompt, fallback config,
override config, model id. Shared logic lives in `lib/`. A fix lands once for
all five. *Our version:* one codebase, `MY_PLAYER_ID` selects the role.

### P2. Four-stage pipeline per tick (A)
state summary -> LLM call -> parse -> deterministic override -> log.
Degradation chain: llm -> parse-fallback -> error-fallback -> last-resort
(`SET_STANCE`). *Ours:* same chain, rules first.

### P3. Structured output, strict validation (A)
Pydantic schema for the command; tolerant JSON parser because models
sometimes chat outside the JSON. One teammate saw 34 of 255 parse-fallbacks.

### P4. Position-specific models by latency (A)
Fast small model (Nova Micro) for GK/DEF, stronger (Nova Lite/2 Lite/Pro) for
MID/FWD. Latency is a stat: one team won 2-1 while averaging 1049 ms vs the
opponent's 550 ms, with its DEF spiking to 1333 ms.

### P5. Cap output + reset history each tick (A)
`temperature 0.2`, `max_tokens ~200`, clear conversation every tick.
Reported effect: ~1.5 s -> ~0.7 s decision latency.

### P6. Feed the LLM solved conclusions, not raw numbers (A)
A `tactics.py` computes shot odds, best pass, open space each tick and puts
"LANE CLEAR / BLOCKED" in the prompt. The model maps conclusions to actions.
*Ours:* we go further and compute the candidate list in code.

### P7. Prompt rules are optional; code rules are law (A)
"Prompt alone was ignored ~40% of the time." Evidence: 207 of 370 commands
MOVE_TO and 0 MARK. Fix: ~11 deterministic overrides (blast shot, counter,
no-chase, anchor, tackle, GK smother, cutback, route-one ...).

### P8. Shot discipline matters (A)
92-98% of one team's shots were 45 m+ hoofs that donated possession.
Heatmap showed forward shots from the centre circle. Fix: only shoot near the box.

### P9. Command diversity is the biggest lever (A, Strands blog)
Teams over-using PASS lose to teams using all command types.

### P10. Observability as a first-class feature (A)
One JSON DECISION line per tick: source, command, latency, had-ball, distance
to goal, coordinates, score, override tag. Powers heatmaps, LLM-share alerts,
match reports. Caught a team playing 0% LLM decisions without realising it.

### P11. Automated tune loop (A)
match -> observe KPIs -> adjust `tuning.json` -> redeploy -> record. Losses
force exploration; wins freeze the config. First full run produced their first win.

### P12. Coach channel as a controller (A)
Only 6 preset shouts work. A `LiveCoach` maps situations to shouts
(conceded -> press_high; leading late -> slow_the_tempo; trailing late ->
go_all_out_attack). Orders at goal moments are swallowed; queue them.

### P13. Tactical presets as personality dials (B)
balanced / aggressive / defensive / possession / counter-attack, with sliders
(aggression, pass directness, work rate, shot boldness, discipline).

### P14. AgentCore extras: Memory, Gateway, Observability (A)
Memory: remember the opponent's habits across matches. Gateway: expose a
pass-success analytics service as a tool. Observability: traces per reasoning step.

### P15. Classical RoboCup ideas (C, from helios-base lineage)
Formations anchored to ball position, tree-search over pass chains,
learned evaluation functions for situations. Heavy for a 1 s budget but the
anchor/formation idea transfers directly.

## Cross-cutting lessons
1. Measure first (logs), tune second, add LLM third.
2. The latency budget decides model choice, not the other way round.
3. Fallbacks cut both ways: they keep you alive and hide bugs.
4. Practice bots are weaker/different from real opponents; do not overfit.

## What nobody seems to do (opportunities)
- True cross-player coordination via deterministic shared planning (we do this).
- Opponent modelling from the first minutes of a match.
- Exploiting the 2-second tick geometry (see crazy ideas).
